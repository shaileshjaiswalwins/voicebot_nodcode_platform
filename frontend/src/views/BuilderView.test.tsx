import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BuilderView } from './BuilderView';
import { api } from '../api';

/** Settings now live under tabs (Agent · Speed · STT · TTS · LLM · Functions · Advanced).
 * Click into a tab before asserting on its fields. */
function gotoTab(name: string) {
  fireEvent.click(screen.getByRole('tab', { name }));
}
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
  languages: [{ id: 'hi', label: 'Hindi' }, { id: 'en', label: 'English' }],
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
    gotoTab('Speed');
    expect(screen.getByText('Voice-activity threshold')).toBeInTheDocument();
    expect(screen.getByText('Min speech duration (ms)')).toBeInTheDocument();
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
    gotoTab('STT');
    expect(screen.getByLabelText('Speech-to-text (STT)')).toHaveValue('');
    gotoTab('TTS');
    expect(screen.getByLabelText('Text-to-speech (TTS)')).toHaveValue('');
    gotoTab('LLM');
    expect(screen.getByLabelText('LLM')).toHaveValue('');
  });

  it('hides provider-specific sub-fields until a non-default provider is selected', () => {
    render(<BuilderView {...baseProps} />);
    gotoTab('STT');
    expect(screen.queryByLabelText('STT model')).not.toBeInTheDocument();
    gotoTab('TTS');
    expect(screen.queryByLabelText('ElevenLabs voice ID')).not.toBeInTheDocument();
    gotoTab('LLM');
    expect(screen.queryByLabelText('OpenAI model')).not.toBeInTheDocument();
  });

  it('reveals Deepgram STT sub-fields (model, language) when selected', async () => {
    const user = userEvent.setup();
    const onUpdateConfig = vi.fn();
    render(<BuilderView {...baseProps} onUpdateConfig={onUpdateConfig} />);
    gotoTab('STT');
    await user.selectOptions(screen.getByLabelText('Speech-to-text (STT)'), 'deepgram');
    expect(onUpdateConfig).toHaveBeenCalledWith('stt_provider', 'deepgram');
  });

  it('reveals a free-text voice ID field for ElevenLabs TTS, not a fixed dropdown', () => {
    const config = { ...defaultConfig, tts_provider: 'elevenlabs' as const };
    render(<BuilderView {...baseProps} config={{ ok: true, value: config }} />);
    gotoTab('TTS');
    const voiceField = screen.getByLabelText(/ElevenLabs voice ID/);
    expect(voiceField.tagName).toBe('INPUT');
  });

  it('reveals a real Sarvam voice dropdown (from the actual bulbul:v3 voice list) when Sarvam TTS is explicitly selected', () => {
    const config = { ...defaultConfig, tts_provider: 'sarvam' as const };
    render(<BuilderView {...baseProps} config={{ ok: true, value: config }} />);
    gotoTab('TTS');
    const voiceField = screen.getByLabelText('Sarvam voice');
    expect(voiceField.tagName).toBe('SELECT');
    expect(screen.getByRole('option', { name: 'simran' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'shubh' })).toBeInTheDocument();
  });

  it('reveals an LLM model field when OpenAI is selected', () => {
    const config = { ...defaultConfig, llm_provider: 'openai' as const };
    render(<BuilderView {...baseProps} config={{ ok: true, value: config }} />);
    gotoTab('LLM');
    expect(screen.getByLabelText('OpenAI model')).toBeInTheDocument();
  });
});

describe('BuilderView — Agent details cost card', () => {
  it('renders Cost/Latency/Tokens once pricing config loads, in INR', async () => {
    vi.spyOn(api, 'getPricingAdminConfig').mockResolvedValue({
      stt: [{ key: 'sarvam_saras_v3', label: 'sarvam-saras-v3', cost_inr_per_min: 0.25 }],
      llm: [{ key: 'gemini_3_1_flash_lite', label: 'gemini-3.1-flash-lite', cost_inr_per_min: 0.87, latency_ms_min: 970, latency_ms_max: 1450, tokens_min: 597, tokens_max: 1000 }],
      tts: [{ key: 'sarvam_bulbul_v3', label: 'sarvam-bulbul-v3', cost_inr_per_min: 1.60 }],
      telephony: [{ key: 'sip_direct', label: 'sip-direct', cost_inr_per_min: 0 }],
    });

    render(<BuilderView {...baseProps} />);

    await waitFor(() => {
      expect(screen.getByText('₹2.72/min')).toBeInTheDocument();
    });
    expect(screen.getByText('970-1450ms')).toBeInTheDocument();
    expect(screen.getByText('597 - 1k')).toBeInTheDocument();
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
