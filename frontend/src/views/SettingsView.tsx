import React, { useState, useEffect } from 'react';
import { AlertTriangle, Save, Settings, SlidersHorizontal } from 'lucide-react';
import type { PlatformSettings, RuntimeSettings } from '../api';
import { StatusPill } from '../components/StatusPill';
import { isValidUrl } from '../utils/validation';
import { FEEDBACK_TIMEOUT_MS } from '../constants/ui';

function Step({ title, text }: { title: string; text: string }) {
  return <div className="step"><strong>{title}</strong><p>{text}</p></div>;
}

export function SettingsView({
  runtimeSettings,
  onUpdateRuntime,
  platformSettings,
  onUpdatePlatformSettings
}: {
  runtimeSettings: RuntimeSettings | null;
  onUpdateRuntime: (payload: Partial<RuntimeSettings>) => void;
  platformSettings: PlatformSettings | null;
  onUpdatePlatformSettings: (payload: PlatformSettings) => Promise<void>;
}) {
  const [draft, setDraft] = useState({
    livekit_api_url: runtimeSettings?.livekit_api_url || '',
    livekit_browser_url: runtimeSettings?.livekit_browser_url || '',
    livekit_agent_name: runtimeSettings?.livekit_agent_name || ''
  });
  const [adminDraft, setAdminDraft] = useState({
    mis_api_base: '',
    default_inactivity_phrase: '',
    default_close_markers: ''
  });
  const [adminSaveState, setAdminSaveState] = useState<'idle' | 'running' | 'saved' | 'failed'>('idle');

  const apiUrlInvalid = !isValidUrl(draft.livekit_api_url, ['http:', 'https:']);
  const browserUrlInvalid = !isValidUrl(draft.livekit_browser_url, ['ws:', 'wss:']);
  const runtimeUrlsInvalid = apiUrlInvalid || browserUrlInvalid;
  const misApiBaseInvalid = !isValidUrl(adminDraft.mis_api_base, ['http:', 'https:']);

  useEffect(() => {
    setDraft({
      livekit_api_url: runtimeSettings?.livekit_api_url || '',
      livekit_browser_url: runtimeSettings?.livekit_browser_url || '',
      livekit_agent_name: runtimeSettings?.livekit_agent_name || ''
    });
  }, [runtimeSettings?._id, runtimeSettings?.livekit_api_url, runtimeSettings?.livekit_browser_url, runtimeSettings?.livekit_agent_name]);

  useEffect(() => {
    if (!platformSettings) return;
    const activeEnvSettings = platformSettings[platformSettings.active_environment];
    setAdminDraft({
      mis_api_base: activeEnvSettings.mis_api_base || '',
      default_inactivity_phrase: platformSettings.default_inactivity_phrase || '',
      default_close_markers: (platformSettings.default_close_markers || []).join(', '),
    });
  }, [platformSettings]);

  async function saveAdminSettings() {
    if (!platformSettings) return;
    setAdminSaveState('running');
    const activeEnv = platformSettings.active_environment;
    const payload: PlatformSettings = {
      ...platformSettings,
      default_inactivity_phrase: adminDraft.default_inactivity_phrase,
      default_close_markers: adminDraft.default_close_markers
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
      [activeEnv]: { ...platformSettings[activeEnv], mis_api_base: adminDraft.mis_api_base },
    };
    try {
      await onUpdatePlatformSettings(payload);
      setAdminSaveState('saved');
      setTimeout(() => setAdminSaveState('idle'), FEEDBACK_TIMEOUT_MS);
    } catch {
      setAdminSaveState('failed');
    }
  }

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div className="content-grid two-col">
        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>Runtime settings</h2>
              <p>Controls where dashboard test calls are created and which LiveKit worker receives them.</p>
            </div>
            <StatusPill value={runtimeSettings?.livekit_credentials_configured ? 'ready' : 'missing keys'} />
          </div>
          <div className="form-grid">
            <label>
              LiveKit API URL
              <input
                value={draft.livekit_api_url}
                onChange={(event) => setDraft({ ...draft, livekit_api_url: event.target.value })}
                aria-invalid={apiUrlInvalid}
                placeholder="https://your-livekit-host:7880"
              />
              {apiUrlInvalid && <small style={{ color: 'var(--danger)' }}>Must be a valid http(s) URL.</small>}
            </label>
            <label>
              Browser WebSocket URL
              <input
                value={draft.livekit_browser_url}
                onChange={(event) => setDraft({ ...draft, livekit_browser_url: event.target.value })}
                aria-invalid={browserUrlInvalid}
                placeholder="wss://your-livekit-host"
              />
              {browserUrlInvalid && <small style={{ color: 'var(--danger)' }}>Must be a valid ws:// or wss:// URL.</small>}
            </label>
            <label>
              Test call worker agent name
              <input value={draft.livekit_agent_name} onChange={(event) => setDraft({ ...draft, livekit_agent_name: event.target.value })} />
              {runtimeSettings?.livekit_agent_name_env_default ? (
                draft.livekit_agent_name !== runtimeSettings.livekit_agent_name_env_default ? (
                  <small style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
                    Environment default: <code>{runtimeSettings.livekit_agent_name_env_default}</code>
                    <button
                      type="button"
                      style={{ fontSize: '0.75rem', padding: '0.15rem 0.5rem', minHeight: 0 }}
                      onClick={() => setDraft({ ...draft, livekit_agent_name: runtimeSettings.livekit_agent_name_env_default! })}
                    >
                      Use it
                    </button>
                  </small>
                ) : (
                  <small>Matches the backend's environment (<code>LIVEKIT_AGENT_NAME</code>).</small>
                )
              ) : (
                <small>No <code>LIVEKIT_AGENT_NAME</code> set in the backend environment — this value is only what's saved here.</small>
              )}
            </label>
            <label>
              Secret keys
              <input value={runtimeSettings?.livekit_credentials_configured ? 'Configured in backend .env' : 'Missing in backend .env'} disabled />
            </label>
          </div>
          <div className="button-row">
            <button className="primary" onClick={() => onUpdateRuntime(draft)} disabled={runtimeUrlsInvalid}><Save size={16} /> Save runtime settings</button>
            {runtimeUrlsInvalid && (
              <span style={{ fontSize: '0.78rem', color: 'var(--danger)', display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                <AlertTriangle size={13} /> Fix the invalid URL(s) above before saving.
              </span>
            )}
          </div>
        </div>
        <div className="panel">
          <h2>Worker vs dashboard agent</h2>
          <div className="timeline">
            <Step title="Dashboard agent" text="The bot you create in the UI: prompt, voice, language and settings stored in Mongo." />
            <Step title="LiveKit worker" text="A Python process connected to LiveKit. It receives rooms for a specific agent name and runs bot.py." />
            <Step
              title="Safe testing"
              text={
                runtimeSettings?.livekit_agent_name_env_default
                  ? `Test calls dispatch to whatever worker name is set above — currently "${draft.livekit_agent_name || runtimeSettings.livekit_agent_name_env_default}". Use a name with no live worker registered under it if you don't want test calls competing with real traffic.`
                  : "Use a separate worker name so test calls do not route to live workers."
              }
            />
          </div>
        </div>
      </div>

      {/* ── Admin Tools ── */}
      <div className="panel">
        <div className="panel-header">
          <div>
            <h2 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <SlidersHorizontal size={18} /> Admin tools
            </h2>
            <p>Platform-wide defaults for bot behaviour. These are used when a bot has no per-bot override configured in Advanced mode.</p>
          </div>
          <span style={{ fontSize: '0.72rem', background: 'var(--warning-bg)', color: 'var(--warning)', border: '1px solid var(--warning-border)', borderRadius: '4px', padding: '2px 8px', fontWeight: 600 }}>Admin only</span>
        </div>

        <div className="admin-tools-grid">
          <div>
            <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Platform defaults</div>
            <div className="form-grid">
              <label>
                MIS API base URL ({platformSettings?.active_environment || 'dev'})
                <input
                  value={adminDraft.mis_api_base}
                  onChange={(e) => setAdminDraft({ ...adminDraft, mis_api_base: e.target.value })}
                  placeholder="http://192.168.8.67:8000"
                  aria-invalid={misApiBaseInvalid}
                />
                {misApiBaseInvalid ? (
                  <small style={{ color: 'var(--danger)' }}>Must be a valid http(s) URL.</small>
                ) : (
                  <small>Platform default for lead fetch in the currently active environment. Saved to the server, used by the callback worker.</small>
                )}
              </label>
              <label>
                Default inactivity end phrase
                <input
                  value={adminDraft.default_inactivity_phrase}
                  onChange={(e) => setAdminDraft({ ...adminDraft, default_inactivity_phrase: e.target.value })}
                  placeholder="Leave blank to keep current Hindi default"
                />
                <small>Spoken when caller is silent for too long. Override per-bot in Advanced mode.</small>
              </label>
              <label className="full">
                Default close markers (comma-separated)
                <textarea
                  rows={3}
                  value={adminDraft.default_close_markers}
                  onChange={(e) => setAdminDraft({ ...adminDraft, default_close_markers: e.target.value })}
                  placeholder="thank you for your time, goodbye, धन्यवाद, …"
                  style={{ fontFamily: 'inherit', fontSize: '0.83rem' }}
                />
                <small>Bot ends the call when any of these phrases are detected. Override per-bot in Advanced mode.</small>
              </label>
            </div>
          </div>
          <div>
            <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Builder modes</div>
            <div className="callout">
              <Settings size={16} />
              <div>
                <strong>Builder modes</strong>
                <p style={{ margin: '4px 0 0', fontSize: '0.82rem' }}>The <strong>PM</strong> toggle shows only core fields (prompt, voice, opening/closing line). The <strong>Advanced</strong> toggle reveals all admin fields. Mode is remembered per browser session.</p>
              </div>
            </div>
          </div>
        </div>

        <div className="button-row" style={{ marginTop: '1rem' }}>
          <button className="primary" onClick={saveAdminSettings} disabled={adminSaveState === 'running' || !platformSettings || misApiBaseInvalid}>
            <Save size={15} />{' '}
            {adminSaveState === 'running' ? 'Saving…'
              : adminSaveState === 'saved' ? 'Saved ✓'
              : adminSaveState === 'failed' ? 'Retry save'
              : 'Save admin settings'}
          </button>
          <span style={{ fontSize: '0.78rem', color: 'var(--muted)' }}>
            Note: MIS API base and phrase defaults require a bot worker restart to take effect.
          </span>
        </div>
        {adminSaveState === 'failed' && (
          <p style={{ fontSize: '0.82rem', color: 'var(--danger)', marginTop: '0.4rem' }}>
            Save failed — check the diagnostics bar above for details, then retry.
          </p>
        )}
      </div>
    </section>
  );
}
