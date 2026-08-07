import React, { useEffect, useRef } from 'react';
import { Mic, MicOff, Square } from 'lucide-react';
import {
  BarVisualizer,
  DisconnectButton,
  LiveKitRoom,
  RoomAudioRenderer,
  TrackToggle,
  useLocalParticipant,
  useVoiceAssistant
} from '@livekit/components-react';
import { Track } from 'livekit-client';
import { deriveAgentState } from '../utils/config';
import { titleCase } from '../utils/formatting';
import { api } from '../api';

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

/** The part of the session UI that needs LiveKit's room context (mic toggle,
 * disconnect, visualizer) — must render inside <LiveKitRoom>, which is what supplies that
 * context to useLocalParticipant/useVoiceAssistant below. */
function LiveSessionControls({
  roomName,
  status,
  onDisconnect,
  onMicChange,
  onAudioReady
}: {
  roomName: string;
  status: string;
  onDisconnect: () => void;
  onMicChange: (enabled: boolean) => void;
  onAudioReady: (ready: boolean) => void;
}) {
  const { isMicrophoneEnabled, localParticipant } = useLocalParticipant();
  const { state: agentState, audioTrack } = useVoiceAssistant();

  useEffect(() => { onMicChange(isMicrophoneEnabled); }, [isMicrophoneEnabled, onMicChange]);
  useEffect(() => { onAudioReady(Boolean(audioTrack)); }, [audioTrack, onAudioReady]);

  const micMediaTrack = localParticipant.getTrackPublication(Track.Source.Microphone)?.track?.mediaStreamTrack;
  const botMediaTrack = audioTrack?.publication?.track?.mediaStreamTrack;
  useTestCallRecorder(roomName, micMediaTrack, botMediaTrack);

  const uiState = deriveAgentState(status, Boolean(audioTrack), true);

  return (
    <>
      <div aria-label={`Agent is ${uiState}`}>
        <BarVisualizer state={agentState} track={audioTrack} barCount={24} options={{ minHeight: 8, maxHeight: 100 }} />
      </div>
      <p className="session-caption">{titleCase(uiState)} · {status}</p>
      <RoomAudioRenderer />
      <div className="agent-control-bar" aria-label="Agent session controls">
        <TrackToggle
          source={Track.Source.Microphone}
          className={isMicrophoneEnabled ? 'control-button active' : 'control-button'}
        >
          {isMicrophoneEnabled ? <><Mic size={15} /> Mute</> : <><MicOff size={15} /> Unmute</>}
        </TrackToggle>
        <DisconnectButton className="control-button danger" onClick={onDisconnect}>
          <Square size={15} /> End
        </DisconnectButton>
      </div>
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
  onConnected,
  onDisconnected,
  onError,
  onDisconnectRequested,
  onMicChange,
  onAudioReady
}: {
  serverUrl: string;
  token: string;
  connected: boolean;
  status: string;
  roomName: string;
  onConnected: () => void;
  onDisconnected: () => void;
  onError: (err: Error) => void;
  onDisconnectRequested: () => void;
  onMicChange: (enabled: boolean) => void;
  onAudioReady: (ready: boolean) => void;
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
        onDisconnect={onDisconnectRequested}
        onMicChange={onMicChange}
        onAudioReady={onAudioReady}
      />
    </LiveKitRoom>
  );
}
