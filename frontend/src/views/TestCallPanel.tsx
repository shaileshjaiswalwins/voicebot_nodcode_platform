import React, { useState, useEffect } from 'react';
import {
  AlertTriangle, Bot, ChevronRight, Database, Mic, PhoneCall,
  Play, Rocket, Square, Volume2, Wifi
} from 'lucide-react';
import type { Bot as BotType, BotVersion, RuntimeSettings } from '../api';
import { api } from '../api';
import type { TestForm } from '../types';
import { deriveAgentState } from '../utils/config';
import { shortId, titleCase } from '../utils/formatting';
import { ConnectionLine } from '../components/ConnectionLine';
import { LiveKitTestSession } from '../components/LiveKitTestSession';
import { PromptPreview } from './BuilderView';

function Step({ title, text }: { title: string; text: string }) {
  return <div className="step"><strong>{title}</strong><p>{text}</p></div>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

export function titleFor(view: import('../types').View) {
  return {
    bots: 'Agents',
    builder: 'Agents',
    flow: 'Flow Builder',
    campaigns: 'Campaigns',
    phone_numbers: 'Phone Numbers',
    test: 'WebRTC Test Call',
    transcripts: 'Transcripts',
    analytics: 'Analytics',
    observability: 'Observability',
    library: 'Phrase Library',
    settings: 'Settings',
    audit_log: 'Audit Log',
    admin: 'Admin'
  }[view];
}

export function subtitleFor(view: import('../types').View) {
  return {
    bots: 'Manage, edit, publish, and safely delete voice agents.',
    builder: 'Manage, edit, publish, and safely delete voice agents.',
    flow: 'Design the conversation as a visual graph — named nodes, conditionals, and transfers.',
    campaigns: 'Connect one bot to one campaign and its lead/callback APIs.',
    phone_numbers: 'Map real phone numbers to bots and environments for inbound routing.',
    test: 'Start a controlled browser call with helpful connection diagnostics.',
    transcripts: 'Inspect raw call transcripts, outcomes, and config snapshots.',
    analytics: 'Outcome aggregation, quality alerts, and call performance trends.',
    observability: 'Track LiveKit health, Gemini latency, TTFW, and callback failures.',
    library: 'Edit voicemail, hold-music, and DNC trigger phrases without a code deploy.',
    settings: 'Control LiveKit routing and dashboard runtime options.',
    audit_log: 'Every admin mutation, recorded with who did it and when.',
    admin: 'Configure LLM/STT/TTS model pricing (₹/min), reflected across the platform.'
  }[view];
}

export function TestCallPanel({
  bots,
  selectedBot,
  selectedBotId,
  onSelectBot,
  runtimeSettings,
  form,
  setForm,
  status,
  error,
  closeNote,
  chatMessage,
  setChatMessage,
  micEnabled,
  roomName,
  remoteAudioReady,
  livekitUrl,
  livekitToken,
  onStart,
  onStop,
  onLiveKitConnected,
  onLiveKitDisconnected,
  onLiveKitError,
  onMicChange,
  onAudioReady
}: {
  bots: BotType[];
  selectedBot?: BotType;
  selectedBotId: string;
  onSelectBot: (botId: string) => void;
  runtimeSettings: RuntimeSettings | null;
  form: TestForm;
  setForm: React.Dispatch<React.SetStateAction<TestForm>>;
  status: string;
  error: string;
  closeNote: string;
  chatMessage: string;
  setChatMessage: (value: string) => void;
  micEnabled: boolean;
  roomName: string;
  remoteAudioReady: boolean;
  livekitUrl: string;
  livekitToken: string;
  onStart: () => void;
  onStop: () => void;
  onLiveKitConnected: () => void;
  onLiveKitDisconnected: () => void;
  onLiveKitError: (err: Error) => void;
  onMicChange: (enabled: boolean) => void;
  onAudioReady: (ready: boolean) => void;
}) {
  const [botVersions, setBotVersions] = useState<BotVersion[]>([]);
  const [versionsError, setVersionsError] = useState('');

  function loadVersions() {
    if (!selectedBot) { setBotVersions([]); return; }
    setVersionsError('');
    api.bot(selectedBot._id)
      .then((bundle) => {
        const sorted = bundle.versions; // already sorted desc by version
        setBotVersions(sorted);
        // Auto-select active published version; fall back to latest draft
        const active = sorted.find((v) => v._id === bundle.bot.active_version_id);
        const defaultV = active || sorted.find((v) => v.state === 'draft') || sorted[0];
        if (defaultV) {
          setForm((prev) => ({ ...prev, test_bot_version_id: defaultV._id }));
        }
      })
      .catch((err) => {
        setBotVersions([]);
        setVersionsError(err instanceof Error ? err.message : 'Failed to load bot versions.');
      });
  }

  useEffect(() => {
    loadVersions();
  }, [selectedBot?._id]);

  function updateField(key: keyof TestForm, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  const defaultWorker = runtimeSettings?.livekit_agent_name || 'voice-bot-justdial';
  const effectiveWorker = form.test_worker_agent_name || defaultWorker;
  const agentState = deriveAgentState(status, remoteAudioReady, Boolean(roomName));
  const connected = Boolean(roomName) && status !== 'Idle' && !status.toLowerCase().includes('failed');

  const selectedVersion = botVersions.find((v) => v._id === form.test_bot_version_id);

  return (
    <section className="test-grid">
      <div className="panel">
        <div className="panel-header">
          <div>
            <h2>{selectedBot ? `Test ${selectedBot.name}` : 'Select an agent to test'}</h2>
            <p>Generate the room, connect browser microphone, and dispatch the agent automatically.</p>
          </div>
          <PhoneCall size={20} />
        </div>
        <div className="test-context">
          <div>
            <span>Agent</span>
            <select value={selectedBotId} onChange={(event) => onSelectBot(event.target.value)}>
              {bots.map((bot) => <option key={bot._id} value={bot._id}>{bot.name}</option>)}
            </select>
            <small>{selectedBot?.assistant_id || 'Select an agent to test'}</small>
          </div>
          <ChevronRight size={18} />
          <div>
            <span>Version to test</span>
            <select
              value={form.test_bot_version_id}
              onChange={(e) => updateField('test_bot_version_id', e.target.value)}
              disabled={botVersions.length === 0}
            >
              {botVersions.length === 0 && (
                <option value="">{versionsError ? 'Failed to load' : !selectedBot ? 'Select an agent first' : 'Loading…'}</option>
              )}
              {botVersions.map((v) => (
                <option key={v._id} value={v._id}>
                  v{v.version} — {v.state === 'published' ? '✓ Published' : '✏ Draft'}
                </option>
              ))}
            </select>
            <small>
              {versionsError
                ? versionsError
                : selectedVersion
                  ? selectedVersion.state === 'published'
                    ? `Active published version`
                    : `Draft — not yet live in production`
                  : 'No versions found'}
            </small>
            {versionsError && <button className="fallback-button" style={{ marginTop: '0.35rem' }} onClick={loadVersions}>Retry</button>}
          </div>
          <ChevronRight size={18} />
          <div>
            <span>LiveKit worker</span>
            <strong>{effectiveWorker}</strong>
            <small>Override below if needed.</small>
          </div>
        </div>
        <div className="form-grid">
          <label>Campaign ID<input value={form.campaign_id} onChange={(event) => updateField('campaign_id', event.target.value)} /></label>
          <label>Lead ID<input value={form.lead_id} onChange={(event) => updateField('lead_id', event.target.value)} placeholder="optional for local test" /></label>
          <label>Call ID<input value={form.call_id} onChange={(event) => updateField('call_id', event.target.value)} /></label>
          <label>Mobile<input value={form.mobile} onChange={(event) => updateField('mobile', event.target.value)} placeholder="test number" /></label>
          <label>Product / Search Term<input value={form.srchterm} onChange={(event) => updateField('srchterm', event.target.value)} /></label>
          <label>Buyer Name<input value={form.buyer_name} onChange={(event) => updateField('buyer_name', event.target.value)} /></label>
          <label>City<input value={form.city} onChange={(event) => updateField('city', event.target.value)} /></label>
          <label>
            Worker agent name for this test
            <input
              value={form.test_worker_agent_name}
              onChange={(event) => updateField('test_worker_agent_name', event.target.value)}
              placeholder={defaultWorker}
            />
          </label>
        </div>
        <div className="quick-actions">
          <button onClick={() => updateField('test_worker_agent_name', 'voice-bot-justdial-dashboard')}>Use safe test worker</button>
          <button onClick={() => updateField('test_worker_agent_name', defaultWorker)}>Use saved default</button>
          <button onClick={() => setForm((current) => ({ ...current, call_id: `TEST-${Date.now()}` }))}>New call ID</button>
        </div>
        <details style={{ marginTop: '0.5rem' }}>
          <summary style={{ fontSize: '0.83rem', fontWeight: 600, cursor: 'pointer', padding: '0.3rem 0', userSelect: 'none' }}>
            Custom lead data (advanced)
          </summary>
          <div style={{ marginTop: '0.5rem' }}>
            <label>
              Lead JSON override
              <textarea
                rows={5}
                value={form.custom_lead_json}
                onChange={(e) => updateField('custom_lead_json', e.target.value)}
                placeholder={'{\n  "is_business": true,\n  "qualification": "premium"\n}'}
                style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}
              />
              <small>Merged into the lead record sent to the bot. Use for edge-case testing (is_business, specific qualification fields, etc.).</small>
            </label>
            {form.custom_lead_json && (() => {
              try { JSON.parse(form.custom_lead_json); return <div style={{ fontSize: '0.78rem', color: '#15803d', marginTop: '2px' }}>✓ Valid JSON</div>; }
              catch { return <div style={{ fontSize: '0.78rem', color: '#b91c1c', marginTop: '2px' }}>✗ Invalid JSON — fix before starting</div>; }
            })()}
          </div>
        </details>
        <div className="button-row">
          <button className={error ? 'fallback-button' : 'primary'} onClick={onStart} disabled={!selectedBot || status.includes('Creating') || status.includes('Connecting')}>
            <Play size={16} /> {error ? 'Fallback: retry setup' : status.includes('Creating') || status.includes('Connecting') ? 'Starting...' : 'Start WebRTC test'}
          </button>
          <button onClick={onStop}><Square size={16} /> End test</button>
        </div>
      </div>
      <div className="panel status-panel">
        <div className="live-session-card">
          <div className="live-session-header">
            <span className={`session-dot ${agentState}`} />
            <div>
              <h2>Live session</h2>
              <p>{titleCase(agentState)} · {status}</p>
            </div>
          </div>
          <LiveKitTestSession
            serverUrl={livekitUrl}
            token={livekitToken}
            connected={connected}
            status={status}
            chatMessage={chatMessage}
            setChatMessage={setChatMessage}
            onConnected={onLiveKitConnected}
            onDisconnected={onLiveKitDisconnected}
            onError={onLiveKitError}
            onDisconnectRequested={onStop}
            onMicChange={onMicChange}
            onAudioReady={onAudioReady}
          />
          <div className="session-meta-grid">
            <Metric label="Room" value={roomName ? shortId(roomName) : 'not created'} />
            <Metric label="Worker" value={effectiveWorker} />
            <Metric label="Mic" value={micEnabled ? 'live' : 'muted'} />
            <Metric label="Bot audio" value={remoteAudioReady ? 'connected' : 'waiting'} />
          </div>
          {closeNote && <p className="session-close-note">{closeNote}</p>}
        </div>
        <h2>Connection checklist</h2>
        <ConnectionLine icon={<Database />} label="Backend room" value={roomName || 'Not created'} done={Boolean(roomName)} />
        <ConnectionLine icon={<Bot />} label="Dispatched worker" value={effectiveWorker} done={Boolean(effectiveWorker)} />
        <ConnectionLine
          icon={<Rocket />}
          label="Bot version"
          value={selectedVersion ? `v${selectedVersion.version} (${selectedVersion.state})` : '—'}
          done={Boolean(selectedVersion)}
        />
        <ConnectionLine icon={<Wifi />} label="LiveKit socket" value={status} done={!status.toLowerCase().includes('failed') && status !== 'Idle'} />
        <ConnectionLine icon={<Mic />} label="Microphone" value={status.includes('Microphone') || remoteAudioReady ? 'Requested' : 'Waiting'} done={status.includes('Microphone') || remoteAudioReady} />
        <ConnectionLine icon={<Volume2 />} label="Bot audio" value={remoteAudioReady ? 'Connected' : 'Waiting'} done={remoteAudioReady} />
        {selectedVersion && <PromptPreview version={selectedVersion} srchterm={form.srchterm} />}
        {error && (
          <div className="notice error test-error">
            <AlertTriangle size={16} />
            <div>
              <strong>{error}</strong>
              <span>Fallback: keep the entered metadata, retry room setup, or end this test and continue editing the bot.</span>
              <div className="button-row">
                <button className="fallback-button" onClick={onStart}>Retry room setup</button>
                <button onClick={onStop}>Skip test for now</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
