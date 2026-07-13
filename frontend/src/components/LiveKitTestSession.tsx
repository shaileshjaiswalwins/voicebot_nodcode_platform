import React, { useEffect, useState } from 'react';
import { Check, MessageSquareText, Mic, MicOff, SendHorizontal, Square } from 'lucide-react';
import {
  BarVisualizer,
  DisconnectButton,
  LiveKitRoom,
  RoomAudioRenderer,
  TrackToggle,
  useLocalParticipant,
  useRoomContext,
  useVoiceAssistant
} from '@livekit/components-react';
import { Track } from 'livekit-client';
import { FEEDBACK_TIMEOUT_MS } from '../constants/ui';
import { deriveAgentState } from '../utils/config';
import { titleCase } from '../utils/formatting';

/** The part of the session UI that needs LiveKit's room context (mic toggle, chat send,
 * disconnect, visualizer) — must render inside <LiveKitRoom>, which is what supplies that
 * context to useRoomContext/useLocalParticipant/useVoiceAssistant below. */
function LiveSessionControls({
  status,
  chatMessage,
  setChatMessage,
  onDisconnect,
  onMicChange,
  onAudioReady
}: {
  status: string;
  chatMessage: string;
  setChatMessage: (value: string) => void;
  onDisconnect: () => void;
  onMicChange: (enabled: boolean) => void;
  onAudioReady: (ready: boolean) => void;
}) {
  const room = useRoomContext();
  const { isMicrophoneEnabled } = useLocalParticipant();
  const { state: agentState, audioTrack } = useVoiceAssistant();
  const [justSent, setJustSent] = useState(false);

  useEffect(() => { onMicChange(isMicrophoneEnabled); }, [isMicrophoneEnabled, onMicChange]);
  useEffect(() => { onAudioReady(Boolean(audioTrack)); }, [audioTrack, onAudioReady]);

  const uiState = deriveAgentState(status, Boolean(audioTrack), true);

  function submitChat(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = chatMessage.trim();
    if (!trimmed) return;
    // TODO: wire to a real backend chat contract once one exists — this is the same
    // best-effort data-channel publish the pre-LiveKitRoom implementation used.
    room.localParticipant
      .publishData(new TextEncoder().encode(JSON.stringify({ type: 'chat', text: trimmed })))
      .catch(() => { /* best-effort only until backend contract exists */ });
    setChatMessage('');
    setJustSent(true);
    setTimeout(() => setJustSent(false), FEEDBACK_TIMEOUT_MS);
  }

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
        <form className="agent-chat-input" onSubmit={submitChat}>
          <MessageSquareText size={15} />
          <input
            value={chatMessage}
            onChange={(event) => setChatMessage(event.target.value)}
            placeholder="Send chat message"
          />
          <button className="control-button send" type="submit" disabled={!chatMessage.trim()} title={justSent ? 'Sent' : 'Send'}>
            {justSent ? <Check size={15} /> : <SendHorizontal size={15} />}
          </button>
        </form>
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
  chatMessage,
  setChatMessage,
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
  chatMessage: string;
  setChatMessage: (value: string) => void;
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
        status={status}
        chatMessage={chatMessage}
        setChatMessage={setChatMessage}
        onDisconnect={onDisconnectRequested}
        onMicChange={onMicChange}
        onAudioReady={onAudioReady}
      />
    </LiveKitRoom>
  );
}
