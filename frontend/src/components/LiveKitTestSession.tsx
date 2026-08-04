import React, { useEffect } from 'react';
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

/** The part of the session UI that needs LiveKit's room context (mic toggle,
 * disconnect, visualizer) — must render inside <LiveKitRoom>, which is what supplies that
 * context to useLocalParticipant/useVoiceAssistant below. */
function LiveSessionControls({
  status,
  onDisconnect,
  onMicChange,
  onAudioReady
}: {
  status: string;
  onDisconnect: () => void;
  onMicChange: (enabled: boolean) => void;
  onAudioReady: (ready: boolean) => void;
}) {
  const { isMicrophoneEnabled } = useLocalParticipant();
  const { state: agentState, audioTrack } = useVoiceAssistant();

  useEffect(() => { onMicChange(isMicrophoneEnabled); }, [isMicrophoneEnabled, onMicChange]);
  useEffect(() => { onAudioReady(Boolean(audioTrack)); }, [audioTrack, onAudioReady]);

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
        onDisconnect={onDisconnectRequested}
        onMicChange={onMicChange}
        onAudioReady={onAudioReady}
      />
    </LiveKitRoom>
  );
}
