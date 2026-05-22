import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  createLocalAudioTrack,
  LocalAudioTrack,
  Room,
  RoomEvent,
  Track
} from 'livekit-client';
import {
  AlertTriangle,
  Activity,
  Bot,
  Braces,
  FileText,
  Gauge,
  Megaphone,
  Mic,
  Play,
  Rocket,
  Save,
  Square
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
  const [testForm, setTestForm] = useState({
    campaign_id: 'test',
    lead_id: '',
    call_id: `TEST-${Date.now()}`,
    mobile: '',
    srchterm: 'air conditioner',
    buyer_name: 'Test User',
    city: 'Mumbai'
  });
  const [testStatus, setTestStatus] = useState('Idle');
  const [testError, setTestError] = useState('');
  const [testRoomName, setTestRoomName] = useState('');
  const [remoteAudioReady, setRemoteAudioReady] = useState(false);
  const livekitRoomRef = useRef<Room | null>(null);
  const localTrackRef = useRef<LocalAudioTrack | null>(null);
  const remoteAudioRef = useRef<HTMLDivElement | null>(null);

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

  async function startWebRtcTest() {
    if (!selectedBot) {
      setTestError('Select a bot before starting a test call.');
      return;
    }
    setTestError('');
    setRemoteAudioReady(false);
    setTestStatus('Creating LiveKit room...');
    try {
      await stopWebRtcTest();
      const session = await api.createWebRtcTestSession(selectedBot._id, testForm);
      setTestRoomName(session.room_name);
      setTestStatus('Connecting browser to LiveKit...');

      const room = new Room({
        adaptiveStream: true,
        dynacast: true
      });
      livekitRoomRef.current = room;

      room.on(RoomEvent.Connected, () => setTestStatus('Connected. Waiting for bot audio...'));
      room.on(RoomEvent.Disconnected, () => setTestStatus('Disconnected'));
      room.on(RoomEvent.Reconnecting, () => setTestStatus('Reconnecting to LiveKit...'));
      room.on(RoomEvent.Reconnected, () => setTestStatus('Reconnected. Continue testing.'));
      room.on(RoomEvent.ParticipantConnected, (participant) => {
        setTestStatus(`Participant joined: ${participant.identity}`);
      });
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind !== Track.Kind.Audio || !remoteAudioRef.current) return;
        const element = track.attach();
        element.autoplay = true;
        remoteAudioRef.current.appendChild(element);
        setRemoteAudioReady(true);
        setTestStatus('Bot audio connected. Speak into your microphone.');
      });
      room.on(RoomEvent.MediaDevicesError, (error) => {
        setTestError(`Microphone permission/device error: ${error.message}`);
      });

      await room.connect(session.livekit_url, session.token);
      setTestStatus('Requesting microphone permission...');
      const micTrack = await createLocalAudioTrack({
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      });
      localTrackRef.current = micTrack;
      await room.localParticipant.publishTrack(micTrack);
      setTestStatus('Microphone is live. Waiting for the bot to respond...');
    } catch (error) {
      await stopWebRtcTest();
      setTestError(friendlyTestError(error));
      setTestStatus('Failed to start test');
    }
  }

  async function stopWebRtcTest() {
    localTrackRef.current?.stop();
    localTrackRef.current = null;
    if (remoteAudioRef.current) {
      remoteAudioRef.current.innerHTML = '';
    }
    if (livekitRoomRef.current) {
      livekitRoomRef.current.disconnect();
      livekitRoomRef.current = null;
    }
    setRemoteAudioReady(false);
    setTestStatus('Idle');
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
        {view === 'test' && (
          <TestCallPanel
            selectedBot={selectedBot}
            form={testForm}
            setForm={setTestForm}
            status={testStatus}
            error={testError}
            roomName={testRoomName}
            remoteAudioReady={remoteAudioReady}
            remoteAudioRef={remoteAudioRef}
            onStart={startWebRtcTest}
            onStop={stopWebRtcTest}
          />
        )}
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

function friendlyTestError(error: unknown) {
  const raw = error instanceof Error ? error.message : String(error);
  if (raw.includes('LiveKit is not configured')) {
    return 'LiveKit is not configured on the backend. Ask backend/infra to set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET in .env, then restart ./start_api.sh.';
  }
  if (raw.includes('Could not create LiveKit test room')) {
    return 'Backend could not create the LiveKit room. Check LiveKit server URL, API credentials, and whether the LiveKit server is reachable from this machine.';
  }
  if (raw.toLowerCase().includes('permission') || raw.toLowerCase().includes('microphone')) {
    return 'Browser microphone access failed. Allow microphone permission, check the selected input device, then try again.';
  }
  if (raw.toLowerCase().includes('websocket') || raw.toLowerCase().includes('network')) {
    return 'Browser could not connect to LiveKit. Check LIVEKIT_URL is reachable from your browser and uses ws/wss correctly.';
  }
  return raw || 'Unknown test call error. Check backend logs and LiveKit server status.';
}

function TestCallPanel({
  selectedBot,
  form,
  setForm,
  status,
  error,
  roomName,
  remoteAudioReady,
  remoteAudioRef,
  onStart,
  onStop
}: {
  selectedBot?: BotType;
  form: Record<string, string>;
  setForm: React.Dispatch<React.SetStateAction<{
    campaign_id: string;
    lead_id: string;
    call_id: string;
    mobile: string;
    srchterm: string;
    buyer_name: string;
    city: string;
  }>>;
  status: string;
  error: string;
  roomName: string;
  remoteAudioReady: boolean;
  remoteAudioRef: React.RefObject<HTMLDivElement>;
  onStart: () => void;
  onStop: () => void;
}) {
  function updateField(key: string, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  return (
    <section className="test-grid">
      <div className="panel">
        <h2>{selectedBot ? `Test ${selectedBot.name}` : 'Select a bot to test'}</h2>
        <p className="muted">This creates a LiveKit room, connects your browser microphone, and dispatches the voicebot agent into the same room.</p>
        <div className="form-grid">
          <label>Campaign ID<input value={form.campaign_id} onChange={(event) => updateField('campaign_id', event.target.value)} /></label>
          <label>Lead ID<input value={form.lead_id} onChange={(event) => updateField('lead_id', event.target.value)} /></label>
          <label>Call ID<input value={form.call_id} onChange={(event) => updateField('call_id', event.target.value)} /></label>
          <label>Mobile<input value={form.mobile} onChange={(event) => updateField('mobile', event.target.value)} /></label>
          <label>Product / Search Term<input value={form.srchterm} onChange={(event) => updateField('srchterm', event.target.value)} /></label>
          <label>Buyer Name<input value={form.buyer_name} onChange={(event) => updateField('buyer_name', event.target.value)} /></label>
          <label>City<input value={form.city} onChange={(event) => updateField('city', event.target.value)} /></label>
        </div>
        <div className="actions">
          <button className="primary" onClick={onStart} disabled={!selectedBot}><Play size={16} /> Start WebRTC Test</button>
          <button onClick={onStop}><Square size={16} /> End Test</button>
        </div>
      </div>
      <div className="panel status-panel">
        <h2>Connection</h2>
        <div className="status-line"><span>Status</span><strong>{status}</strong></div>
        <div className="status-line"><span>Room</span><strong>{roomName || '-'}</strong></div>
        <div className="status-line"><span>Bot Audio</span><strong>{remoteAudioReady ? 'Connected' : 'Waiting'}</strong></div>
        <div ref={remoteAudioRef} />
        {error && <div className="error-box"><AlertTriangle size={16} /> {error}</div>}
      </div>
    </section>
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
