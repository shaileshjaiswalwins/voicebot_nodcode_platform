/** Per-provider tunable parameter specs, mirroring provider_params.py on the backend.
 * Drives the ProviderOptionsEditor so a PM can play with every relevant Sarvam STT /
 * Sarvam TTS / Gemini knob. Only params the user actually changes are written into the
 * corresponding *_options dict; everything else falls back to the pipeline defaults. */

export type ParamKind = 'number' | 'text' | 'bool' | 'select';

export type ParamField = {
  key: string;
  label: string;
  kind: ParamKind;
  hint?: string;
  placeholder?: string;   // shown as the default/current pipeline value
  min?: number;
  max?: number;
  step?: number;
  options?: string[];     // for kind='select'
  group?: string;         // optional visual grouping
};

// ── Sarvam STT ────────────────────────────────────────────────────────────────
export const SARVAM_STT_FIELDS: ParamField[] = [
  { key: 'mode', label: 'Mode', kind: 'select', options: ['transcribe', 'translate', 'verbatim', 'translit', 'codemix'], placeholder: 'transcribe', hint: 'Only saaras:v3 supports non-transcribe modes.' },
  { key: 'sample_rate', label: 'Input sample rate (Hz)', kind: 'number', placeholder: '16000', min: 8000, step: 1000 },
  { key: 'high_vad_sensitivity', label: 'High VAD sensitivity', kind: 'bool', hint: 'Detect softer/shorter utterances.' },
  { key: 'input_audio_codec', label: 'Input audio codec', kind: 'text', placeholder: 'audio/wav' },
  // Fine-grained VAD (saaras:v3 only)
  { key: 'positive_speech_threshold', label: 'Positive speech threshold', kind: 'number', min: 0, max: 1, step: 0.05, group: 'Fine-grained VAD (saaras:v3)' },
  { key: 'negative_speech_threshold', label: 'Negative speech threshold', kind: 'number', min: 0, max: 1, step: 0.05, group: 'Fine-grained VAD (saaras:v3)' },
  { key: 'min_speech_frames', label: 'Min speech frames', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)' },
  { key: 'first_turn_min_speech_frames', label: 'First-turn min speech frames', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)' },
  { key: 'negative_frames_count', label: 'Negative frames count', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)' },
  { key: 'negative_frames_window', label: 'Negative frames window', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)' },
  { key: 'start_speech_volume_threshold', label: 'Start speech volume (dB)', kind: 'number', step: 1, group: 'Fine-grained VAD (saaras:v3)' },
  { key: 'interrupt_min_speech_frames', label: 'Interrupt min speech frames', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)' },
  { key: 'pre_speech_pad_frames', label: 'Pre-speech pad frames', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)' },
  { key: 'num_initial_ignored_frames', label: 'Initial ignored frames', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)' },
];

// ── Sarvam TTS ────────────────────────────────────────────────────────────────
export const SARVAM_TTS_FIELDS: ParamField[] = [
  { key: 'pace', label: 'Pace', kind: 'number', min: 0.3, max: 3.0, step: 0.05, placeholder: '1.0', hint: 'Speech rate multiplier (0.3–3.0).' },
  { key: 'temperature', label: 'Temperature', kind: 'number', min: 0.01, max: 2.0, step: 0.05, placeholder: '0.75', hint: 'bulbul:v3 only (0.01–2.0).' },
  { key: 'pitch', label: 'Pitch', kind: 'number', min: -0.75, max: 0.75, step: 0.05, placeholder: '0.0', hint: 'Range -0.75–0.75.' },
  { key: 'loudness', label: 'Loudness', kind: 'number', min: 0.5, max: 2.0, step: 0.05, placeholder: '1.0', hint: 'Volume multiplier (0.5–2.0).' },
  { key: 'speech_sample_rate', label: 'Output sample rate (Hz)', kind: 'select', options: ['8000', '16000', '22050', '24000', '32000', '44100', '48000'], placeholder: '24000' },
  { key: 'output_audio_codec', label: 'Output codec', kind: 'select', options: ['linear16', 'mp3', 'wav', 'aac', 'opus', 'flac', 'mulaw', 'alaw'], placeholder: 'linear16' },
  { key: 'output_audio_bitrate', label: 'Output bitrate', kind: 'select', options: ['32k', '64k', '96k', '128k', '192k'], placeholder: '128k' },
  { key: 'min_buffer_size', label: 'Min buffer size', kind: 'number', min: 30, max: 200, step: 5, placeholder: '50', hint: 'Chars before flushing (30–200).' },
  { key: 'max_chunk_length', label: 'Max chunk length', kind: 'number', min: 50, max: 500, step: 10, placeholder: '150' },
  { key: 'enable_preprocessing', label: 'Enable preprocessing', kind: 'bool', hint: 'bulbul:v2 only — normalize English/numbers.' },
  { key: 'enable_cached_responses', label: 'Cached responses', kind: 'bool', hint: 'bulbul:v2 only.' },
  { key: 'dict_id', label: 'Pronunciation dict ID', kind: 'text', hint: 'bulbul:v3 custom pronunciation dictionary.' },
];

// ── Gemini LLM ────────────────────────────────────────────────────────────────
export const GEMINI_LLM_FIELDS: ParamField[] = [
  { key: 'top_p', label: 'Top-p', kind: 'number', min: 0, max: 1, step: 0.05, hint: 'Nucleus sampling (0–1).' },
  { key: 'top_k', label: 'Top-k', kind: 'number', min: 0, step: 1 },
  { key: 'presence_penalty', label: 'Presence penalty', kind: 'number', min: -2, max: 2, step: 0.1 },
  { key: 'frequency_penalty', label: 'Frequency penalty', kind: 'number', min: -2, max: 2, step: 0.1 },
  { key: 'max_output_tokens', label: 'Max output tokens', kind: 'number', min: 1, step: 1 },
  { key: 'seed', label: 'Seed', kind: 'number', step: 1, hint: 'Deterministic sampling.' },
];
