import React, { useRef, useState } from 'react';
import { Pause, Play, Volume2, VolumeX } from 'lucide-react';
import { useWavesurfer } from '@wavesurfer/react';

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

/** Waveform playback UI for a call recording, built on wavesurfer.js (@wavesurfer/react).
 * `src` is a blob: URL for a file that's already fully in memory — wavesurfer decodes it
 * once and renders the real waveform, so playback position is always visually anchored
 * to actual audio content instead of an abstract progress bar. Played/unplayed is a color
 * split on the same waveform (progressColor over waveColor) — there's no separate
 * "buffering" indicator to be confused with playback position. */
export function AudioPlayer({ src }: { src: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [muted, setMuted] = useState(false);

  const { wavesurfer, isPlaying, isReady, currentTime } = useWavesurfer({
    container: containerRef,
    url: src,
    height: 40,
    waveColor: 'var(--border)',
    progressColor: 'var(--primary)',
    cursorColor: 'var(--primary)',
    cursorWidth: 2,
    barWidth: 2,
    barGap: 2,
    barRadius: 2,
    normalize: true,
    fillParent: true,
    interact: true,
  });

  const duration = wavesurfer?.getDuration() || 0;

  function togglePlay() {
    wavesurfer?.playPause();
  }

  function toggleMute() {
    if (!wavesurfer) return;
    const next = !muted;
    wavesurfer.setMuted(next);
    setMuted(next);
  }

  return (
    <div className="audio-player" aria-label="Call recording player">
      <button
        type="button"
        className="audio-player-toggle"
        onClick={togglePlay}
        disabled={!isReady}
        aria-label={isPlaying ? 'Pause recording' : 'Play recording'}
      >
        {isPlaying ? <Pause size={16} /> : <Play size={16} />}
      </button>
      <span className="audio-player-time">{isReady ? formatTime(currentTime) : '--:--'}</span>
      <div className="audio-player-waveform">
        <div ref={containerRef} className="audio-player-waveform-canvas" />
        {!isReady && <div className="audio-player-track-loading" />}
      </div>
      <span className="audio-player-time">{isReady ? formatTime(duration) : '--:--'}</span>
      <button
        type="button"
        className="audio-player-mute"
        onClick={toggleMute}
        aria-label={muted ? 'Unmute' : 'Mute'}
      >
        {muted ? <VolumeX size={15} /> : <Volume2 size={15} />}
      </button>
    </div>
  );
}
