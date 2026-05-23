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
  Activity,
  AlertCircle,
  AlertTriangle,
  Bot,
  Braces,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  FileText,
  Gauge,
  Headphones,
  Megaphone,
  Mic,
  PhoneCall,
  Play,
  RefreshCw,
  Rocket,
  Save,
  Search,
  ShieldCheck,
  Square,
  Volume2,
  Wand2,
  Wifi
} from 'lucide-react';
import {
  api,
  Bot as BotType,
  BotVersion,
  Campaign,
  LangfuseSettings,
  LanguageOption,
  Transcript,
  VoiceOption
} from './api';
import './styles.css';

type View = 'bots' | 'builder' | 'campaigns' | 'test' | 'transcripts' | 'observability';

type RuntimeConfig = {
  assistant_id?: string;
  model?: string;
  voice?: string;
  language?: string;
  livekit_language?: string;
  sarvam_language?: string;
  temperature?: number;
  max_call_duration?: number;
  system_prompt?: string;
  initial_message?: string;
  call_end_text?: string;
  function_calling?: boolean;
  gemini_silence_duration_ms?: number;
  gemini_prefix_padding_ms?: number;
  post_speech_hold_ms?: number;
  prompt_config?: Record<string, unknown>;
  functions?: unknown[];
  [key: string]: unknown;
};

type TestForm = {
  campaign_id: string;
  lead_id: string;
  call_id: string;
  mobile: string;
  srchterm: string;
  buyer_name: string;
  city: string;
};

const defaultConfig: RuntimeConfig = {
  assistant_id: 'e8c0fd31-2d60-4531-a029-2047b17988c4',
  model: 'gemini-3.1-flash-live-preview',
  voice: 'Aoede',
  language: 'hindi',
  livekit_language: 'hi-IN',
  temperature: 0.7,
  max_call_duration: 300,
  system_prompt: 'You are Simran, a warm JustDial call center agent.',
  initial_message: 'हेलो, मैं Simran बोल रही हूँ Justdial से — आपको {product} की requirement है ना?',
  call_end_text: 'ठीक है जी, सारी details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए शुक्रिया.',
  function_calling: true,
  gemini_silence_duration_ms: 1800,
  gemini_prefix_padding_ms: 300,
  post_speech_hold_ms: 800
};

function App() {
  const [view, setView] = useState<View>('bots');
  const [bots, setBots] = useState<BotType[]>([]);
  const [selectedBotId, setSelectedBotId] = useState('');
  const [versions, setVersions] = useState<BotVersion[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [voices, setVoices] = useState<VoiceOption[]>([]);
  const [languages, setLanguages] = useState<LanguageOption[]>([]);
  const [langfuseSettings, setLangfuseSettings] = useState<LangfuseSettings | null>(null);
  const [configText, setConfigText] = useState(JSON.stringify(defaultConfig, null, 2));
  const [transcripts, setTranscripts] = useState<Transcript[]>([]);
  const [selectedTranscriptId, setSelectedTranscriptId] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [testForm, setTestForm] = useState<TestForm>({
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

  const selectedTranscript = useMemo(
    () => transcripts.find((item) => item._id === selectedTranscriptId) || transcripts[0],
    [transcripts, selectedTranscriptId]
  );

  const parsedConfig = useMemo(() => parseConfig(configText), [configText]);
  const activeVersion = versions.find((item) => item._id === selectedBot?.active_version_id);
  const latestDraft = versions.find((item) => item.state === 'draft');
  const publishedCount = versions.filter((item) => item.state === 'published').length;
  const filteredTranscripts = useMemo(() => {
    const needle = searchText.trim().toLowerCase();
    if (!needle) return transcripts;
    return transcripts.filter((item) => {
      const text = [
        item.call_id,
        item.lead_id,
        item.campaign_id,
        item.status,
        item.transcript?.map((turn) => turn.text).join(' ')
      ].join(' ').toLowerCase();
      return text.includes(needle);
    });
  }, [searchText, transcripts]);

  async function refresh() {
    setLoading(true);
    setMessage('');
    try {
      const [nextBots, nextTranscripts, nextCampaigns, nextVoices, nextLanguages, nextLangfuse] = await Promise.all([
        api.bots(),
        api.transcripts(),
        api.campaigns(),
        api.voices(),
        api.languages(),
        api.langfuseSettings()
      ]);
      setBots(nextBots);
      setTranscripts(nextTranscripts);
      setCampaigns(nextCampaigns);
      setVoices(nextVoices);
      setLanguages(nextLanguages);
      setLangfuseSettings(nextLangfuse);
      if (!selectedBotId && nextBots[0]) setSelectedBotId(nextBots[0]._id);
      if (!selectedTranscriptId && nextTranscripts[0]) setSelectedTranscriptId(nextTranscripts[0]._id);
    } catch (error) {
      setMessage(friendlyApiError(error));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    if (!selectedBot) return;
    api.bot(selectedBot._id)
      .then((bundle) => {
        setVersions(bundle.versions);
        const preferred =
          bundle.versions.find((item) => item.state === 'draft') ||
          bundle.versions.find((item) => item._id === bundle.bot.active_version_id) ||
          bundle.versions[0];
        if (preferred) setConfigText(JSON.stringify(preferred.config, null, 2));
      })
      .catch((error) => setMessage(friendlyApiError(error)));
  }, [selectedBot?._id]);

  async function createBot() {
    try {
      const bot = await api.createBot({
        name: 'New JustDial Voice Bot',
        description: 'Prompt and settings based outbound bot',
        orchestration: { mode: 'prompt_settings', flow_provider: null, flow_id: null },
        config: defaultConfig
      });
      setSelectedBotId(bot._id);
      setView('builder');
      setMessage('Draft bot created. Add prompt details, save draft, then publish.');
      await refresh();
    } catch (error) {
      setMessage(friendlyApiError(error));
    }
  }

  async function saveDraft() {
    if (!selectedBot || !parsedConfig.ok) return;
    try {
      const version = await api.saveDraft(selectedBot._id, {
        config: parsedConfig.value,
        notes: 'Dashboard draft save'
      });
      setMessage(`Draft version ${version.version} saved. Publish it when ready for new calls.`);
      await refresh();
    } catch (error) {
      setMessage(friendlyApiError(error));
    }
  }

  async function publishDraft() {
    if (!selectedBot) return;
    try {
      const version = await api.publish(selectedBot._id, latestDraft?._id);
      setMessage(`Published version ${version.version}. Live calls keep their old snapshot; new calls use this version.`);
      await refresh();
    } catch (error) {
      setMessage(friendlyApiError(error));
    }
  }

  async function updateLangfuse(payload: Partial<LangfuseSettings>) {
    try {
      const nextSettings = await api.updateLangfuseSettings(payload);
      setLangfuseSettings(nextSettings);
      setMessage(`Langfuse ${nextSettings.enabled ? 'enabled' : 'disabled'} for ${nextSettings.environment}.`);
    } catch (error) {
      setMessage(friendlyApiError(error));
    }
  }

  function updateConfig(key: keyof RuntimeConfig, value: unknown) {
    const current = parsedConfig.ok ? parsedConfig.value : defaultConfig;
    setConfigText(JSON.stringify({ ...current, [key]: value }, null, 2));
  }

  function updateLanguage(languageId: string) {
    const language = languages.find((item) => item.id === languageId);
    const current = parsedConfig.ok ? parsedConfig.value : defaultConfig;
    setConfigText(JSON.stringify({
      ...current,
      language: languageId,
      livekit_language: language?.livekit_code || current.livekit_language,
      sarvam_language: language?.sarvam_code || current.sarvam_language || language?.livekit_code
    }, null, 2));
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

      const room = new Room({ adaptiveStream: true, dynacast: true });
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
    if (remoteAudioRef.current) remoteAudioRef.current.innerHTML = '';
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
          <div className="brand-mark"><Mic size={19} /></div>
          <div>
            <strong>JustDial Voice AI</strong>
            <span>No-code operations</span>
          </div>
        </div>
        <div className="nav-group">
          <NavButton icon={<Bot />} label="Agents" active={view === 'bots'} onClick={() => setView('bots')} />
          <NavButton icon={<Braces />} label="Builder" active={view === 'builder'} onClick={() => setView('builder')} />
          <NavButton icon={<Megaphone />} label="Campaigns" active={view === 'campaigns'} onClick={() => setView('campaigns')} />
          <NavButton icon={<Play />} label="Test Call" active={view === 'test'} onClick={() => setView('test')} />
          <NavButton icon={<FileText />} label="Transcripts" active={view === 'transcripts'} onClick={() => setView('transcripts')} />
          <NavButton icon={<Gauge />} label="Observability" active={view === 'observability'} onClick={() => setView('observability')} />
        </div>
        <div className="sidebar-card">
          <span className="status-dot" />
          <strong>Mongo connected</strong>
          <small>ai_voice_bot_management</small>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <span className="eyebrow">Standalone platform</span>
            <h1>{titleFor(view)}</h1>
            <p>{subtitleFor(view)}</p>
          </div>
          <div className="topbar-actions">
            <select value={selectedBot?._id || ''} onChange={(event) => setSelectedBotId(event.target.value)}>
              {bots.map((bot) => <option key={bot._id} value={bot._id}>{bot.name}</option>)}
            </select>
            <button onClick={refresh}><RefreshCw size={16} /> Refresh</button>
            <button className="primary" onClick={createBot}><Rocket size={16} /> New Agent</button>
          </div>
        </header>

        {message && (
          <div className={message.startsWith('Cannot') || message.startsWith('API') ? 'notice error' : 'notice'}>
            <AlertCircle size={16} /> {message}
          </div>
        )}

        <KpiStrip bots={bots} transcripts={transcripts} campaigns={campaigns} loading={loading} />

        {view === 'bots' && (
          <BotsView
            bots={bots}
            selectedBot={selectedBot}
            transcripts={transcripts}
            onSelect={(botId) => { setSelectedBotId(botId); setView('builder'); }}
          />
        )}

        {view === 'builder' && (
          <BuilderView
            selectedBot={selectedBot}
            versions={versions}
            activeVersion={activeVersion}
            latestDraft={latestDraft}
            publishedCount={publishedCount}
            config={parsedConfig}
            configText={configText}
            voices={voices}
            languages={languages}
            onConfigTextChange={setConfigText}
            onUpdateConfig={updateConfig}
            onUpdateLanguage={updateLanguage}
            onSaveDraft={saveDraft}
            onPublish={publishDraft}
          />
        )}

        {view === 'campaigns' && (
          <CampaignsView campaigns={campaigns} bots={bots} />
        )}

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

        {view === 'transcripts' && (
          <TranscriptsView
            transcripts={filteredTranscripts}
            selectedTranscript={selectedTranscript}
            searchText={searchText}
            onSearchText={setSearchText}
            onSelect={setSelectedTranscriptId}
          />
        )}

        {view === 'observability' && (
          <ObservabilityView
            selectedBot={selectedBot}
            transcripts={transcripts}
            langfuseSettings={langfuseSettings}
            onUpdateLangfuse={updateLangfuse}
          />
        )}
      </main>
    </div>
  );
}

function KpiStrip({ bots, transcripts, campaigns, loading }: {
  bots: BotType[];
  transcripts: Transcript[];
  campaigns: Campaign[];
  loading: boolean;
}) {
  const activeBots = bots.filter((bot) => bot.status === 'active').length;
  const completedCalls = transcripts.filter((item) => item.status === 'completed').length;
  const avgDuration = average(transcripts.map((item) => Number(item.call_duration_sec || 0)).filter(Boolean));
  return (
    <section className="kpi-strip">
      <Kpi icon={<Bot />} label="Active agents" value={loading ? '...' : activeBots.toString()} helper={`${bots.length} total`} />
      <Kpi icon={<Megaphone />} label="Campaigns" value={loading ? '...' : campaigns.length.toString()} helper="Mongo-backed mappings" />
      <Kpi icon={<PhoneCall />} label="Completed calls" value={loading ? '...' : completedCalls.toString()} helper={`${transcripts.length} transcripts`} />
      <Kpi icon={<Clock3 />} label="Avg duration" value={avgDuration ? `${avgDuration}s` : '-'} helper="from saved transcripts" />
    </section>
  );
}

function Kpi({ icon, label, value, helper }: { icon: React.ReactNode; label: string; value: string; helper: string }) {
  return (
    <div className="kpi">
      <div className="kpi-icon">{icon}</div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{helper}</small>
      </div>
    </div>
  );
}

function BotsView({ bots, selectedBot, transcripts, onSelect }: {
  bots: BotType[];
  selectedBot?: BotType;
  transcripts: Transcript[];
  onSelect: (botId: string) => void;
}) {
  return (
    <section className="content-grid two-col">
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Agents</h2>
            <p>Each agent owns its prompt, voice, language, versions, and runtime settings.</p>
          </div>
        </div>
        <table>
          <thead>
            <tr><th>Name</th><th>Status</th><th>Assistant</th><th>Updated</th><th /></tr>
          </thead>
          <tbody>
            {bots.map((bot) => (
              <tr key={bot._id} onClick={() => onSelect(bot._id)} className={bot._id === selectedBot?._id ? 'selected-row' : ''}>
                <td>
                  <strong>{bot.name}</strong>
                  <small>{bot.description || 'Prompt + settings agent'}</small>
                </td>
                <td><StatusPill value={bot.status} /></td>
                <td><code>{shortId(bot.assistant_id)}</code></td>
                <td>{formatDate(bot.updated_at)}</td>
                <td><ChevronRight size={16} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="panel">
        <div className="agent-profile">
          <div className="agent-avatar"><Headphones size={28} /></div>
          <div>
            <h2>{selectedBot?.name || 'No agent selected'}</h2>
            <p>{selectedBot?.description || 'Open an agent to edit its draft or test a browser call.'}</p>
          </div>
        </div>
        <div className="detail-list">
          <Detail label="Assistant ID" value={selectedBot?.assistant_id || '-'} />
          <Detail label="Active version" value={selectedBot?.active_version_id ? shortId(selectedBot.active_version_id) : '-'} />
          <Detail label="Orchestration" value={selectedBot?.orchestration?.mode || 'prompt_settings'} />
          <Detail label="Owner" value={selectedBot?.owner || 'local-dev'} />
          <Detail label="Calls stored" value={transcripts.filter((item) => item.bot_id === selectedBot?._id).length.toString()} />
        </div>
        <div className="callout success">
          <ShieldCheck size={18} />
          Live calls keep their original published config snapshot. Publishing changes affects only new calls.
        </div>
      </div>
    </section>
  );
}

function BuilderView({
  selectedBot,
  versions,
  activeVersion,
  latestDraft,
  publishedCount,
  config,
  configText,
  voices,
  languages,
  onConfigTextChange,
  onUpdateConfig,
  onUpdateLanguage,
  onSaveDraft,
  onPublish
}: {
  selectedBot?: BotType;
  versions: BotVersion[];
  activeVersion?: BotVersion;
  latestDraft?: BotVersion;
  publishedCount: number;
  config: { ok: true; value: RuntimeConfig } | { ok: false; error: string };
  configText: string;
  voices: VoiceOption[];
  languages: LanguageOption[];
  onConfigTextChange: (value: string) => void;
  onUpdateConfig: (key: keyof RuntimeConfig, value: unknown) => void;
  onUpdateLanguage: (value: string) => void;
  onSaveDraft: () => void;
  onPublish: () => void;
}) {
  const value = config.ok ? config.value : defaultConfig;
  return (
    <section className="builder-layout">
      <div className="builder-main">
        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>{selectedBot?.name || 'Bot Builder'}</h2>
              <p>PMs edit the spoken behavior here. Developers can use the JSON panel for advanced runtime settings.</p>
            </div>
            <div className="button-row">
              <button disabled={!config.ok} onClick={onSaveDraft}><Save size={16} /> Save draft</button>
              <button className="primary" disabled={!latestDraft && versions.length > 0} onClick={onPublish}><Rocket size={16} /> Publish</button>
            </div>
          </div>
          {!config.ok && <div className="notice error"><AlertTriangle size={16} /> JSON is invalid: {config.error}</div>}
          <div className="form-section">
            <label className="full">
              System prompt
              <textarea
                className="prompt-editor"
                value={String(value.system_prompt || '')}
                onChange={(event) => onUpdateConfig('system_prompt', event.target.value)}
              />
            </label>
            <div className="form-grid">
              <label>
                Opening line
                <input value={String(value.initial_message || '')} onChange={(event) => onUpdateConfig('initial_message', event.target.value)} />
              </label>
              <label>
                Closing line
                <input value={String(value.call_end_text || '')} onChange={(event) => onUpdateConfig('call_end_text', event.target.value)} />
              </label>
              <label>
                Voice
                <select value={String(value.voice || '')} onChange={(event) => onUpdateConfig('voice', event.target.value)}>
                  {voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.label} {voice.gender ? `(${voice.gender})` : ''}</option>)}
                </select>
              </label>
              <label>
                Language
                <select value={String(value.language || '')} onChange={(event) => onUpdateLanguage(event.target.value)}>
                  {languages.map((language) => <option key={language.id} value={language.id}>{language.label}</option>)}
                </select>
              </label>
              <label>
                Model
                <input value={String(value.model || '')} onChange={(event) => onUpdateConfig('model', event.target.value)} />
              </label>
              <label>
                Max call duration
                <input type="number" value={Number(value.max_call_duration || 300)} onChange={(event) => onUpdateConfig('max_call_duration', Number(event.target.value))} />
              </label>
              <label>
                Temperature
                <input type="number" min="0" max="2" step="0.1" value={Number(value.temperature || 0)} onChange={(event) => onUpdateConfig('temperature', Number(event.target.value))} />
              </label>
              <label>
                Silence duration ms
                <input type="number" value={Number(value.gemini_silence_duration_ms || 1800)} onChange={(event) => onUpdateConfig('gemini_silence_duration_ms', Number(event.target.value))} />
              </label>
            </div>
          </div>
        </div>
        <div className="panel json-panel">
          <div className="panel-header">
            <div>
              <h2>Developer JSON</h2>
              <p>Advanced config stays visible so backend/runtime fields are not hidden from developers.</p>
            </div>
            <Database size={18} />
          </div>
          <textarea className="json-editor" value={configText} onChange={(event) => onConfigTextChange(event.target.value)} spellCheck={false} />
        </div>
      </div>
      <aside className="right-rail">
        <div className="panel compact">
          <h2>Publishing</h2>
          <div className="detail-list">
            <Detail label="Published versions" value={publishedCount.toString()} />
            <Detail label="Active version" value={activeVersion ? `v${activeVersion.version}` : '-'} />
            <Detail label="Latest draft" value={latestDraft ? `v${latestDraft.version}` : 'None'} />
          </div>
        </div>
        <div className="panel compact">
          <h2>Version history</h2>
          <div className="version-list">
            {versions.map((version) => (
              <div className="version-row" key={version._id}>
                <span>v{version.version}</span>
                <StatusPill value={version.state} />
                <small>{version.published_at ? `Published ${formatDate(version.published_at)}` : `Created ${formatDate(version.created_at)}`}</small>
              </div>
            ))}
          </div>
        </div>
        <div className="callout">
          <Wand2 size={18} />
          V1 is prompt and settings only. Visual node routing can come later through Dograh.
        </div>
      </aside>
    </section>
  );
}

function CampaignsView({ campaigns, bots }: { campaigns: Campaign[]; bots: BotType[] }) {
  return (
    <section className="content-grid two-col">
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Campaign mappings</h2>
            <p>One bot belongs to one campaign, with lead API and callback mapping owned in Mongo.</p>
          </div>
        </div>
        <table>
          <thead>
            <tr><th>Campaign</th><th>Bot</th><th>Status</th><th>Lead API</th><th>Updated</th></tr>
          </thead>
          <tbody>
            {campaigns.map((campaign) => (
              <tr key={campaign._id}>
                <td><strong>{campaign.name}</strong><small>{campaign.campaign_key}</small></td>
                <td>{bots.find((bot) => bot._id === campaign.bot_id)?.name || '-'}</td>
                <td><StatusPill value={campaign.status || 'draft'} /></td>
                <td><code>{String(campaign.lead_api?.url || campaign.lead_api?.endpoint || '-')}</code></td>
                <td>{formatDate(campaign.updated_at)}</td>
              </tr>
            ))}
            {!campaigns.length && (
              <tr><td colSpan={5}>No campaigns yet. Backend can create mappings through <code>POST /api/campaigns</code>.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="panel">
        <h2>Dispatch contract</h2>
        <div className="timeline">
          <Step title="Lead event/API" text="Campaign fetches lead id, mobile, product, buyer name, city, and call id." />
          <Step title="Room metadata" text="Dialer creates LiveKit room with assistant_id, campaign_id, lead_id and call_id." />
          <Step title="Agent joins" text="Runtime fetches active published config and stores immutable call snapshot." />
          <Step title="Callback" text="Outcome and raw transcript are saved, then callback mapping runs per campaign." />
        </div>
      </div>
    </section>
  );
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
  form: TestForm;
  setForm: React.Dispatch<React.SetStateAction<TestForm>>;
  status: string;
  error: string;
  roomName: string;
  remoteAudioReady: boolean;
  remoteAudioRef: React.RefObject<HTMLDivElement>;
  onStart: () => void;
  onStop: () => void;
}) {
  function updateField(key: keyof TestForm, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
  }

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
        <div className="form-grid">
          <label>Campaign ID<input value={form.campaign_id} onChange={(event) => updateField('campaign_id', event.target.value)} /></label>
          <label>Lead ID<input value={form.lead_id} onChange={(event) => updateField('lead_id', event.target.value)} placeholder="optional for local test" /></label>
          <label>Call ID<input value={form.call_id} onChange={(event) => updateField('call_id', event.target.value)} /></label>
          <label>Mobile<input value={form.mobile} onChange={(event) => updateField('mobile', event.target.value)} placeholder="test number" /></label>
          <label>Product / Search Term<input value={form.srchterm} onChange={(event) => updateField('srchterm', event.target.value)} /></label>
          <label>Buyer Name<input value={form.buyer_name} onChange={(event) => updateField('buyer_name', event.target.value)} /></label>
          <label>City<input value={form.city} onChange={(event) => updateField('city', event.target.value)} /></label>
        </div>
        <div className="button-row">
          <button className="primary" onClick={onStart} disabled={!selectedBot}><Play size={16} /> Start WebRTC test</button>
          <button onClick={onStop}><Square size={16} /> End test</button>
        </div>
      </div>
      <div className="panel status-panel">
        <h2>Connection checklist</h2>
        <ConnectionLine icon={<Database />} label="Backend room" value={roomName || 'Not created'} done={Boolean(roomName)} />
        <ConnectionLine icon={<Wifi />} label="LiveKit socket" value={status} done={!status.toLowerCase().includes('failed') && status !== 'Idle'} />
        <ConnectionLine icon={<Mic />} label="Microphone" value={status.includes('Microphone') || remoteAudioReady ? 'Requested' : 'Waiting'} done={status.includes('Microphone') || remoteAudioReady} />
        <ConnectionLine icon={<Volume2 />} label="Bot audio" value={remoteAudioReady ? 'Connected' : 'Waiting'} done={remoteAudioReady} />
        <div ref={remoteAudioRef} />
        {error && <div className="notice error"><AlertTriangle size={16} /> {error}</div>}
      </div>
    </section>
  );
}

function TranscriptsView({
  transcripts,
  selectedTranscript,
  searchText,
  onSearchText,
  onSelect
}: {
  transcripts: Transcript[];
  selectedTranscript?: Transcript;
  searchText: string;
  onSearchText: (value: string) => void;
  onSelect: (id: string) => void;
}) {
  return (
    <section className="content-grid transcripts-grid">
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Saved transcripts</h2>
            <p>Raw transcripts are stored forever with call config snapshots.</p>
          </div>
          <div className="search-box"><Search size={15} /><input value={searchText} onChange={(event) => onSearchText(event.target.value)} placeholder="Search call, lead, campaign or text" /></div>
        </div>
        <table>
          <thead>
            <tr><th>Call</th><th>Lead</th><th>Status</th><th>Duration</th><th>Turns</th><th>Created</th></tr>
          </thead>
          <tbody>
            {transcripts.map((item) => (
              <tr key={item._id} onClick={() => onSelect(item._id)} className={item._id === selectedTranscript?._id ? 'selected-row' : ''}>
                <td><strong>{item.call_id || '-'}</strong><small>{item.campaign_id || '-'}</small></td>
                <td>{item.lead_id || '-'}</td>
                <td><StatusPill value={item.status || 'unknown'} /></td>
                <td>{item.call_duration_sec || 0}s</td>
                <td>{item.transcript?.length || 0}</td>
                <td>{formatDate(item.created_at)}</td>
              </tr>
            ))}
            {!transcripts.length && <tr><td colSpan={6}>No transcripts found.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="panel transcript-detail">
        <h2>Transcript detail</h2>
        {selectedTranscript ? (
          <>
            <div className="detail-list">
              <Detail label="Call ID" value={selectedTranscript.call_id || '-'} />
              <Detail label="Bot version" value={selectedTranscript.bot_version_id ? shortId(selectedTranscript.bot_version_id) : '-'} />
              <Detail label="Callback" value={selectedTranscript.callback_status || '-'} />
              <Detail label="Recording" value={selectedTranscript.recording_url || 'Dialer link not saved'} />
            </div>
            <div className="turn-list">
              {(selectedTranscript.transcript || []).slice(0, 14).map((turn, index) => (
                <div className={`turn ${turn.role}`} key={`${turn.role}-${index}`}>
                  <span>{turn.role}</span>
                  <p>{turn.text}</p>
                </div>
              ))}
              {!selectedTranscript.transcript?.length && <p className="muted">No transcript turns saved for this call yet.</p>}
            </div>
          </>
        ) : <p className="muted">Select a transcript to inspect details.</p>}
      </div>
    </section>
  );
}

function ObservabilityView({
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

function NavButton({ icon, label, active, onClick }: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return <button className={`nav-button ${active ? 'active' : ''}`} onClick={onClick}>{icon}<span>{label}</span></button>;
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div className="detail"><span>{label}</span><strong>{value}</strong></div>;
}

function StatusPill({ value }: { value: string }) {
  return <span className={`pill ${value.toLowerCase()}`}>{value}</span>;
}

function ConnectionLine({ icon, label, value, done }: {
  icon: React.ReactNode;
  label: string;
  value: string;
  done: boolean;
}) {
  return (
    <div className="connection-line">
      <div className={done ? 'connection-icon done' : 'connection-icon'}>{done ? <CheckCircle2 size={17} /> : icon}</div>
      <div><strong>{label}</strong><span>{value}</span></div>
    </div>
  );
}

function Step({ title, text }: { title: string; text: string }) {
  return <div className="step"><strong>{title}</strong><p>{text}</p></div>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function titleFor(view: View) {
  return {
    bots: 'Agents',
    builder: 'Agent Builder',
    campaigns: 'Campaigns',
    test: 'WebRTC Test Call',
    transcripts: 'Transcripts',
    observability: 'Observability'
  }[view];
}

function subtitleFor(view: View) {
  return {
    bots: 'Manage outbound-first voice agents and active published versions.',
    builder: 'Edit prompts, voice, language, and runtime settings safely.',
    campaigns: 'Connect one bot to one campaign and its lead/callback APIs.',
    test: 'Start a controlled browser call with helpful connection diagnostics.',
    transcripts: 'Inspect raw call transcripts, outcomes, and config snapshots.',
    observability: 'Track LiveKit health, Gemini latency, TTFW, and callback failures.'
  }[view];
}

function parseConfig(value: string): { ok: true; value: RuntimeConfig } | { ok: false; error: string } {
  try {
    return { ok: true, value: JSON.parse(value) as RuntimeConfig };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

function friendlyApiError(error: unknown) {
  const raw = error instanceof Error ? error.message : String(error);
  if (raw.toLowerCase().includes('failed to fetch')) {
    return 'Cannot reach the FastAPI backend. Start it with ./start_api.sh, then refresh this page.';
  }
  return raw || 'API request failed.';
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

function shortId(value: string) {
  if (!value) return '-';
  return value.length > 12 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}

function formatDate(value?: string) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });
}

function average(values: number[]) {
  if (!values.length) return 0;
  return Math.round(values.reduce((sum, item) => sum + item, 0) / values.length);
}

createRoot(document.getElementById('root')!).render(<App />);
