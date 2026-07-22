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
  { key: 'mode', label: 'Mode', kind: 'select', options: ['transcribe', 'translate', 'verbatim', 'translit', 'codemix'], placeholder: 'transcribe', hint: 'How Sarvam returns text. Only saaras:v3 supports non-transcribe modes.' },
  { key: 'sample_rate', label: 'Input sample rate (Hz)', kind: 'number', placeholder: '16000', min: 8000, step: 1000, hint: 'Sample rate of the audio sent to STT. Must match the telephony stream (usually 16000).' },
  { key: 'high_vad_sensitivity', label: 'High VAD sensitivity', kind: 'bool', hint: 'Detect softer/shorter utterances (more sensitive, may pick up noise).' },
  { key: 'input_audio_codec', label: 'Input audio codec', kind: 'text', placeholder: 'audio/wav', hint: 'MIME type of the incoming audio, e.g. audio/wav.' },
  // Fine-grained VAD (saaras:v3 only)
  { key: 'positive_speech_threshold', label: 'Positive speech threshold', kind: 'number', min: 0, max: 1, step: 0.05, group: 'Fine-grained VAD (saaras:v3)', hint: 'Probability (0–1) above which a frame counts as speech. Higher = stricter.' },
  { key: 'negative_speech_threshold', label: 'Negative speech threshold', kind: 'number', min: 0, max: 1, step: 0.05, group: 'Fine-grained VAD (saaras:v3)', hint: 'Probability (0–1) below which a frame counts as silence. Lower = more eager to end speech.' },
  { key: 'min_speech_frames', label: 'Min speech frames', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)', hint: 'Minimum consecutive speech frames before a turn is accepted. Higher rejects blips.' },
  { key: 'first_turn_min_speech_frames', label: 'First-turn min speech frames', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)', hint: 'Same as Min speech frames but only for the caller’s very first turn.' },
  { key: 'negative_frames_count', label: 'Negative frames count', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)', hint: 'Silence frames required to declare end of speech.' },
  { key: 'negative_frames_window', label: 'Negative frames window', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)', hint: 'Sliding window (frames) over which the silence count is measured.' },
  { key: 'start_speech_volume_threshold', label: 'Start speech volume (dB)', kind: 'number', step: 1, group: 'Fine-grained VAD (saaras:v3)', hint: 'Minimum loudness (dB) to start capturing speech. Raise to ignore faint background talk.' },
  { key: 'interrupt_min_speech_frames', label: 'Interrupt min speech frames', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)', hint: 'Speech frames needed for the caller to barge in and interrupt the bot.' },
  { key: 'pre_speech_pad_frames', label: 'Pre-speech pad frames', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)', hint: 'Extra audio frames kept before detected speech so the first word isn’t clipped.' },
  { key: 'num_initial_ignored_frames', label: 'Initial ignored frames', kind: 'number', min: 0, step: 1, group: 'Fine-grained VAD (saaras:v3)', hint: 'Frames to discard at stream start (skips connect pops/noise).' },
];

// ── Sarvam TTS ────────────────────────────────────────────────────────────────
export const SARVAM_TTS_FIELDS: ParamField[] = [
  { key: 'pace', label: 'Pace', kind: 'number', min: 0.3, max: 3.0, step: 0.05, placeholder: '1.0', hint: 'Speech rate multiplier (0.3–3.0).' },
  { key: 'temperature', label: 'Temperature', kind: 'number', min: 0.01, max: 2.0, step: 0.05, placeholder: '0.75', hint: 'bulbul:v3 only (0.01–2.0).' },
  { key: 'pitch', label: 'Pitch', kind: 'number', min: -0.75, max: 0.75, step: 0.05, placeholder: '0.0', hint: 'Range -0.75–0.75.' },
  { key: 'loudness', label: 'Loudness', kind: 'number', min: 0.5, max: 2.0, step: 0.05, placeholder: '1.0', hint: 'Volume multiplier (0.5–2.0).' },
  { key: 'speech_sample_rate', label: 'Output sample rate (Hz)', kind: 'select', options: ['8000', '16000', '22050', '24000', '32000', '44100', '48000'], placeholder: '24000', hint: 'Sample rate of generated audio. Match your telephony pipeline (usually 24000).' },
  { key: 'output_audio_codec', label: 'Output codec', kind: 'select', options: ['linear16', 'mp3', 'wav', 'aac', 'opus', 'flac', 'mulaw', 'alaw'], placeholder: 'linear16', hint: 'Encoding of the audio Sarvam returns. linear16 is the pipeline default.' },
  { key: 'output_audio_bitrate', label: 'Output bitrate', kind: 'select', options: ['32k', '64k', '96k', '128k', '192k'], placeholder: '128k', hint: 'Bitrate for compressed codecs (mp3/aac/opus). Ignored for linear16/wav.' },
  { key: 'min_buffer_size', label: 'Min buffer size', kind: 'number', min: 30, max: 200, step: 5, placeholder: '50', hint: 'Characters buffered before audio starts flushing (30–200). Lower = snappier start, more requests.' },
  { key: 'max_chunk_length', label: 'Max chunk length', kind: 'number', min: 50, max: 500, step: 10, placeholder: '150', hint: 'Max characters sent to TTS per request (50–500). Larger = smoother prosody, higher latency.' },
  { key: 'enable_preprocessing', label: 'Enable preprocessing', kind: 'bool', hint: 'bulbul:v2 only — normalize English/numbers.' },
  { key: 'enable_cached_responses', label: 'Cached responses', kind: 'bool', hint: 'bulbul:v2 only.' },
  { key: 'dict_id', label: 'Pronunciation dict ID', kind: 'text', hint: 'ID of a bulbul:v3 custom pronunciation dictionary to apply.' },
];

// ── Gemini LLM ────────────────────────────────────────────────────────────────
export const GEMINI_LLM_FIELDS: ParamField[] = [
  { key: 'model', label: 'Model', kind: 'select', options: ['gemini-2.5-flash', 'gemini-3.1-flash-lite', 'gemini-3.1-pro'], placeholder: 'gemini-3.1-flash-lite', hint: 'Which Gemini model handles the conversation. Affects both response quality/speed and cost per minute.' },
  { key: 'top_p', label: 'Top-p', kind: 'number', min: 0, max: 1, step: 0.05, hint: 'Nucleus sampling (0–1): consider only the most probable tokens summing to this mass. Lower = more focused.' },
  { key: 'top_k', label: 'Top-k', kind: 'number', min: 0, step: 1, hint: 'Sample only from the K most likely tokens. Lower = more focused; 0/blank = disabled.' },
  { key: 'presence_penalty', label: 'Presence penalty', kind: 'number', min: -2, max: 2, step: 0.1, hint: 'Penalise tokens already used, encouraging new topics (-2 to 2).' },
  { key: 'frequency_penalty', label: 'Frequency penalty', kind: 'number', min: -2, max: 2, step: 0.1, hint: 'Penalise repeated tokens, reducing verbatim repetition (-2 to 2).' },
  { key: 'max_output_tokens', label: 'Max output tokens', kind: 'number', min: 1, step: 1, hint: 'Hard cap on tokens per reply. Keep modest for low-latency voice turns.' },
  { key: 'seed', label: 'Seed', kind: 'number', step: 1, hint: 'Fixed seed for reproducible sampling. Same input + seed = same output.' },
];
