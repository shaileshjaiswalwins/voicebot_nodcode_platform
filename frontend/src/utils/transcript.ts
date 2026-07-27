import type { CallEvent, Transcript } from '../api';
import type { ConversationItem } from '../types';
import { formatTime } from './formatting';
import { titleCase } from './formatting';

export const INLINE_EVENT_TYPES = new Set([
  'call_started',
  'first_user_audio_received',
  'first_user_transcript_received',
  'first_agent_response',
  'first_word_spoken',
  'user_interrupted',
  'interruption',
  'transcript_save_succeeded',
  'transcript_save_failed',
  'callback_failed',
  'callback_succeeded',
  'call_ended'
]);

export const START_EVENT_TYPES = new Set(['call_started', 'first_user_audio_received', 'first_user_transcript_received']);
export const END_EVENT_TYPES = new Set(['transcript_save_succeeded', 'transcript_save_failed', 'callback_failed', 'callback_succeeded', 'call_ended']);

export function buildConversationItems(transcript?: Transcript, events: CallEvent[] = []): ConversationItem[] {
  if (!transcript) return [];
  const inlineEvents = events.filter((event) => INLINE_EVENT_TYPES.has(event.event_type));
  const selectedTurns = transcript.verified_transcript_status === 'succeeded' && transcript.verified_transcript?.length
    ? transcript.verified_transcript
    : transcript.transcript || transcript.live_transcript || [];
  const turnItems: ConversationItem[] = selectedTurns.map((turn, index) => ({
    kind: 'turn',
    id: `turn-${index}`,
    role: normalizeTranscriptRole(turn.role),
    text: turn.text || '',
    time: formatTime(turn.created_at),
    sortAt: timestampValue(turn.created_at),
    interrupted: Boolean(turn.interrupted || turn.event_type?.toLowerCase().includes('interrupt'))
  }));
  const eventItems = inlineEvents.map((event) => eventToConversationItem(event));
  const hasTurnTimes = selectedTurns.some((turn) => Boolean(turn.created_at));
  if (hasTurnTimes) {
    return [...eventItems, ...turnItems].sort((a, b) => a.sortAt - b.sortAt);
  }
  return [
    ...eventItems.filter((event) => event.kind === 'event' && START_EVENT_TYPES.has(event.title)),
    ...turnItems,
    ...eventItems.filter((event) => event.kind === 'event' && !START_EVENT_TYPES.has(event.title) && !END_EVENT_TYPES.has(event.title)),
    ...eventItems.filter((event) => event.kind === 'event' && END_EVENT_TYPES.has(event.title))
  ];
}

export function eventToConversationItem(event: CallEvent): ConversationItem {
  return {
    kind: 'event',
    id: event._id,
    title: event.event_type,
    text: event.message || titleCase(event.event_type.replace(/_/g, ' ')),
    time: formatTime(event.created_at),
    sortAt: timestampValue(event.created_at),
    severity: event.severity || 'info'
  };
}

export function timestampValue(value?: string) {
  if (!value) return Number.MAX_SAFE_INTEGER;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.MAX_SAFE_INTEGER : parsed;
}

export function normalizeTranscriptRole(role: string) {
  const value = (role || '').toLowerCase();
  if (['assistant', 'agent', 'bot', 'model'].includes(value)) return 'assistant';
  if (['user', 'customer', 'caller', 'human'].includes(value)) return 'user';
  if (['recording', 'verified_recording'].includes(value)) return 'recording';
  return value || 'system';
}

export function transcriptSourceLabel(transcript: Transcript) {
  if (transcript.verified_transcript_status === 'succeeded') return 'Verified recording';
  if (transcript.verified_transcript_status === 'pending') return 'Live transcript, verification pending';
  if (transcript.verified_transcript_status === 'failed') return 'Live transcript, verification failed';
  if (transcript.verified_transcript_status === 'unavailable') return 'Live transcript, recording unavailable';
  return titleCase(transcript.analysis_transcript_source || transcript.transcript_source || 'gemini live');
}

export function maxResponseDelayLabel(transcript: Transcript) {
  const metrics = transcript.latency_metrics || {};
  const delay = Number(metrics.max_response_delay_ms || metrics.first_response_delay_ms || 0);
  if (!delay) return '-';
  return delay >= 8000 ? `${delay}ms high` : delay >= 3000 ? `${delay}ms slow` : `${delay}ms`;
}
