import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BuilderView } from './BuilderView';
import type { Bot, BotVersion } from '../api';
import { defaultConfig } from '../constants/ui';

function makeBot(overrides: Partial<Bot> = {}): Bot {
  return {
    _id: 'bot-1',
    name: 'Sales Bot',
    description: '',
    assistant_id: 'asst-1',
    status: 'active',
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

function makeVersion(overrides: Partial<BotVersion> = {}): BotVersion {
  return {
    _id: 'v1',
    bot_id: 'bot-1',
    version: 1,
    state: 'draft',
    config: defaultConfig,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

const baseProps = {
  selectedBot: makeBot(),
  versions: [makeVersion()],
  publishedCount: 0,
  config: { ok: true as const, value: defaultConfig },
  configText: JSON.stringify(defaultConfig, null, 2),
  languages: [{ id: 'hindi', label: 'Hindi' }],
  onConfigTextChange: vi.fn(),
  onUpdateConfig: vi.fn(),
  onUpdateLanguage: vi.fn(),
  onSaveDraft: vi.fn(),
  onPublish: vi.fn(),
  builderMode: 'advanced' as const,
};

describe('BuilderView — speech-to-speech field cleanup', () => {
  it('does not show the dead Gemini-Live-only fields (Voice, Model, Silence/Prefix padding)', () => {
    render(<BuilderView {...baseProps} />);
    expect(screen.queryByText('Voice', { selector: 'label > *' })).not.toBeInTheDocument();
    expect(screen.queryByText('Model')).not.toBeInTheDocument();
    expect(screen.queryByText('Silence duration ms')).not.toBeInTheDocument();
    expect(screen.queryByText('Prefix padding ms')).not.toBeInTheDocument();
  });

  it('shows the real bot_pipeline.py controls instead: silero and inactivity timeouts', () => {
    render(<BuilderView {...baseProps} />);
    expect(screen.getByText('Voice-activity threshold')).toBeInTheDocument();
    expect(screen.getByText('Min speech duration ms')).toBeInTheDocument();
    expect(screen.getByText('First rescue (s)')).toBeInTheDocument();
    expect(screen.getByText('First nudge gap (s)')).toBeInTheDocument();
    expect(screen.getByText('Nudge interval (s)')).toBeInTheDocument();
    expect(screen.getByText('Auto-close (s)')).toBeInTheDocument();
  });

  it('notes that the Language field has no effect on the current Sarvam pipeline', () => {
    render(<BuilderView {...baseProps} />);
    expect(screen.getByText(/always runs in Hindi/)).toBeInTheDocument();
  });
});

describe('BuilderView — per-agent STT/TTS/LLM provider selection', () => {
  it('shows provider dropdowns for STT, TTS, and LLM defaulting to the existing stack', () => {
    render(<BuilderView {...baseProps} />);
    expect(screen.getByLabelText('Speech-to-text (STT)')).toHaveValue('');
    expect(screen.getByLabelText('Text-to-speech (TTS)')).toHaveValue('');
    expect(screen.getByLabelText('LLM')).toHaveValue('');
  });

  it('hides provider-specific sub-fields until a non-default provider is selected', () => {
    render(<BuilderView {...baseProps} />);
    expect(screen.queryByLabelText('STT model')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('ElevenLabs voice ID')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('LLM model')).not.toBeInTheDocument();
  });

  it('reveals Deepgram STT sub-fields (model, language) when selected', async () => {
    const user = userEvent.setup();
    const onUpdateConfig = vi.fn();
    render(<BuilderView {...baseProps} onUpdateConfig={onUpdateConfig} />);
    await user.selectOptions(screen.getByLabelText('Speech-to-text (STT)'), 'deepgram');
    expect(onUpdateConfig).toHaveBeenCalledWith('stt_provider', 'deepgram');
  });

  it('reveals a free-text voice ID field for ElevenLabs TTS, not a fixed dropdown', () => {
    const config = { ...defaultConfig, tts_provider: 'elevenlabs' as const };
    render(<BuilderView {...baseProps} config={{ ok: true, value: config }} />);
    const voiceField = screen.getByLabelText(/ElevenLabs voice ID/);
    expect(voiceField.tagName).toBe('INPUT');
  });

  it('reveals a real Sarvam voice dropdown (from the actual bulbul:v3 voice list) when Sarvam TTS is explicitly selected', () => {
    const config = { ...defaultConfig, tts_provider: 'sarvam' as const };
    render(<BuilderView {...baseProps} config={{ ok: true, value: config }} />);
    const voiceField = screen.getByLabelText('Sarvam voice');
    expect(voiceField.tagName).toBe('SELECT');
    expect(screen.getByRole('option', { name: 'simran' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'shubh' })).toBeInTheDocument();
  });

  it('reveals an LLM model field when OpenAI is selected', () => {
    const config = { ...defaultConfig, llm_provider: 'openai' as const };
    render(<BuilderView {...baseProps} config={{ ok: true, value: config }} />);
    expect(screen.getByLabelText('LLM model')).toBeInTheDocument();
  });
});

describe('defaultConfig — no speech-to-speech-only fields', () => {
  it('does not include model, voice, livekit_language, or gemini VAD params', () => {
    expect(defaultConfig).not.toHaveProperty('model');
    expect(defaultConfig).not.toHaveProperty('voice');
    expect(defaultConfig).not.toHaveProperty('livekit_language');
    expect(defaultConfig).not.toHaveProperty('gemini_silence_duration_ms');
    expect(defaultConfig).not.toHaveProperty('gemini_prefix_padding_ms');
  });

  it('includes the real bot_pipeline.py fields with matching fallback defaults', () => {
    expect(defaultConfig.temperature).toBe(0.4);
    expect(defaultConfig.post_speech_hold_ms).toBe(400);
    expect(defaultConfig.silero_threshold).toBe(0.6);
    expect(defaultConfig.silero_min_speech_ms).toBe(1000);
  });
});
