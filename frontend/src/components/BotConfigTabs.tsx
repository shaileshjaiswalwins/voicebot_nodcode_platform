import React, { useState } from 'react';
import { AlertTriangle, Database } from 'lucide-react';
import type { RuntimeConfig, CustomFunction, FunctionTestResult } from '../types';
import type { LanguageOption } from '../api';
import { SARVAM_TTS_VOICES, SARVAM_TTS_LANGUAGES } from '../constants/ui';
import { CustomFunctionsEditor } from './CustomFunctionsEditor';
import { CloseMarkersEditor } from './CloseMarkersEditor';
import { ProviderOptionsEditor } from './ProviderOptionsEditor';
import { SARVAM_STT_FIELDS, SARVAM_TTS_FIELDS, GEMINI_LLM_FIELDS } from '../constants/providerParams';

export type BuilderTab = 'agent' | 'speed' | 'stt' | 'tts' | 'llm' | 'functions' | 'advanced';

const TABS: { id: BuilderTab; label: string }[] = [
  { id: 'agent', label: 'Agent' },
  { id: 'speed', label: 'Speed' },
  { id: 'stt', label: 'STT' },
  { id: 'tts', label: 'TTS' },
  { id: 'llm', label: 'LLM' },
  { id: 'functions', label: 'Functions' },
  { id: 'advanced', label: 'Advanced' },
];

/** Tabbed bot settings (retell.ai-style): Agent · Speed · STT · TTS · LLM · Functions ·
 * Advanced. Every field is per-bot and versioned via the parent's onUpdateConfig. */
export function BotConfigTabs({
  value,
  onUpdateConfig,
  onUpdateLanguage,
  languages,
  configText,
  onConfigTextChange,
  configOk,
  configError,
  botId,
  onTestFunction,
}: {
  value: RuntimeConfig;
  onUpdateConfig: (key: keyof RuntimeConfig, value: unknown) => void;
  onUpdateLanguage: (value: string) => void;
  languages: LanguageOption[];
  configText: string;
  onConfigTextChange: (value: string) => void;
  configOk: boolean;
  configError?: string;
  botId?: string;
  onTestFunction?: (fn: CustomFunction, args: Record<string, unknown>) => Promise<FunctionTestResult>;
}) {
  const [tab, setTab] = useState<BuilderTab>('agent');
  const recording = (typeof value.recording === 'object' && value.recording ? value.recording : {}) as RuntimeConfig['recording'];
  const apiUrls = (typeof value.api_urls === 'object' && value.api_urls ? value.api_urls : {}) as Record<string, string>;
  const optsFor = (key: 'stt_options' | 'tts_options' | 'llm_options'): Record<string, unknown> =>
    (typeof value[key] === 'object' && value[key] ? value[key] : {}) as Record<string, unknown>;

  return (
    <div className="bot-config-tabs">
      <div className="cf-tabbar" role="tablist" style={{ display: 'flex', gap: '0.25rem', borderBottom: '1px solid var(--border)', marginBottom: '1rem', flexWrap: 'wrap' }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? 'cf-tab active' : 'cf-tab'}
            onClick={() => setTab(t.id)}
            style={{
              padding: '0.45rem 0.85rem', border: 'none', background: 'none', cursor: 'pointer',
              fontWeight: tab === t.id ? 700 : 500,
              borderBottom: tab === t.id ? '2px solid var(--primary, #2563eb)' : '2px solid transparent',
              color: tab === t.id ? 'var(--primary, #2563eb)' : 'var(--muted)',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'agent' && (
        <div role="tabpanel">
          <div className="form-grid">
            <label>
              Persona name
              <input value={String(value.agent_name || '')} placeholder="e.g. Tarun, Priya, Aman" onChange={(e) => onUpdateConfig('agent_name', e.target.value)} />
              <small>Used in opening line and system prompt.</small>
            </label>
            <label>
              Organization name
              <input value={String(value.organization_name || '')} placeholder="e.g. JustDial" onChange={(e) => onUpdateConfig('organization_name', e.target.value)} />
            </label>
            <label>
              AI partner key
              <input value={String(value.ai_partner || '')} placeholder="e.g. inh-suny-bot" onChange={(e) => onUpdateConfig('ai_partner', e.target.value)} />
              <small>Dialer lead fetch tag. Leave blank to use platform default.</small>
            </label>
          </div>
          <label className="full">
            System prompt
            <textarea className="prompt-editor" value={String(value.system_prompt || '')} onChange={(e) => onUpdateConfig('system_prompt', e.target.value)} />
          </label>
          <div className="form-grid">
            <label>
              Opening line
              <input value={String(value.initial_message || '')} onChange={(e) => onUpdateConfig('initial_message', e.target.value)} />
            </label>
            <label>
              Closing line
              <input value={String(value.call_end_text || '')} onChange={(e) => onUpdateConfig('call_end_text', e.target.value)} />
            </label>
            <label>
              Inactivity end phrase
              <input value={String(value.inactivity_end_text || '')} placeholder="Platform default used if blank" onChange={(e) => onUpdateConfig('inactivity_end_text', e.target.value)} />
            </label>
            <label>
              Language
              <select value={String(value.language || '')} onChange={(e) => onUpdateLanguage(e.target.value)}>
                {languages.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
              </select>
              <small>Not yet wired into the runtime — bot_pipeline.py's Sarvam STT/TTS always runs in Hindi (hi-IN) regardless of this setting.</small>
            </label>
            <label>
              Max call duration: {Number(value.max_call_duration || 300)}s ({Math.round(Number(value.max_call_duration || 300) / 60)} min)
              <input type="range" min={60} max={600} step={30} value={Number(value.max_call_duration || 300)} onChange={(e) => onUpdateConfig('max_call_duration', Number(e.target.value))} />
            </label>
          </div>
        </div>
      )}

      {tab === 'speed' && (
        <div role="tabpanel" className="form-grid">
          <label>
            Post-speech hold ms
            <input type="number" value={Number(value.post_speech_hold_ms ?? 400)} onChange={(e) => onUpdateConfig('post_speech_hold_ms', Number(e.target.value))} />
            <small>How long to hold after the caller stops speaking before the bot responds.</small>
          </label>
          <label>
            Voice-activity threshold
            <input type="number" min="0" max="1" step="0.05" value={Number(value.silero_threshold ?? 0.6)} onChange={(e) => onUpdateConfig('silero_threshold', Number(e.target.value))} />
            <small>Sensitivity for detecting real speech vs. background noise.</small>
          </label>
          <label>
            Min speech duration ms
            <input type="number" value={Number(value.silero_min_speech_ms ?? 1000)} onChange={(e) => onUpdateConfig('silero_min_speech_ms', Number(e.target.value))} />
          </label>
          <label>
            First rescue (s)
            <input type="number" step="0.5" value={Number(value.inactivity_first_rescue_secs ?? 4)} onChange={(e) => onUpdateConfig('inactivity_first_rescue_secs', Number(e.target.value))} />
          </label>
          <label>
            First nudge gap (s)
            <input type="number" step="0.5" value={Number(value.inactivity_first_nudge_gap_secs ?? 4)} onChange={(e) => onUpdateConfig('inactivity_first_nudge_gap_secs', Number(e.target.value))} />
          </label>
          <label>
            Nudge interval (s)
            <input type="number" step="0.5" value={Number(value.inactivity_nudge_secs ?? 10)} onChange={(e) => onUpdateConfig('inactivity_nudge_secs', Number(e.target.value))} />
          </label>
          <label>
            Auto-close (s)
            <input type="number" step="0.5" value={Number(value.inactivity_close_secs ?? 5)} onChange={(e) => onUpdateConfig('inactivity_close_secs', Number(e.target.value))} />
          </label>
          <label>
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <input type="checkbox" checked={Boolean(value.backchanneling_enabled)} onChange={(e) => onUpdateConfig('backchanneling_enabled', e.target.checked)} />
              Backchanneling
            </span>
            <small>Plays a short hold/acknowledgment sound during tool calls.</small>
          </label>
          <label>
            Noise filter sensitivity
            <select value={String(value.noise_filter_sensitivity || 'medium')} onChange={(e) => onUpdateConfig('noise_filter_sensitivity', e.target.value)}>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </label>
        </div>
      )}

      {tab === 'stt' && (
        <div role="tabpanel" className="form-grid">
          <label>
            Speech-to-text (STT)
            <select value={String(value.stt_provider || '')} onChange={(e) => onUpdateConfig('stt_provider', e.target.value)}>
              <option value="">Sarvam (default)</option>
              <option value="sarvam">Sarvam</option>
              <option value="deepgram">Deepgram</option>
            </select>
          </label>
          {value.stt_provider === 'deepgram' && (
            <>
              <label>
                STT model
                <input value={String(value.stt_model || '')} placeholder="nova-3 (default)" onChange={(e) => onUpdateConfig('stt_model', e.target.value)} />
              </label>
              <label>
                STT language
                <input value={String(value.stt_language || '')} placeholder="en-US (default)" onChange={(e) => onUpdateConfig('stt_language', e.target.value)} />
              </label>
            </>
          )}
          {(value.stt_provider === 'sarvam' || !value.stt_provider) && (
            <div style={{ gridColumn: '1 / -1' }}>
              <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0.75rem 0 0.25rem' }}>
                Sarvam STT parameters
              </div>
              <ProviderOptionsEditor fields={SARVAM_STT_FIELDS} value={optsFor('stt_options')} onChange={(next) => onUpdateConfig('stt_options', next)} />
            </div>
          )}
        </div>
      )}

      {tab === 'tts' && (
        <div role="tabpanel" className="form-grid">
          <label>
            Text-to-speech (TTS)
            <select value={String(value.tts_provider || '')} onChange={(e) => onUpdateConfig('tts_provider', e.target.value)}>
              <option value="">Sarvam (default)</option>
              <option value="sarvam">Sarvam</option>
              <option value="elevenlabs">ElevenLabs</option>
            </select>
          </label>
          {(value.tts_provider === 'sarvam' || !value.tts_provider) && (
            <>
              <label>
                Sarvam voice
                <select value={String(value.tts_voice || '')} onChange={(e) => onUpdateConfig('tts_voice', e.target.value)}>
                  <option value="">simran (default)</option>
                  {SARVAM_TTS_VOICES.map((voice) => <option key={voice} value={voice}>{voice}</option>)}
                </select>
              </label>
              <label>
                Sarvam language
                <select value={String(value.tts_language || '')} onChange={(e) => onUpdateConfig('tts_language', e.target.value)}>
                  <option value="">Hindi (default)</option>
                  {SARVAM_TTS_LANGUAGES.map((lang) => <option key={lang.id} value={lang.id}>{lang.label}</option>)}
                </select>
              </label>
            </>
          )}
          {value.tts_provider === 'elevenlabs' && (
            <label>
              ElevenLabs voice ID
              <input value={String(value.tts_voice || '')} placeholder="Paste a voice ID from your ElevenLabs dashboard" onChange={(e) => onUpdateConfig('tts_voice', e.target.value)} />
              <small>Find voice IDs at elevenlabs.io under Voices.</small>
            </label>
          )}
          {(value.tts_provider === 'sarvam' || !value.tts_provider) && (
            <div style={{ gridColumn: '1 / -1' }}>
              <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0.75rem 0 0.25rem' }}>
                Sarvam TTS parameters
              </div>
              <ProviderOptionsEditor fields={SARVAM_TTS_FIELDS} value={optsFor('tts_options')} onChange={(next) => onUpdateConfig('tts_options', next)} />
            </div>
          )}
        </div>
      )}

      {tab === 'llm' && (
        <div role="tabpanel" className="form-grid">
          <label>
            LLM
            <select value={String(value.llm_provider || '')} onChange={(e) => onUpdateConfig('llm_provider', e.target.value)}>
              <option value="">Gemini (default)</option>
              <option value="gemini">Gemini</option>
              <option value="openai">OpenAI</option>
            </select>
          </label>
          {value.llm_provider === 'openai' && (
            <label>
              LLM model
              <input value={String(value.llm_model || '')} placeholder="gpt-4.1 (default)" onChange={(e) => onUpdateConfig('llm_model', e.target.value)} />
            </label>
          )}
          <label>
            Temperature
            <input type="number" min="0" max="2" step="0.1" value={Number(value.temperature ?? 0.4)} onChange={(e) => onUpdateConfig('temperature', Number(e.target.value))} />
            <small>LLM sampling temperature.</small>
          </label>
          {(value.llm_provider === 'gemini' || !value.llm_provider) && (
            <div style={{ gridColumn: '1 / -1' }}>
              <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0.75rem 0 0.25rem' }}>
                Gemini parameters
              </div>
              <ProviderOptionsEditor fields={GEMINI_LLM_FIELDS} value={optsFor('llm_options')} onChange={(next) => onUpdateConfig('llm_options', next)} />
            </div>
          )}
        </div>
      )}

      {tab === 'functions' && (
        <div role="tabpanel">
          <label style={{ display: 'block', marginBottom: '0.75rem' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <input type="checkbox" checked={Boolean(value.function_calling)} onChange={(e) => onUpdateConfig('function_calling', e.target.checked)} style={{ width: 'auto' }} />
              <span>Enable function calling (during-call tools)</span>
            </span>
            <small>Required for the bot to call during-call functions. Pre/post-call functions run regardless.</small>
          </label>
          <CustomFunctionsEditor
            functions={Array.isArray(value.functions) ? (value.functions as CustomFunction[]) : []}
            onChange={(next) => onUpdateConfig('functions', next)}
            onTest={botId ? onTestFunction : undefined}
          />
        </div>
      )}

      {tab === 'advanced' && (
        <div role="tabpanel">
          <div className="form-grid">
            <label>
              Dialer service ID
              <input type="number" value={Number(recording?.service_id || 293)} onChange={(e) => onUpdateConfig('recording', { ...recording, service_id: Number(e.target.value) })} />
            </label>
            <label>
              Dialer city
              <input value={String(recording?.dialer_city || 'bangalore')} onChange={(e) => onUpdateConfig('recording', { ...recording, dialer_city: e.target.value })} />
            </label>
            <label>
              MIS API base URL
              <input value={String(apiUrls?.mis_api_base || '')} placeholder="Leave blank to use platform default" onChange={(e) => onUpdateConfig('api_urls', { ...apiUrls, mis_api_base: e.target.value })} />
              <small>Per-bot override for MIS lead fetch endpoint.</small>
            </label>
          </div>
          <CloseMarkersEditor
            markers={Array.isArray(value.close_markers) ? (value.close_markers as string[]) : []}
            onChange={(v) => onUpdateConfig('close_markers', v)}
          />
          <div style={{ marginTop: '1rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem' }}>
              <Database size={16} />
              <strong style={{ fontSize: '0.9rem' }}>Developer JSON</strong>
            </div>
            <p style={{ fontSize: '0.78rem', color: 'var(--muted)', margin: '0 0 0.4rem' }}>Full runtime config. Edits here override the fields above.</p>
            <textarea className="json-editor" value={configText} onChange={(e) => onConfigTextChange(e.target.value)} spellCheck={false} aria-invalid={!configOk} />
            {!configOk && (
              <div className="notice error" role="alert" style={{ marginTop: '0.5rem' }}>
                <AlertTriangle size={16} /> Invalid JSON — fix before saving: {configError}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
