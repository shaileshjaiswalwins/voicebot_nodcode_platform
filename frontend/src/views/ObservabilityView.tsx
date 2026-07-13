import React from 'react';
import { Activity, AlertTriangle } from 'lucide-react';
import type { Bot as BotType, LangfuseSettings, Transcript } from '../api';
import { StatusPill } from '../components/StatusPill';

function Step({ title, text }: { title: string; text: string }) {
  return <div className="step"><strong>{title}</strong><p>{text}</p></div>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

export function ObservabilityView({
  selectedBot,
  transcripts,
  langfuseSettings,
  onUpdateLangfuse
}: {
  selectedBot?: BotType;
  transcripts: Transcript[];
  langfuseSettings: LangfuseSettings | null;
  onUpdateLangfuse: (payload: Partial<LangfuseSettings>) => void;
}) {
  const errored = transcripts.filter((item) => item.status && item.status !== 'completed').length;
  const langfuseEnabled = Boolean(langfuseSettings?.enabled);
  const credentialsReady = Boolean(langfuseSettings?.credentials_configured);
  return (
    <section className="observability-layout">
      <div className="panel observability-control">
        <div className="panel-header">
          <div>
            <h2>Langfuse tracing</h2>
            <p>Control whether calls send traces, prompts, and raw transcripts to Langfuse Cloud US.</p>
          </div>
          <StatusPill value={langfuseEnabled ? 'enabled' : 'disabled'} />
        </div>
        <div className="toggle-row">
          <div>
            <strong>Send traces to Langfuse</strong>
            <span>{credentialsReady ? 'Credentials configured on backend' : 'Credentials missing in backend .env'}</span>
          </div>
          <button
            className={langfuseEnabled ? '' : 'primary'}
            onClick={() => onUpdateLangfuse({ enabled: !langfuseEnabled })}
          >
            {langfuseEnabled ? 'Disable' : 'Enable'}
          </button>
        </div>
        <div className="form-grid">
          <label>
            Environment
            <select
              value={langfuseSettings?.environment || 'local'}
              onChange={(event) => onUpdateLangfuse({ environment: event.target.value as LangfuseSettings['environment'] })}
            >
              <option value="local">Local</option>
              <option value="staging">Staging</option>
              <option value="prod">Prod</option>
            </select>
          </label>
          <label>
            Langfuse URL
            <input value={langfuseSettings?.base_url || 'Not configured'} disabled />
          </label>
        </div>
        <div className="check-grid">
          <label className="checkbox-line">
            <input
              type="checkbox"
              checked={Boolean(langfuseSettings?.send_transcripts)}
              onChange={(event) => onUpdateLangfuse({ send_transcripts: event.target.checked })}
            />
            Send raw transcripts
          </label>
          <label className="checkbox-line">
            <input
              type="checkbox"
              checked={Boolean(langfuseSettings?.send_prompts)}
              onChange={(event) => onUpdateLangfuse({ send_prompts: event.target.checked })}
            />
            Send prompts/config snapshots
          </label>
        </div>
        {langfuseSettings?.runtime_status?.last_error && (
          <div className="notice error"><AlertTriangle size={16} /> {langfuseSettings.runtime_status.last_error}</div>
        )}
      </div>

      <div className="content-grid two-col">
        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>Production observability</h2>
              <p>Langfuse receives call lifecycle, Gemini latency, first-word latency, tool latency, and callback status.</p>
            </div>
            <Activity size={20} />
          </div>
          <div className="metric-board">
            <Metric label="Selected agent" value={selectedBot?.name || '-'} />
            <Metric label="Trace backend" value="Langfuse Cloud US" />
            <Metric label="LiveKit monitor" value="Standalone container" />
            <Metric label="Non-completed calls" value={errored.toString()} />
          </div>
        </div>
        <div className="panel">
          <h2>Call timeline</h2>
          <div className="timeline">
            <Step title="call_started" text="Room created and participant metadata received." />
            <Step title="first_audio_received" text="Caller audio detected by runtime." />
            <Step title="first_model_response" text="Gemini starts responding." />
            <Step title="transcript_saved" text="Mongo document stores transcript and config snapshot." />
            <Step title="callback_sent" text="Campaign callback mapping completes." />
            <Step title="call_ended" text="Final call status and duration are flushed to Langfuse." />
          </div>
        </div>
      </div>
    </section>
  );
}
