import React from 'react';
import {
  Activity,
  BarChart2,
  BookOpen,
  Bot,
  CheckCircle2,
  ClipboardList,
  Clock3,
  FileText,
  IndianRupee,
  Link2,
  Megaphone,
  Phone,
  PhoneCall,
  Settings,
  XCircle
} from 'lucide-react';
import type { RuntimeConfig, View } from '../types';

// Hindi conjugates the speaker's verb by gender, so the opening line has to match the
// persona. Keyed so the New-agent form can swap the default when gender changes.
export const OPENING_LINE_BY_GENDER: Record<'female' | 'male', string> = {
  female: 'हेलो, मैं {agent_name} बोल रही हूँ {organization_name} से — आपको {product} की requirement है ना?',
  male: 'हेलो, मैं {agent_name} बोल रहा हूँ {organization_name} से — आपको {product} की requirement है ना?',
};

export const defaultConfig: RuntimeConfig = {
  agent_name: '',
  organization_name: '',
  persona_gender: 'female',
  ai_partner: '',
  language: 'hindi',
  temperature: 0.4,
  max_call_duration: 300,
  system_prompt: 'You are a warm and professional call center agent. Greet the caller, understand their requirement, and collect key details.',
  initial_message: OPENING_LINE_BY_GENDER.female,
  call_end_text: 'ठीक है जी, सारी details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए शुक्रिया.',
  inactivity_end_text: '',
  function_calling: false,
  post_speech_hold_ms: 400,
  silero_threshold: 0.6,
  silero_min_speech_ms: 1000,
  inactivity_first_rescue_secs: 4,
  inactivity_first_nudge_gap_secs: 4,
  inactivity_nudge_secs: 10,
  inactivity_close_secs: 5,
  recording: {
    service_id: 293,
    dialer_city: 'bangalore'
  }
};

// Shared duration for transient "success" UI feedback (copy checkmarks, inline "Saved ✓"
// button labels, etc.) so these don't drift into inconsistent timings across the app.
export const FEEDBACK_TIMEOUT_MS = 2000;

// Real Sarvam bulbul:v3 voice catalog (docs.sarvam.ai/api-reference-docs/text-to-speech/convert)
// — not a placeholder list. ElevenLabs has no equivalent fixed catalog (voices are
// account-specific, including custom/cloned voices), so that provider uses a free-text
// voice ID field instead — see BuilderView's Pipeline section.
export const SARVAM_TTS_VOICES: string[] = [
  'shubh', 'aditya', 'ritu', 'priya', 'neha', 'rahul', 'pooja', 'rohan', 'simran', 'kavya',
  'amit', 'dev', 'ishita', 'shreya', 'ratan', 'varun', 'manan', 'sumit', 'roopa', 'kabir',
  'aayan', 'ashutosh', 'advait', 'anand', 'tanya', 'tarun', 'sunny', 'mani', 'gokul', 'vijay',
  'shruti', 'suhani', 'mohit', 'kavitha', 'rehan', 'soham', 'rupali',
];

// Maps a bot's tts_provider to the pricing-catalog key (backend/pricing.py's TTS_RATES) so
// the Agent Builder's cost estimate (agentCost.ts, keyed off tts_model) matches whichever TTS
// actually runs the call instead of silently defaulting to Sarvam's rate for every provider.
export const TTS_PROVIDER_MODEL_KEY: Record<string, string> = {
  '': 'sarvam_bulbul_v3',
  sarvam: 'sarvam_bulbul_v3',
  elevenlabs: 'elevenlabs_turbo',
  justdial: 'indic_f5',
};

export const SARVAM_TTS_LANGUAGES: { id: string; label: string }[] = [
  { id: 'hi-IN', label: 'Hindi' },
  { id: 'en-IN', label: 'English (India)' },
  { id: 'bn-IN', label: 'Bengali' },
  { id: 'gu-IN', label: 'Gujarati' },
  { id: 'kn-IN', label: 'Kannada' },
  { id: 'ml-IN', label: 'Malayalam' },
  { id: 'mr-IN', label: 'Marathi' },
  { id: 'od-IN', label: 'Odia' },
  { id: 'pa-IN', label: 'Punjabi' },
  { id: 'ta-IN', label: 'Tamil' },
  { id: 'te-IN', label: 'Telugu' },
];

export const STATUS_ICONS: Record<string, React.ReactNode> = {
  completed:    <CheckCircle2 size={12} aria-hidden />,
  active:       <CheckCircle2 size={12} aria-hidden />,
  published:    <CheckCircle2 size={12} aria-hidden />,
  enabled:      <CheckCircle2 size={12} aria-hidden />,
  ready:        <CheckCircle2 size={12} aria-hidden />,
  running:      <Activity size={12} aria-hidden />,
  connecting:   <Activity size={12} aria-hidden />,
  disconnected: <XCircle size={12} aria-hidden />,
  failed:       <XCircle size={12} aria-hidden />,
  error:        <XCircle size={12} aria-hidden />,
  not_interested: <XCircle size={12} aria-hidden />,
  draft:        <Clock3 size={12} aria-hidden />,
  paused:       <Clock3 size={12} aria-hidden />,
  pending:      <Clock3 size={12} aria-hidden />,
  queued:       <Clock3 size={12} aria-hidden />,
  dialing:      <Activity size={12} aria-hidden />,
  voicemail:    <Clock3 size={12} aria-hidden />,
  busy:         <Clock3 size={12} aria-hidden />,
  no_answer:    <Clock3 size={12} aria-hidden />,
  alert:        <XCircle size={12} aria-hidden />,
  ok:           <CheckCircle2 size={12} aria-hidden />,
};

export type CmdKViewResult = { kind: 'view'; view: View; label: string; icon: React.ReactNode; description?: string };

export const CMD_VIEWS: CmdKViewResult[] = [
  { kind: 'view', view: 'bots',          label: 'Agents',        icon: <Bot size={15} />,          description: 'Manage voice agents' },
  { kind: 'view', view: 'campaigns',     label: 'Campaigns',     icon: <Megaphone size={15} />,    description: 'Campaign mappings' },
  { kind: 'view', view: 'phone_numbers', label: 'Phone Numbers', icon: <Phone size={15} />,        description: 'Number-to-bot routing' },
  { kind: 'view', view: 'number_mapping', label: 'Number Mapping', icon: <Link2 size={15} />,      description: 'Map agents to numbers' },
  { kind: 'view', view: 'test',          label: 'Test Call',     icon: <PhoneCall size={15} />,    description: 'Run a browser call' },
  { kind: 'view', view: 'transcripts',   label: 'Transcripts',   icon: <FileText size={15} />,     description: 'Browse call transcripts' },
  { kind: 'view', view: 'analytics',     label: 'Analytics',     icon: <BarChart2 size={15} />,    description: 'Outcomes and quality' },
  { kind: 'view', view: 'library',       label: 'Library',       icon: <BookOpen size={15} />,     description: 'Phrase library' },
  { kind: 'view', view: 'settings',      label: 'Settings',      icon: <Settings size={15} />,     description: 'Runtime settings' },
  { kind: 'view', view: 'audit_log',     label: 'Audit Log',     icon: <ClipboardList size={15} />, description: 'Admin mutation history' },
  { kind: 'view', view: 'admin',         label: 'Admin',         icon: <IndianRupee size={15} />,  description: 'Model pricing (₹/min)' },
];

export const SHORTCUT_MAP: Array<{ key: string; view: View; label: string }> = [
  { key: 'b', view: 'bots',          label: 'Go to Agents' },
  { key: 'c', view: 'campaigns',     label: 'Go to Campaigns' },
  { key: 'p', view: 'phone_numbers', label: 'Go to Phone Numbers' },
  { key: 't', view: 'test',          label: 'Go to Test Call' },
  { key: 'x', view: 'transcripts',   label: 'Go to Transcripts' },
  { key: 'a', view: 'analytics',     label: 'Go to Analytics' },
];
