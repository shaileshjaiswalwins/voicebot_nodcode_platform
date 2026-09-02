import React, { useEffect, useRef, useState } from 'react';
import { Bot, Mic, MicOff, Square, User } from 'lucide-react';
import {
  DisconnectButton,
  LiveKitRoom,
  RoomAudioRenderer,
  TrackToggle,
  useLocalParticipant,
  useRoomContext,
  useTrackVolume,
  useVoiceAssistant,
  type AgentState
} from '@livekit/components-react';
import { RoomEvent, Track, type TranscriptionSegment } from 'livekit-client';
import { deriveAgentState } from '../utils/config';
import { api } from '../api';

interface LiveLine {
  id: string;
  role: 'user' | 'bot';
  text: string;
  final: boolean;
  time: number;
}

/** Live "who said what" feed for an in-progress call. Subscribes to LiveKit's
 * TranscriptionReceived event (populated by the agent's STT for the caller's speech
 * and by its TTS/LLM text for the bot's side) and renders it with the same
 * chat-bubble markup TestLLMPanel's ChatTranscript uses, so it looks consistent. */
function LiveTranscript({ botName }: { botName?: string }) {
  const room = useRoomContext();
  const [lines, setLines] = useState<Record<string, LiveLine>>({});
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (segments: TranscriptionSegment[], participant?: { isLocal?: boolean }) => {
      const role: 'user' | 'bot' = participant?.isLocal ? 'user' : 'bot';
      setLines((prev) => {
        const next = { ...prev };
        for (const seg of segments) {
          // Keep the original first-seen time across partial→final updates of the
          // same segment id, so a later-finalized line doesn't jump ahead in order.
          const time = prev[seg.id]?.time ?? seg.firstReceivedTime ?? Date.now();
          next[seg.id] = { id: seg.id, role, text: seg.text, final: seg.final, time };
        }
        return next;
      });
    };
    room.on(RoomEvent.TranscriptionReceived, handler);
    return () => { room.off(RoomEvent.TranscriptionReceived, handler); };
  }, [room]);

  const byTime = Object.values(lines).sort((a, b) => a.time - b.time);
  // livekit-agents always flushes a bot segment with final=true on barge-in, even
  // when TTS was cut off mid-sentence (room_io/_output.py's transcription flush()
  // has no interruption awareness — that's a framework gap, not something bot.py
  // can suppress). When the bot then replays the same answer in full, the replay's
  // text always starts with the truncated fragment's text verbatim — so drop any
  // final bot line that's an exact text-prefix of a later bot line.
  const superseded = new Set<string>();
  for (let i = 0; i < byTime.length; i++) {
    const earlier = byTime[i];
    if (earlier.role !== 'bot' || !earlier.final || earlier.text.trim().length < 3) continue;
    for (let j = i + 1; j < byTime.length; j++) {
      const later = byTime[j];
      if (later.role !== 'bot') continue;
      if (later.text.length > earlier.text.length && later.text.startsWith(earlier.text)) {
        superseded.add(earlier.id);
        break;
      }
    }
  }
  const ordered = byTime.filter((l) => !superseded.has(l.id));
  // Re-runs on every text change too (not just new lines) — a growing partial
  // segment needs to keep pulling the view down, not just a brand new line arriving.
  const contentSignature = ordered.map((l) => `${l.id}:${l.text.length}`).join('|');
  useEffect(() => {
    const el = containerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [contentSignature]);

  if (ordered.length === 0) return null;

  return (
    <div ref={containerRef} className="chat-transcript live-call-transcript" aria-label="Live call transcript">
      {ordered.map((line) => (
        <div key={line.id} className={line.role === 'user' ? 'chat-turn user' : 'chat-turn'}>
          <div className="chat-avatar">{line.role === 'user' ? <User size={14} /> : <Bot size={14} />}</div>
          <div className="chat-bubble">
            {line.role === 'bot' && botName && <div className="chat-meta"><strong>{botName}</strong></div>}
            <p className={line.final ? undefined : 'muted'}>{line.text}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

/** Mixes the local mic track and the bot's remote audio track into one MediaRecorder
 * (mirrors the local-recording feature that shipped in the pre-refactor main.tsx and
 * got dropped when that file was split up) and uploads the result on disconnect so
 * dashboard Test Calls have a playable recording. Only active for "test-" rooms —
 * production/dialer calls have their own recording pipeline. */
function useTestCallRecorder(roomName: string, micTrack?: MediaStreamTrack, botTrack?: MediaStreamTrack) {
  const audioContextRef = useRef<AudioContext | null>(null);
  const destinationRef = useRef<MediaStreamAudioDestinationNode | null>(null);
  const sourceNodesRef = useRef<MediaStreamAudioSourceNode[]>([]);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const attachedTracksRef = useRef<Set<MediaStreamTrack>>(new Set());

  function addTrack(track: MediaStreamTrack) {
    const audioContext = audioContextRef.current;
    const destination = destinationRef.current;
    if (!audioContext || !destination || attachedTracksRef.current.has(track)) return;
    try {
      const source = audioContext.createMediaStreamSource(new MediaStream([track]));
      source.connect(destination);
      sourceNodesRef.current.push(source);
      attachedTracksRef.current.add(track);
    } catch {
      // Best-effort — recording continues with whichever tracks mixed in successfully.
    }
  }

  useEffect(() => {
    if (!roomName.startsWith('test-') || !micTrack || !window.MediaRecorder) return;
    if (recorderRef.current) return; // already recording this call

    const audioContext = new AudioContext();
    const destination = audioContext.createMediaStreamDestination();
    audioContextRef.current = audioContext;
    destinationRef.current = destination;
    addTrack(micTrack);

    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
    const recorder = new MediaRecorder(destination.stream, { mimeType });
    chunksRef.current = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorderRef.current = recorder;
    recorder.start(1000);

    return () => {
      const rec = recorderRef.current;
      recorderRef.current = null;
      if (!rec) return;
      const chunks = chunksRef.current;
      chunksRef.current = [];
      const finish = () => {
        for (const source of sourceNodesRef.current) {
          try { source.disconnect(); } catch { /* best-effort cleanup */ }
        }
        sourceNodesRef.current = [];
        attachedTracksRef.current = new Set();
        audioContextRef.current?.close().catch(() => undefined);
        audioContextRef.current = null;
        if (chunks.length) {
          const blob = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
          api.uploadTestRecording(roomName, blob).catch(() => undefined);
        }
      };
      if (rec.state !== 'inactive') {
        rec.onstop = finish;
        rec.stop();
      } else {
        finish();
      }
    };
    // Re-runs only when the room changes or the mic track first becomes available —
    // botTrack is attached opportunistically below without restarting the recorder.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomName, micTrack]);

  useEffect(() => {
    if (botTrack) addTrack(botTrack);
  }, [botTrack]);
}

/** Pulsing orb that stands in for the bar visualizer — same purpose (glanceable
 * listening/thinking/speaking indicator) but as a single reactive shape. Ring size
 * responds to the bot's actual output volume via useTrackVolume; color/animation
 * speed switch on LiveKit's own AgentState so it stays in sync with the caption text. */
function VoiceOrb({ agentState, audioTrack }: { agentState: AgentState; audioTrack: Parameters<typeof useTrackVolume>[0] }) {
  const volume = useTrackVolume(audioTrack);
  const scale = 1 + Math.min(volume, 1) * 0.35;

  return (
    <div className={`voice-orb voice-orb-${agentState}`} aria-hidden="true">
      <div className="voice-orb-ring" style={{ transform: `scale(${scale})` }} />
      <div className="voice-orb-core" />
    </div>
  );
}

/** The part of the session UI that needs LiveKit's room context (mic toggle,
 * disconnect, visualizer) — must render inside <LiveKitRoom>, which is what supplies that
 * context to useLocalParticipant/useVoiceAssistant below. */
function LiveSessionControls({
  roomName,
  status,
  botName,
  onDisconnect,
  onMicChange,
  onAudioReady,
  onAgentStateChange
}: {
  roomName: string;
  status: string;
  botName?: string;
  onDisconnect: () => void;
  onMicChange: (enabled: boolean) => void;
  onAudioReady: (ready: boolean) => void;
  onAgentStateChange?: (state: AgentState) => void;
}) {
  const { isMicrophoneEnabled, localParticipant } = useLocalParticipant();
  const { state: agentState, audioTrack } = useVoiceAssistant();

  useEffect(() => { onMicChange(isMicrophoneEnabled); }, [isMicrophoneEnabled, onMicChange]);
  useEffect(() => { onAudioReady(Boolean(audioTrack)); }, [audioTrack, onAudioReady]);
  // Report LiveKit's own AgentState up so the parent's caption text can use the
  // real signal instead of the heuristic deriveAgentState — those two disagreeing
  // is what caused the caption to say "Speaking" while the orb showed "thinking".
  useEffect(() => { onAgentStateChange?.(agentState); }, [agentState, onAgentStateChange]);

  const micMediaTrack = localParticipant.getTrackPublication(Track.Source.Microphone)?.track?.mediaStreamTrack;
  const botMediaTrack = audioTrack?.publication?.track?.mediaStreamTrack;
  useTestCallRecorder(roomName, micMediaTrack, botMediaTrack);

  const uiState = deriveAgentState(status, Boolean(audioTrack), true);

  return (
    <>
      <div aria-label={`Agent is ${uiState}`}>
        <VoiceOrb agentState={agentState} audioTrack={audioTrack} />
      </div>
      <RoomAudioRenderer />
      <div className="agent-control-bar" aria-label="Agent session controls">
        <TrackToggle
          source={Track.Source.Microphone}
          showIcon={false}
          className={isMicrophoneEnabled ? 'control-button active' : 'control-button'}
        >
          {isMicrophoneEnabled ? <><Mic size={15} /> Mute</> : <><MicOff size={15} /> Unmute</>}
        </TrackToggle>
        <DisconnectButton className="control-button danger" onClick={onDisconnect}>
          <Square size={15} /> End
        </DisconnectButton>
      </div>
      <LiveTranscript botName={botName} />
    </>
  );
}

/** Owns the actual LiveKit room connection (via <LiveKitRoom connect audio>) so mic
 * publish/subscribe/disconnect are handled by LiveKit's React context instead of a
 * hand-rolled `new Room()` + imperative RoomEvent listeners. */
export function LiveKitTestSession({
  serverUrl,
  token,
  connected,
  status,
  roomName,
  botName,
  onConnected,
  onDisconnected,
  onError,
  onDisconnectRequested,
  onMicChange,
  onAudioReady,
  onAgentStateChange
}: {
  serverUrl: string;
  token: string;
  connected: boolean;
  status: string;
  roomName: string;
  botName?: string;
  onConnected: () => void;
  onDisconnected: () => void;
  onError: (err: Error) => void;
  onDisconnectRequested: () => void;
  onMicChange: (enabled: boolean) => void;
  onAudioReady: (ready: boolean) => void;
  onAgentStateChange?: (state: AgentState) => void;
}) {
  if (!connected) {
    return <div aria-label="Agent is idle" className="bar-visualizer-placeholder" />;
  }
  return (
    <LiveKitRoom
      serverUrl={serverUrl}
      token={token}
      connect={connected}
      audio
      onConnected={onConnected}
      onDisconnected={onDisconnected}
      onError={onError}
    >
      <LiveSessionControls
        roomName={roomName}
        status={status}
        botName={botName}
        onDisconnect={onDisconnectRequested}
        onMicChange={onMicChange}
        onAudioReady={onAudioReady}
        onAgentStateChange={onAgentStateChange}
      />
    </LiveKitRoom>
  );
}
