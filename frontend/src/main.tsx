import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity,
  Bot,
  Braces,
  FileText,
  Gauge,
  Megaphone,
  Mic,
  Play,
  Rocket,
  Save
} from 'lucide-react';
import { api, Bot as BotType, BotVersion, Transcript } from './api';
import './styles.css';

type View = 'bots' | 'builder' | 'campaigns' | 'test' | 'transcripts' | 'observability';

const defaultConfig = {
  assistant_id: 'e8c0fd31-2d60-4531-a029-2047b17988c4',
  model: 'gemini-3.1-flash-live-preview',
  voice: 'Aoede',
  language: 'hindi',
  livekit_language: 'hi-IN',
  temperature: 0.7,
  max_call_duration: 300,
  system_prompt: 'You are Simran, a warm JustDial call center agent.',
  function_calling: true
};

function App() {
  const [view, setView] = useState<View>('bots');
  const [bots, setBots] = useState<BotType[]>([]);
  const [selectedBotId, setSelectedBotId] = useState('');
  const [versions, setVersions] = useState<BotVersion[]>([]);
  const [configText, setConfigText] = useState(JSON.stringify(defaultConfig, null, 2));
  const [transcripts, setTranscripts] = useState<Transcript[]>([]);
  const [message, setMessage] = useState('');

  const selectedBot = useMemo(
    () => bots.find((bot) => bot._id === selectedBotId) || bots[0],
    [bots, selectedBotId]
  );

  async function refresh() {
    const nextBots = await api.bots();
    setBots(nextBots);
    if (!selectedBotId && nextBots[0]) setSelectedBotId(nextBots[0]._id);
    setTranscripts(await api.transcripts());
  }

  useEffect(() => {
    refresh().catch((error) => setMessage(error.message));
  }, []);

  useEffect(() => {
    if (!selectedBot) return;
    api.bot(selectedBot._id)
      .then((bundle) => {
        setVersions(bundle.versions);
        const preferred = bundle.versions.find((item) => item.state === 'draft') || bundle.versions[0];
        if (preferred) setConfigText(JSON.stringify(preferred.config, null, 2));
      })
      .catch((error) => setMessage(error.message));
  }, [selectedBot?._id]);

  async function createBot() {
    const bot = await api.createBot({
      name: 'New Voice Bot',
      description: 'Prompt and settings based outbound bot',
      config: defaultConfig
    });
    setSelectedBotId(bot._id);
    setMessage('Draft bot created');
    await refresh();
  }

  async function saveDraft() {
    if (!selectedBot) return;
    const config = JSON.parse(configText);
    const version = await api.saveDraft(selectedBot._id, { config, notes: 'Dashboard draft save' });
    setMessage(`Draft version ${version.version} saved`);
    await refresh();
  }

  async function publishDraft() {
    if (!selectedBot) return;
    const draft = versions.find((item) => item.state === 'draft');
    const version = await api.publish(selectedBot._id, draft?._id);
    setMessage(`Published version ${version.version}`);
    await refresh();
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Mic size={22} />
          <div>
            <strong>JustDial Voice AI</strong>
            <span>No-code Platform</span>
          </div>
        </div>
        <NavButton icon={<Bot />} label="Bots" active={view === 'bots'} onClick={() => setView('bots')} />
        <NavButton icon={<Braces />} label="Builder" active={view === 'builder'} onClick={() => setView('builder')} />
        <NavButton icon={<Megaphone />} label="Campaigns" active={view === 'campaigns'} onClick={() => setView('campaigns')} />
        <NavButton icon={<Play />} label="Test Call" active={view === 'test'} onClick={() => setView('test')} />
        <NavButton icon={<FileText />} label="Transcripts" active={view === 'transcripts'} onClick={() => setView('transcripts')} />
        <NavButton icon={<Gauge />} label="Observability" active={view === 'observability'} onClick={() => setView('observability')} />
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <h1>{titleFor(view)}</h1>
            <p>{subtitleFor(view)}</p>
          </div>
          <div className="actions">
            <button onClick={refresh}><Activity size={16} /> Refresh</button>
            <button className="primary" onClick={createBot}><Rocket size={16} /> New Bot</button>
          </div>
        </header>

        {message && <div className="notice">{message}</div>}

        {view === 'bots' && (
          <section className="table-panel">
            <table>
              <thead>
                <tr><th>Name</th><th>Status</th><th>Assistant ID</th><th>Active Version</th><th>Owner</th></tr>
              </thead>
              <tbody>
                {bots.map((bot) => (
                  <tr key={bot._id} onClick={() => { setSelectedBotId(bot._id); setView('builder'); }}>
                    <td>{bot.name}</td>
                    <td><span className="pill">{bot.status}</span></td>
                    <td>{bot.assistant_id}</td>
                    <td>{bot.active_version_id || '-'}</td>
                    <td>{bot.owner || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {view === 'builder' && (
          <section className="builder-grid">
            <div className="panel">
              <h2>{selectedBot?.name || 'Select a bot'}</h2>
              <p className="muted">Edits create drafts. Published versions are immutable for live-call safety.</p>
              <textarea value={configText} onChange={(event) => setConfigText(event.target.value)} spellCheck={false} />
              <div className="actions">
                <button onClick={saveDraft}><Save size={16} /> Save Draft</button>
                <button className="primary" onClick={publishDraft}><Rocket size={16} /> Publish</button>
              </div>
            </div>
            <div className="panel">
              <h2>Versions</h2>
              {versions.map((version) => (
                <div className="version-row" key={version._id}>
                  <span>v{version.version}</span>
                  <strong>{version.state}</strong>
                  <small>{version.published_at || 'not published'}</small>
                </div>
              ))}
            </div>
          </section>
        )}

        {view === 'campaigns' && <Placeholder title="Campaign Mapping" lines={['Map each outbound campaign to a bot.', 'Point it to the existing lead API.', 'Dispatch room metadata with assistant_id, campaign_id, lead_id and call_id.']} />}
        {view === 'test' && <Placeholder title="Controlled Test Call" lines={['Generate LiveKit room metadata for a sample lead.', 'Place a test outbound call through the in-house dialer.', 'Open the transcript after the call completes.']} />}
        {view === 'observability' && <Placeholder title="Operations" lines={['Langfuse traces show call lifecycle and model latency.', 'LiveKit monitor tracks active rooms and participants.', 'Alerts focus on no greeting, high first-word latency, Gemini errors and callback failures.']} />}

        {view === 'transcripts' && (
          <section className="table-panel">
            <table>
              <thead>
                <tr><th>Call ID</th><th>Lead</th><th>Status</th><th>Duration</th><th>Turns</th><th>Created</th></tr>
              </thead>
              <tbody>
                {transcripts.map((item) => (
                  <tr key={item._id}>
                    <td>{item.call_id || '-'}</td>
                    <td>{item.lead_id || '-'}</td>
                    <td><span className="pill">{item.status || '-'}</span></td>
                    <td>{item.call_duration_sec || 0}s</td>
                    <td>{item.transcript?.length || 0}</td>
                    <td>{item.created_at || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
      </main>
    </div>
  );
}

function NavButton({ icon, label, active, onClick }: { icon: React.ReactNode; label: string; active: boolean; onClick: () => void }) {
  return <button className={`nav-button ${active ? 'active' : ''}`} onClick={onClick}>{icon}<span>{label}</span></button>;
}

function Placeholder({ title, lines }: { title: string; lines: string[] }) {
  return (
    <section className="panel placeholder">
      <h2>{title}</h2>
      {lines.map((line) => <p key={line}>{line}</p>)}
    </section>
  );
}

function titleFor(view: View) {
  return {
    bots: 'Bots',
    builder: 'Bot Builder',
    campaigns: 'Campaigns',
    test: 'Test Call',
    transcripts: 'Transcripts',
    observability: 'Observability'
  }[view];
}

function subtitleFor(view: View) {
  return {
    bots: 'Manage outbound voice bots and active versions.',
    builder: 'Edit prompt and runtime settings safely.',
    campaigns: 'Connect bots to outbound lead APIs.',
    test: 'Prepare controlled calls before publishing.',
    transcripts: 'Search calls, outcomes and saved config snapshots.',
    observability: 'Track LiveKit health and Gemini latency.'
  }[view];
}

createRoot(document.getElementById('root')!).render(<App />);
