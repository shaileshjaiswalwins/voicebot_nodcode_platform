import React, { Component, ErrorInfo, ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  createLocalAudioTrack,
  LocalAudioTrack,
  RemoteTrack,
  Room,
  RoomEvent,
  Track
} from 'livekit-client';
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  Bot,
  BookOpen,
  Braces,
  CheckCircle2,
  ChevronRight,
  Clock3,
  ClipboardList,
  Database,
  FileText,
  Gauge,
  Headphones,
  Megaphone,
  Mic,
  PhoneCall,
  Play,
  Plus,
  RefreshCw,
  Rocket,
  Save,
  Search,
  ShieldCheck,
  Square,
  Trash2,
  Volume2,
  Wand2,
  Wifi
} from 'lucide-react';
import {
  api,
  Bot as BotType,
  BotVersion,
  Campaign,
  CallEvent,
  LangfuseSettings,
  LanguageOption,
  LanguageSettings,
  LibraryPhrase,
  OutcomeEntry,
  PhraseCategory,
  RuntimeSettings,
  Transcript,
  VoiceOption
} from './api';
import './styles.css';

type View = 'bots' | 'builder' | 'campaigns' | 'test' | 'transcripts' | 'observability' | 'library' | 'settings';
type DiagnosticSeverity = 'info' | 'warning' | 'error';

type Diagnostic = {
  id: string;
  scope: string;
  severity: DiagnosticSeverity;
  message: string;
  action?: string;
  createdAt: string;
};

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
  test_worker_agent_name: string;
};

const defaultConfig: RuntimeConfig = {
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
  const [runtimeSettings, setRuntimeSettings] = useState<RuntimeSettings | null>(null);
  const [phrases, setPhrases] = useState<LibraryPhrase[]>([]);
  const [outcomes, setOutcomes] = useState<OutcomeEntry[]>([]);
  const [languageSettings, setLanguageSettings] = useState<LanguageSettings[]>([]);
  const [configText, setConfigText] = useState(JSON.stringify(defaultConfig, null, 2));
  const [transcripts, setTranscripts] = useState<Transcript[]>([]);
  const [callEvents, setCallEvents] = useState<CallEvent[]>([]);
  const [selectedTranscriptId, setSelectedTranscriptId] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([]);
  const [actionState, setActionState] = useState<Record<string, 'idle' | 'running' | 'failed'>>({});
  const [searchText, setSearchText] = useState('');
  const [testForm, setTestForm] = useState<TestForm>({
    campaign_id: 'test',
    lead_id: '',
    call_id: `TEST-${Date.now()}`,
    mobile: '',
    srchterm: 'air conditioner',
    buyer_name: 'Test User',
    city: 'Mumbai',
    test_worker_agent_name: ''
  });
  const [testStatus, setTestStatus] = useState('Idle');
  const [testError, setTestError] = useState('');
  const [testRoomName, setTestRoomName] = useState('');
  const [remoteAudioReady, setRemoteAudioReady] = useState(false);
  const livekitRoomRef = useRef<Room | null>(null);
  const localTrackRef = useRef<LocalAudioTrack | null>(null);
  const remoteAudioRef = useRef<HTMLDivElement | null>(null);
  const subscribedTracksRef = useRef<Set<RemoteTrack>>(new Set());

  useEffect(() => {
    if (!runtimeSettings?.livekit_agent_name) return;
    setTestForm((current) => (
      current.test_worker_agent_name
        ? current
        : { ...current, test_worker_agent_name: runtimeSettings.livekit_agent_name }
    ));
  }, [runtimeSettings?.livekit_agent_name]);

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

  function reportDiagnostic(scope: string, error: unknown, action?: string, severity: DiagnosticSeverity = 'error') {
    const diagnostic = buildDiagnostic(scope, error, action, severity);
    setDiagnostics((current) => [diagnostic, ...current.filter((item) => item.scope !== scope)].slice(0, 8));
    setMessage(diagnostic.message);
  }

  function clearDiagnostic(scope?: string) {
    setDiagnostics((current) => scope ? current.filter((item) => item.scope !== scope) : []);
  }

  async function refresh() {
    setLoading(true);
    setMessage('');
    const results = await Promise.allSettled([
      api.bots(),
      api.transcripts(),
      api.campaigns(),
      api.voices(),
      api.languages(),
      api.langfuseSettings(),
      api.runtimeSettings(),
      api.phrases(),
      api.outcomes(),
      api.languageSettings()
    ]);
    const scopes = ['Bots API', 'Transcripts API', 'Campaigns API', 'Voice Options API', 'Language Options API', 'Langfuse Settings API', 'Runtime Settings API', 'Phrase Library API', 'Outcome Catalog API', 'Language Settings API'];
    results.forEach((result, index) => {
      if (result.status === 'rejected') reportDiagnostic(scopes[index], result.reason, 'Retry refresh or use cached dashboard data');
    });
    if (results[0].status === 'fulfilled') {
      setBots(results[0].value);
      cacheSet('bots', results[0].value);
      if (!selectedBotId && results[0].value[0]) setSelectedBotId(results[0].value[0]._id);
      clearDiagnostic('Bots API');
    }
    if (results[1].status === 'fulfilled') {
      setTranscripts(results[1].value);
      cacheSet('transcripts', results[1].value);
      if (!selectedTranscriptId && results[1].value[0]) setSelectedTranscriptId(results[1].value[0]._id);
      clearDiagnostic('Transcripts API');
    }
    if (results[2].status === 'fulfilled') {
      setCampaigns(results[2].value);
      cacheSet('campaigns', results[2].value);
      clearDiagnostic('Campaigns API');
    }
    if (results[3].status === 'fulfilled') {
      setVoices(results[3].value);
      cacheSet('voices', results[3].value);
      clearDiagnostic('Voice Options API');
    }
    if (results[4].status === 'fulfilled') {
      setLanguages(results[4].value);
      cacheSet('languages', results[4].value);
      clearDiagnostic('Language Options API');
    }
    if (results[5].status === 'fulfilled') {
      setLangfuseSettings(results[5].value);
      cacheSet('langfuse', results[5].value);
      clearDiagnostic('Langfuse Settings API');
    }
    if (results[6].status === 'fulfilled') {
      setRuntimeSettings(results[6].value);
      cacheSet('runtimeSettings', results[6].value);
      clearDiagnostic('Runtime Settings API');
    }
    if (results[7].status === 'fulfilled') {
      setPhrases(results[7].value);
      cacheSet('phrases', results[7].value);
      clearDiagnostic('Phrase Library API');
    }
    if (results[8].status === 'fulfilled') {
      setOutcomes(results[8].value);
      cacheSet('outcomes', results[8].value);
      clearDiagnostic('Outcome Catalog API');
    }
    if (results[9].status === 'fulfilled') {
      setLanguageSettings(results[9].value);
      cacheSet('languageSettings', results[9].value);
      clearDiagnostic('Language Settings API');
    }
    if (results.some((result) => result.status === 'rejected')) {
      setMessage('Some dashboard data could not load. You can retry or continue with cached data.');
    }
    setLoading(false);
  }

  useEffect(() => {
    setBots(cacheGet<BotType[]>('bots', []));
    setTranscripts(cacheGet<Transcript[]>('transcripts', []));
    setCampaigns(cacheGet<Campaign[]>('campaigns', []));
    setVoices(cacheGet<VoiceOption[]>('voices', []));
    setLanguages(cacheGet<LanguageOption[]>('languages', []));
    setLangfuseSettings(cacheGet<LangfuseSettings | null>('langfuse', null));
    setRuntimeSettings(cacheGet<RuntimeSettings | null>('runtimeSettings', null));
    setPhrases(cacheGet<LibraryPhrase[]>('phrases', []));
    setOutcomes(cacheGet<OutcomeEntry[]>('outcomes', []));
    setLanguageSettings(cacheGet<LanguageSettings[]>('languageSettings', []));
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
      .catch((error) => reportDiagnostic('Bot Versions API', error, 'Keep editing cached config or retry refresh'));
  }, [selectedBot?._id]);

  useEffect(() => {
    if (!selectedTranscriptId) {
      setCallEvents([]);
      return;
    }
    api.transcriptEvents(selectedTranscriptId)
      .then((events) => {
        setCallEvents(events);
        clearDiagnostic('Call Events API');
      })
      .catch((error) => {
        setCallEvents([]);
        reportDiagnostic('Call Events API', error, 'Refresh after the call ends or inspect worker logs');
      });
  }, [selectedTranscriptId]);

  async function runAction(actionKey: string, label: string, task: () => Promise<void>) {
    setActionState((current) => ({ ...current, [actionKey]: 'running' }));
    clearDiagnostic(label);
    try {
      await task();
      setActionState((current) => ({ ...current, [actionKey]: 'idle' }));
    } catch (error) {
      setActionState((current) => ({ ...current, [actionKey]: 'failed' }));
      reportDiagnostic(label, error, 'Retry the action or keep editing locally');
    }
  }

  async function createBot() {
    await runAction('createBot', 'Create Bot', async () => {
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
    });
  }

  async function saveDraft() {
    if (!selectedBot || !parsedConfig.ok) return;
    await runAction('saveDraft', 'Save Draft', async () => {
      const version = await api.saveDraft(selectedBot._id, {
        config: parsedConfig.value,
        notes: 'Dashboard draft save'
      });
      setMessage(`Draft version ${version.version} saved. Publish it when ready for new calls.`);
      await refresh();
    });
  }

  async function publishDraft() {
    if (!selectedBot) return;
    await runAction('publishDraft', 'Publish Bot', async () => {
      const version = await api.publish(selectedBot._id, latestDraft?._id);
      setMessage(`Published version ${version.version}. Live calls keep their old snapshot; new calls use this version.`);
      await refresh();
    });
  }

  async function updateLangfuse(payload: Partial<LangfuseSettings>) {
    await runAction('langfuse', 'Langfuse Settings', async () => {
      const nextSettings = await api.updateLangfuseSettings(payload);
      setLangfuseSettings(nextSettings);
      setMessage(`Langfuse ${nextSettings.enabled ? 'enabled' : 'disabled'} for ${nextSettings.environment}.`);
    });
  }

  async function reloadPhrases() {
    try {
      const next = await api.phrases();
      setPhrases(next);
      cacheSet('phrases', next);
      clearDiagnostic('Phrase Library API');
    } catch (error) {
      reportDiagnostic('Phrase Library API', error, 'Retry or keep working with cached phrases');
    }
  }

  async function createPhrase(payload: Partial<LibraryPhrase>) {
    await runAction('createPhrase', 'Create Phrase', async () => {
      await api.createPhrase(payload);
      await reloadPhrases();
      setMessage('Phrase added. Calls starting now will use the updated list within a minute.');
    });
  }

  async function updatePhrase(id: string, payload: Partial<LibraryPhrase>) {
    await runAction(`updatePhrase-${id}`, 'Update Phrase', async () => {
      await api.updatePhrase(id, payload);
      await reloadPhrases();
      setMessage('Phrase updated.');
    });
  }

  async function deletePhrase(id: string) {
    await runAction(`deletePhrase-${id}`, 'Delete Phrase', async () => {
      await api.deletePhrase(id);
      await reloadPhrases();
      setMessage('Phrase removed.');
    });
  }

  async function upsertLanguageSettingsEntry(payload: Partial<LanguageSettings>) {
    await runAction(`upsertLang-${payload.id || 'new'}`, 'Save Language Settings', async () => {
      await api.upsertLanguageSettings(payload);
      try {
        const next = await api.languageSettings();
        setLanguageSettings(next);
        cacheSet('languageSettings', next);
        clearDiagnostic('Language Settings API');
      } catch (error) {
        reportDiagnostic('Language Settings API', error, 'Retry or keep working with cached settings');
      }
      setMessage(`Language settings for "${payload.id}" saved.`);
    });
  }

  async function updateOutcomeEntry(key: string, payload: Partial<OutcomeEntry>) {
    await runAction(`updateOutcome-${key}`, 'Update Outcome', async () => {
      await api.updateOutcome(key, payload);
      try {
        const next = await api.outcomes();
        setOutcomes(next);
        cacheSet('outcomes', next);
        clearDiagnostic('Outcome Catalog API');
      } catch (error) {
        reportDiagnostic('Outcome Catalog API', error, 'Retry or keep working with cached outcomes');
      }
      setMessage(`Outcome "${key}" updated. The AI will use the new description on next call analysis.`);
    });
  }

  async function updateRuntime(payload: Partial<RuntimeSettings>) {
    await runAction('runtimeSettings', 'Runtime Settings', async () => {
      const nextSettings = await api.updateRuntimeSettings(payload);
      setRuntimeSettings(nextSettings);
      cacheSet('runtimeSettings', nextSettings);
      setMessage(`Runtime settings saved. Test calls now dispatch to ${nextSettings.livekit_agent_name}.`);
    });
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
        if (track.kind !== Track.Kind.Audio) return;
        const container = remoteAudioRef.current;
        if (!container) return;
        const element = track.attach();
        // Re-check the ref now that attach has run — the component may have unmounted mid-flight.
        if (!remoteAudioRef.current) {
          track.detach(element);
          element.remove();
          return;
        }
        element.autoplay = true;
        container.appendChild(element);
        subscribedTracksRef.current.add(track);
        setRemoteAudioReady(true);
        setTestStatus('Bot audio connected. Speak into your microphone.');
      });
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        for (const element of track.detach()) element.remove();
        subscribedTracksRef.current.delete(track);
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
      const message = friendlyTestError(error);
      setTestError(message);
      reportDiagnostic('WebRTC Test Call', message, 'Retry room setup or skip LiveKit and keep editing metadata');
      setTestStatus('Failed to start test');
    }
  }

  async function stopWebRtcTest() {
    const roomName = testRoomName;
    localTrackRef.current?.stop();
    localTrackRef.current = null;
    // Properly detach every subscribed track so LiveKit releases its element references.
    for (const track of subscribedTracksRef.current) {
      try {
        for (const element of track.detach()) element.remove();
      } catch {
        // best-effort cleanup
      }
    }
    subscribedTracksRef.current.clear();
    const container = remoteAudioRef.current;
    if (container) {
      for (const el of Array.from(container.querySelectorAll('audio, video'))) {
        const media = el as HTMLMediaElement;
        try { media.pause(); } catch { /* ignore */ }
        media.srcObject = null;
        media.remove();
      }
    }
    if (livekitRoomRef.current) {
      try { await livekitRoomRef.current.disconnect(); } catch { /* ignore */ }
      livekitRoomRef.current = null;
    }
    if (roomName) {
      try {
        setTestStatus('Closing LiveKit test room...');
        await api.closeWebRtcTestSession(roomName);
      } catch (error) {
        reportDiagnostic(
          'WebRTC Test Call',
          friendlyTestError(error),
          'The browser disconnected locally. Retry Stop once, then check worker logs if the transcript is missing.'
        );
      }
    }
    setRemoteAudioReady(false);
    setTestRoomName('');
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
          <NavButton icon={<BookOpen />} label="Library" active={view === 'library'} onClick={() => setView('library')} />
          <NavButton icon={<ClipboardList />} label="Settings" active={view === 'settings'} onClick={() => setView('settings')} />
        </div>
        <div className="sidebar-card">
          <span className={diagnostics.some((item) => item.severity === 'error') ? 'status-dot error-dot' : 'status-dot'} />
          <strong>{diagnostics.length ? `${diagnostics.length} diagnostic${diagnostics.length > 1 ? 's' : ''}` : 'Systems nominal'}</strong>
          <small>{diagnostics[0]?.scope || 'ai_voice_bot_management'}</small>
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
            <button className="primary" onClick={createBot} disabled={actionState.createBot === 'running'}><Rocket size={16} /> {actionState.createBot === 'failed' ? 'Retry New Agent' : 'New Agent'}</button>
          </div>
        </header>

        <DiagnosticsBar diagnostics={diagnostics} onClear={clearDiagnostic} onRetry={refresh} onUseCache={() => {
          setBots(cacheGet<BotType[]>('bots', bots));
          setTranscripts(cacheGet<Transcript[]>('transcripts', transcripts));
          setCampaigns(cacheGet<Campaign[]>('campaigns', campaigns));
          setVoices(cacheGet<VoiceOption[]>('voices', voices));
          setLanguages(cacheGet<LanguageOption[]>('languages', languages));
          setLangfuseSettings(cacheGet<LangfuseSettings | null>('langfuse', langfuseSettings));
          setRuntimeSettings(cacheGet<RuntimeSettings | null>('runtimeSettings', runtimeSettings));
          setMessage('Loaded last known cached dashboard data.');
        }} />

        {message && (
          <div className={message.startsWith('Cannot') || message.startsWith('API') ? 'notice error' : 'notice'}>
            <AlertCircle size={16} /> {message}
          </div>
        )}

        <KpiStrip bots={bots} transcripts={transcripts} campaigns={campaigns} loading={loading} />

        {view === 'bots' && (
          <ResilientPanel name="Agents" onDiagnostic={reportDiagnostic}>
            <BotsView
              bots={bots}
              selectedBot={selectedBot}
              transcripts={transcripts}
              onSelect={(botId) => { setSelectedBotId(botId); setView('builder'); }}
            />
          </ResilientPanel>
        )}

        {view === 'builder' && (
          <ResilientPanel name="Builder" onDiagnostic={reportDiagnostic}>
            <BuilderView
              selectedBot={selectedBot}
              versions={versions}
              activeVersion={activeVersion}
              latestDraft={latestDraft}
              publishedCount={publishedCount}
              config={parsedConfig}
              configText={configText}
              voices={voices.length ? voices : cacheGet<VoiceOption[]>('voices', [])}
              languages={languages.length ? languages : cacheGet<LanguageOption[]>('languages', [])}
              onConfigTextChange={setConfigText}
              onUpdateConfig={updateConfig}
              onUpdateLanguage={updateLanguage}
              onSaveDraft={saveDraft}
              onPublish={publishDraft}
              saveState={actionState.saveDraft}
              publishState={actionState.publishDraft}
            />
          </ResilientPanel>
        )}

        {view === 'campaigns' && (
          <ResilientPanel name="Campaigns" onDiagnostic={reportDiagnostic}>
            <CampaignsView campaigns={campaigns} bots={bots} />
          </ResilientPanel>
        )}

        {view === 'test' && (
          <ResilientPanel name="WebRTC Test" onDiagnostic={reportDiagnostic}>
            <TestCallPanel
              bots={bots}
              selectedBot={selectedBot}
              selectedBotId={selectedBot?._id || ''}
              onSelectBot={setSelectedBotId}
              runtimeSettings={runtimeSettings}
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
          </ResilientPanel>
        )}

        {view === 'transcripts' && (
          <ResilientPanel name="Transcripts" onDiagnostic={reportDiagnostic}>
            <TranscriptsView
              transcripts={filteredTranscripts}
              selectedTranscript={selectedTranscript}
              callEvents={callEvents}
              searchText={searchText}
              onSearchText={setSearchText}
              onSelect={setSelectedTranscriptId}
            />
          </ResilientPanel>
        )}

        {view === 'observability' && (
          <ResilientPanel name="Observability" onDiagnostic={reportDiagnostic}>
            <ObservabilityView
              selectedBot={selectedBot}
              transcripts={transcripts}
              langfuseSettings={langfuseSettings}
              onUpdateLangfuse={updateLangfuse}
            />
          </ResilientPanel>
        )}

        {view === 'library' && (
          <ResilientPanel name="Library" onDiagnostic={reportDiagnostic}>
            <LibraryView
              phrases={phrases}
              outcomes={outcomes}
              languageSettings={languageSettings}
              onCreate={createPhrase}
              onUpdate={updatePhrase}
              onDelete={deletePhrase}
              onUpdateOutcome={updateOutcomeEntry}
              onUpsertLanguageSettings={upsertLanguageSettingsEntry}
            />
          </ResilientPanel>
        )}

        {view === 'settings' && (
          <ResilientPanel name="Settings" onDiagnostic={reportDiagnostic}>
            <SettingsView runtimeSettings={runtimeSettings} onUpdateRuntime={updateRuntime} />
          </ResilientPanel>
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

class ResilientPanel extends Component<{
  name: string;
  children: ReactNode;
  onDiagnostic: (scope: string, error: unknown, action?: string, severity?: DiagnosticSeverity) => void;
}, { failed: boolean; error?: Error }> {
  state = { failed: false, error: undefined as Error | undefined };

  static getDerivedStateFromError(error: Error) {
    return { failed: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onDiagnostic(
      this.props.name,
      `${error.message}\n${info.componentStack}`,
      'Reset this widget or bypass it and continue elsewhere'
    );
  }

  render() {
    if (this.state.failed) {
      return (
        <section className="panel crash-panel">
          <AlertTriangle size={22} />
          <div>
            <h2>{this.props.name} crashed locally</h2>
            <p>{this.state.error?.message || 'A widget-level render error occurred.'}</p>
            <div className="button-row">
              <button className="fallback-button" onClick={() => this.setState({ failed: false, error: undefined })}>
                Reset local state
              </button>
              <button onClick={() => window.location.hash = '#diagnostics'}>Bypass / inspect diagnostics</button>
            </div>
          </div>
        </section>
      );
    }
    return this.props.children;
  }
}

function DiagnosticsBar({
  diagnostics,
  onClear,
  onRetry,
  onUseCache
}: {
  diagnostics: Diagnostic[];
  onClear: (scope?: string) => void;
  onRetry: () => void;
  onUseCache: () => void;
}) {
  if (!diagnostics.length) {
    return (
      <section className="diagnostics-bar healthy" id="diagnostics">
        <CheckCircle2 size={16} />
        <span>Dashboard diagnostics clear</span>
      </section>
    );
  }
  const latest = diagnostics[0];
  return (
    <section className={`diagnostics-bar ${latest.severity}`} id="diagnostics">
      <AlertTriangle size={17} />
      <div>
        <strong>{latest.scope}: {latest.message}</strong>
        <span>{latest.action || 'Retry, continue with cached data, or inspect the affected widget.'}</span>
      </div>
      <div className="diagnostics-actions">
        <button className="fallback-button" onClick={onRetry}><RefreshCw size={15} /> Retry</button>
        <button className="fallback-button" onClick={onUseCache}><ClipboardList size={15} /> Use cached</button>
        <button onClick={() => onClear(latest.scope)}>Dismiss</button>
      </div>
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
  onPublish,
  saveState,
  publishState
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
  saveState?: 'idle' | 'running' | 'failed';
  publishState?: 'idle' | 'running' | 'failed';
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
              <button disabled={!config.ok || saveState === 'running'} onClick={onSaveDraft}>
                <Save size={16} /> {saveState === 'failed' ? 'Retry save draft' : saveState === 'running' ? 'Saving...' : 'Save draft'}
              </button>
              <button className={publishState === 'failed' ? 'fallback-button' : 'primary'} disabled={publishState === 'running' || (!latestDraft && versions.length > 0)} onClick={onPublish}>
                <Rocket size={16} /> {publishState === 'failed' ? 'Fallback: retry publish' : publishState === 'running' ? 'Publishing...' : 'Publish'}
              </button>
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
  bots,
  selectedBot,
  selectedBotId,
  onSelectBot,
  runtimeSettings,
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
  bots: BotType[];
  selectedBot?: BotType;
  selectedBotId: string;
  onSelectBot: (botId: string) => void;
  runtimeSettings: RuntimeSettings | null;
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

  const defaultWorker = runtimeSettings?.livekit_agent_name || 'voice-bot-justdial';
  const effectiveWorker = form.test_worker_agent_name || defaultWorker;

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
            <span>Dashboard agent</span>
            <select value={selectedBotId} onChange={(event) => onSelectBot(event.target.value)}>
              {bots.map((bot) => <option key={bot._id} value={bot._id}>{bot.name}</option>)}
            </select>
            <small>{selectedBot?.assistant_id || 'Select an agent to test'}</small>
          </div>
          <ChevronRight size={18} />
          <div>
            <span>LiveKit worker for this test</span>
            <strong>{effectiveWorker}</strong>
            <small>Override here. No SSH or server restart needed.</small>
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
          <button onClick={() => updateField('test_worker_agent_name', 'voice-bot-justdial-test')}>Use safe test worker</button>
          <button onClick={() => updateField('test_worker_agent_name', defaultWorker)}>Use saved default</button>
          <button onClick={() => setForm((current) => ({ ...current, call_id: `TEST-${Date.now()}` }))}>New call ID</button>
        </div>
        <div className="button-row">
          <button className={error ? 'fallback-button' : 'primary'} onClick={onStart} disabled={!selectedBot || status.includes('Creating') || status.includes('Connecting')}>
            <Play size={16} /> {error ? 'Fallback: retry setup' : status.includes('Creating') || status.includes('Connecting') ? 'Starting...' : 'Start WebRTC test'}
          </button>
          <button onClick={onStop}><Square size={16} /> End test</button>
        </div>
      </div>
      <div className="panel status-panel">
        <h2>Connection checklist</h2>
        <ConnectionLine icon={<Database />} label="Backend room" value={roomName || 'Not created'} done={Boolean(roomName)} />
        <ConnectionLine icon={<Bot />} label="Dispatched worker" value={effectiveWorker} done={Boolean(effectiveWorker)} />
        <ConnectionLine icon={<Wifi />} label="LiveKit socket" value={status} done={!status.toLowerCase().includes('failed') && status !== 'Idle'} />
        <ConnectionLine icon={<Mic />} label="Microphone" value={status.includes('Microphone') || remoteAudioReady ? 'Requested' : 'Waiting'} done={status.includes('Microphone') || remoteAudioReady} />
        <ConnectionLine icon={<Volume2 />} label="Bot audio" value={remoteAudioReady ? 'Connected' : 'Waiting'} done={remoteAudioReady} />
        <div ref={remoteAudioRef} />
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

function TranscriptsView({
  transcripts,
  selectedTranscript,
  callEvents,
  searchText,
  onSearchText,
  onSelect
}: {
  transcripts: Transcript[];
  selectedTranscript?: Transcript;
  callEvents: CallEvent[];
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
                <td>{item.transcript_count ?? item.transcript?.length ?? 0}</td>
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
            <div className="timeline-section">
              <div className="section-heading">
                <h3>Call timeline</h3>
                <span>{callEvents.length} events</span>
              </div>
              <div className="event-timeline">
                {callEvents.map((event) => (
                  <div className={`call-event ${event.severity || 'info'}`} key={event._id}>
                    <div className="event-time">{formatTime(event.created_at)}</div>
                    <div>
                      <strong>{event.event_type}</strong>
                      <p>{event.message}</p>
                      {event.details && Object.keys(event.details).length > 0 && (
                        <small>{summarizeDetails(event.details)}</small>
                      )}
                    </div>
                  </div>
                ))}
                {!callEvents.length && <p className="muted">No technical timeline events have been stored for this call yet.</p>}
              </div>
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

function SettingsView({
  runtimeSettings,
  onUpdateRuntime
}: {
  runtimeSettings: RuntimeSettings | null;
  onUpdateRuntime: (payload: Partial<RuntimeSettings>) => void;
}) {
  const [draft, setDraft] = useState({
    livekit_api_url: runtimeSettings?.livekit_api_url || '',
    livekit_browser_url: runtimeSettings?.livekit_browser_url || '',
    livekit_agent_name: runtimeSettings?.livekit_agent_name || ''
  });

  useEffect(() => {
    setDraft({
      livekit_api_url: runtimeSettings?.livekit_api_url || '',
      livekit_browser_url: runtimeSettings?.livekit_browser_url || '',
      livekit_agent_name: runtimeSettings?.livekit_agent_name || ''
    });
  }, [runtimeSettings?._id, runtimeSettings?.livekit_api_url, runtimeSettings?.livekit_browser_url, runtimeSettings?.livekit_agent_name]);

  return (
    <section className="content-grid two-col">
      <div className="panel">
        <div className="panel-header">
          <div>
            <h2>Runtime settings</h2>
            <p>These values control where dashboard test calls are created and which LiveKit worker receives them.</p>
          </div>
          <StatusPill value={runtimeSettings?.livekit_credentials_configured ? 'ready' : 'missing keys'} />
        </div>
        <div className="form-grid">
          <label>
            LiveKit API URL
            <input value={draft.livekit_api_url} onChange={(event) => setDraft({ ...draft, livekit_api_url: event.target.value })} />
          </label>
          <label>
            Browser WebSocket URL
            <input value={draft.livekit_browser_url} onChange={(event) => setDraft({ ...draft, livekit_browser_url: event.target.value })} />
          </label>
          <label>
            Test call worker agent name
            <input value={draft.livekit_agent_name} onChange={(event) => setDraft({ ...draft, livekit_agent_name: event.target.value })} />
          </label>
          <label>
            Secret keys
            <input value={runtimeSettings?.livekit_credentials_configured ? 'Configured in backend .env' : 'Missing in backend .env'} disabled />
          </label>
        </div>
        <div className="button-row">
          <button className="primary" onClick={() => onUpdateRuntime(draft)}><Save size={16} /> Save runtime settings</button>
        </div>
      </div>
      <div className="panel">
        <h2>Worker vs dashboard agent</h2>
        <div className="timeline">
          <Step title="Dashboard agent" text="The bot you create in the UI: prompt, voice, language and settings stored in Mongo." />
          <Step title="LiveKit worker" text="A Python process connected to LiveKit. It receives rooms for a specific agent name and runs bot.py." />
          <Step title="Safe testing" text="Use a separate worker name such as voice-bot-justdial-test so test calls do not route to live workers." />
        </div>
      </div>
    </section>
  );
}

const PHRASE_CATEGORY_META: { id: PhraseCategory; label: string; description: string }[] = [
  {
    id: 'voicemail',
    label: 'Voicemail phrases',
    description: 'When a call hits an automated answering machine. Adding more lines here makes the bot give up faster on dead numbers.'
  },
  {
    id: 'hold_music',
    label: 'Hold-music phrases',
    description: 'PBX/carrier messages played when a person picks up but parks the call. Adding regional language phrases helps detect these.'
  },
  {
    id: 'dnc_trigger',
    label: 'Do-not-call triggers',
    description: 'Phrases that mean the caller wants to be removed from outreach. Detection is used by the bot to end the call respectfully.'
  }
];

type LibrarySection = PhraseCategory | 'outcomes' | 'languages';

function LibraryView({
  phrases,
  outcomes,
  languageSettings,
  onCreate,
  onUpdate,
  onDelete,
  onUpdateOutcome,
  onUpsertLanguageSettings
}: {
  phrases: LibraryPhrase[];
  outcomes: OutcomeEntry[];
  languageSettings: LanguageSettings[];
  onCreate: (payload: Partial<LibraryPhrase>) => Promise<void> | void;
  onUpdate: (id: string, payload: Partial<LibraryPhrase>) => Promise<void> | void;
  onDelete: (id: string) => Promise<void> | void;
  onUpdateOutcome: (key: string, payload: Partial<OutcomeEntry>) => Promise<void> | void;
  onUpsertLanguageSettings: (payload: Partial<LanguageSettings>) => Promise<void> | void;
}) {
  const [activeSection, setActiveSection] = useState<LibrarySection>('voicemail');
  const [draftText, setDraftText] = useState('');
  const [draftLanguage, setDraftLanguage] = useState('en');
  const [draftNotes, setDraftNotes] = useState('');
  const [editingId, setEditingId] = useState<string>('');
  const [editingText, setEditingText] = useState('');
  const [editingLanguage, setEditingLanguage] = useState('');
  const [editingNotes, setEditingNotes] = useState('');

  const phraseMeta = PHRASE_CATEGORY_META.find((item) => item.id === activeSection as PhraseCategory);
  const meta = phraseMeta;
  const activeCategory = activeSection as PhraseCategory;
  const rows = phraseMeta ? phrases.filter((item) => item.category === activeSection) : [];

  function startEdit(row: LibraryPhrase) {
    setEditingId(row._id);
    setEditingText(row.text);
    setEditingLanguage(row.language || '');
    setEditingNotes(row.notes || '');
  }

  function cancelEdit() {
    setEditingId('');
    setEditingText('');
    setEditingLanguage('');
    setEditingNotes('');
  }

  async function submitNew() {
    if (!draftText.trim()) return;
    await onCreate({
      category: activeCategory,
      text: draftText.trim(),
      language: draftLanguage.trim(),
      notes: draftNotes.trim()
    });
    setDraftText('');
    setDraftNotes('');
  }

  async function submitEdit() {
    if (!editingId || !editingText.trim()) return;
    await onUpdate(editingId, {
      text: editingText.trim(),
      language: editingLanguage.trim(),
      notes: editingNotes.trim()
    });
    cancelEdit();
  }

  async function confirmDelete(row: LibraryPhrase) {
    const ok = window.confirm(`Delete this phrase?\n\n"${row.text}"\n\nCalls starting after the next refresh will stop detecting this line.`);
    if (!ok) return;
    await onDelete(row._id);
  }

  return (
    <section className="library-layout">
      <div className="library-tabs">
        {PHRASE_CATEGORY_META.map((item) => (
          <button
            key={item.id}
            className={item.id === activeSection ? 'library-tab active' : 'library-tab'}
            onClick={() => { setActiveSection(item.id); cancelEdit(); }}
          >
            <strong>{item.label}</strong>
            <small>{phrases.filter((p) => p.category === item.id).length} phrases</small>
          </button>
        ))}
        <button
          className={activeSection === 'outcomes' ? 'library-tab active' : 'library-tab'}
          onClick={() => { setActiveSection('outcomes'); cancelEdit(); }}
        >
          <strong>Call outcomes</strong>
          <small>{outcomes.length} categories</small>
        </button>
        <button
          className={activeSection === 'languages' ? 'library-tab active' : 'library-tab'}
          onClick={() => { setActiveSection('languages'); cancelEdit(); }}
        >
          <strong>Language settings</strong>
          <small>{languageSettings.length} languages</small>
        </button>
      </div>

      {activeSection === 'outcomes' ? (
        <OutcomeCatalog outcomes={outcomes} onUpdate={onUpdateOutcome} />
      ) : activeSection === 'languages' ? (
        <LanguageSettingsCatalog
          settings={languageSettings}
          onUpsert={onUpsertLanguageSettings}
        />
      ) : (
        <PhraseEditor
          meta={meta!}
          rows={rows}
          editingId={editingId}
          editingText={editingText}
          editingLanguage={editingLanguage}
          editingNotes={editingNotes}
          draftText={draftText}
          draftLanguage={draftLanguage}
          draftNotes={draftNotes}
          setDraftText={setDraftText}
          setDraftLanguage={setDraftLanguage}
          setDraftNotes={setDraftNotes}
          setEditingText={setEditingText}
          setEditingLanguage={setEditingLanguage}
          setEditingNotes={setEditingNotes}
          onStartEdit={startEdit}
          onCancelEdit={cancelEdit}
          onSubmitEdit={submitEdit}
          onSubmitNew={submitNew}
          onConfirmDelete={confirmDelete}
        />
      )}
    </section>
  );
}

function PhraseEditor({
  meta, rows, editingId,
  editingText, editingLanguage, editingNotes,
  draftText, draftLanguage, draftNotes,
  setDraftText, setDraftLanguage, setDraftNotes,
  setEditingText, setEditingLanguage, setEditingNotes,
  onStartEdit, onCancelEdit, onSubmitEdit, onSubmitNew, onConfirmDelete
}: {
  meta: { id: PhraseCategory; label: string; description: string };
  rows: LibraryPhrase[];
  editingId: string;
  editingText: string;
  editingLanguage: string;
  editingNotes: string;
  draftText: string;
  draftLanguage: string;
  draftNotes: string;
  setDraftText: (v: string) => void;
  setDraftLanguage: (v: string) => void;
  setDraftNotes: (v: string) => void;
  setEditingText: (v: string) => void;
  setEditingLanguage: (v: string) => void;
  setEditingNotes: (v: string) => void;
  onStartEdit: (row: LibraryPhrase) => void;
  onCancelEdit: () => void;
  onSubmitEdit: () => void;
  onSubmitNew: () => void;
  onConfirmDelete: (row: LibraryPhrase) => void;
}) {
  return (
    <>
      <div className="callout">
        <BookOpen size={18} />
        <div>
          <strong>{meta.label}</strong>
          <p>{meta.description}</p>
          <p className="muted">Edits go live within a minute. Currently-running calls keep using their original list.</p>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div>
            <h2>Add a new phrase</h2>
            <p>Type the words exactly as you'd expect them to appear in a transcript. Matching is case-insensitive substring.</p>
          </div>
          <Plus size={18} />
        </div>
        <div className="form-grid">
          <label className="full">
            Phrase text
            <input
              value={draftText}
              onChange={(event) => setDraftText(event.target.value)}
              placeholder='e.g. "please record your message after the tone"'
            />
          </label>
          <label>
            Language tag (optional)
            <input
              value={draftLanguage}
              onChange={(event) => setDraftLanguage(event.target.value)}
              placeholder="en, hi, gu, ta..."
            />
          </label>
          <label>
            Notes (optional)
            <input
              value={draftNotes}
              onChange={(event) => setDraftNotes(event.target.value)}
              placeholder="Where you saw this, or why you added it"
            />
          </label>
        </div>
        <div className="button-row">
          <button className="primary" disabled={!draftText.trim()} onClick={onSubmitNew}>
            <Plus size={16} /> Add to {meta.label}
          </button>
        </div>
      </div>

      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Saved {meta.label.toLowerCase()}</h2>
            <p>{rows.length} entries. Click a row to edit. Deleted entries stop matching new calls within a minute.</p>
          </div>
        </div>
        <table>
          <thead>
            <tr><th>Phrase</th><th>Language</th><th>Notes</th><th>Updated</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              editingId === row._id ? (
                <tr key={row._id} className="selected-row">
                  <td><input value={editingText} onChange={(event) => setEditingText(event.target.value)} /></td>
                  <td><input value={editingLanguage} onChange={(event) => setEditingLanguage(event.target.value)} style={{ width: 80 }} /></td>
                  <td><input value={editingNotes} onChange={(event) => setEditingNotes(event.target.value)} /></td>
                  <td>{formatDate(row.updated_at)}</td>
                  <td>
                    <div className="button-row">
                      <button className="primary" onClick={onSubmitEdit}><Save size={14} /> Save</button>
                      <button onClick={onCancelEdit}>Cancel</button>
                    </div>
                  </td>
                </tr>
              ) : (
                <tr key={row._id}>
                  <td><strong>{row.text}</strong>{row.created_by && <small>added by {row.created_by}</small>}</td>
                  <td><code>{row.language || '-'}</code></td>
                  <td><small>{row.notes || '-'}</small></td>
                  <td>{formatDate(row.updated_at)}</td>
                  <td>
                    <div className="button-row">
                      <button onClick={() => onStartEdit(row)}>Edit</button>
                      <button className="fallback-button" onClick={() => onConfirmDelete(row)}><Trash2 size={14} /> Delete</button>
                    </div>
                  </td>
                </tr>
              )
            ))}
            {!rows.length && <tr><td colSpan={5}>No phrases yet for this category. Add the first one above.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function OutcomeCatalog({
  outcomes,
  onUpdate
}: {
  outcomes: OutcomeEntry[];
  onUpdate: (key: string, payload: Partial<OutcomeEntry>) => Promise<void> | void;
}) {
  const [editingKey, setEditingKey] = useState('');
  const [draftLabel, setDraftLabel] = useState('');
  const [draftDescription, setDraftDescription] = useState('');

  function startEditOutcome(row: OutcomeEntry) {
    setEditingKey(row.key);
    setDraftLabel(row.display_label || row.key);
    setDraftDescription(row.description || '');
  }

  function cancelOutcomeEdit() {
    setEditingKey('');
    setDraftLabel('');
    setDraftDescription('');
  }

  async function submitOutcomeEdit() {
    if (!editingKey || !draftDescription.trim()) return;
    await onUpdate(editingKey, {
      display_label: draftLabel.trim() || editingKey,
      description: draftDescription.trim()
    });
    cancelOutcomeEdit();
  }

  return (
    <>
      <div className="callout">
        <BookOpen size={18} />
        <div>
          <strong>Call outcomes</strong>
          <p>These are the categories the AI uses to tag every call (Approved, Not Interested, Wrong Number, etc.). The key is fixed because downstream systems use it, but you can edit the description that teaches the AI when to pick each one.</p>
          <p className="muted">Edits take effect on calls analyzed after a 60-second cache refresh.</p>
        </div>
      </div>
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>{outcomes.length} outcome categories</h2>
            <p>The longer and clearer the description, the better the AI gets at picking the right label.</p>
          </div>
        </div>
        <table>
          <thead>
            <tr>
              <th style={{ width: 220 }}>Key</th>
              <th style={{ width: 180 }}>Display label</th>
              <th>Description (sent to the AI)</th>
              <th>Updated</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {outcomes.map((row) => (
              editingKey === row.key ? (
                <tr key={row.key} className="selected-row">
                  <td><code>{row.key}</code></td>
                  <td><input value={draftLabel} onChange={(e) => setDraftLabel(e.target.value)} /></td>
                  <td><textarea rows={4} value={draftDescription} onChange={(e) => setDraftDescription(e.target.value)} /></td>
                  <td>{formatDate(row.updated_at)}</td>
                  <td>
                    <div className="button-row">
                      <button className="primary" onClick={submitOutcomeEdit}><Save size={14} /> Save</button>
                      <button onClick={cancelOutcomeEdit}>Cancel</button>
                    </div>
                  </td>
                </tr>
              ) : (
                <tr key={row.key}>
                  <td><code>{row.key}</code></td>
                  <td><strong>{row.display_label || row.key}</strong></td>
                  <td><small>{row.description}</small></td>
                  <td>{formatDate(row.updated_at)}</td>
                  <td><button onClick={() => startEditOutcome(row)}>Edit</button></td>
                </tr>
              )
            ))}
            {!outcomes.length && <tr><td colSpan={5}>Outcome catalog is empty. The backend seeds defaults on next startup.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function LanguageSettingsCatalog({
  settings,
  onUpsert
}: {
  settings: LanguageSettings[];
  onUpsert: (payload: Partial<LanguageSettings>) => Promise<void> | void;
}) {
  const [activeId, setActiveId] = useState<string>(settings[0]?.id || '');
  const current = settings.find((item) => item.id === activeId) || settings[0];
  const [draft, setDraft] = useState<LanguageSettings>({
    id: current?.id || '',
    name: current?.name || '',
    timeout_message: current?.timeout_message || '',
    inactivity_nudge: current?.inactivity_nudge || '',
    lang_notes: current?.lang_notes || ''
  });
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!creating) {
      setDraft({
        id: current?.id || '',
        name: current?.name || '',
        timeout_message: current?.timeout_message || '',
        inactivity_nudge: current?.inactivity_nudge || '',
        lang_notes: current?.lang_notes || ''
      });
    }
  }, [current?.id, current?.name, current?.timeout_message, current?.inactivity_nudge, current?.lang_notes, creating]);

  function startNew() {
    setCreating(true);
    setActiveId('');
    setDraft({
      id: '',
      name: '',
      timeout_message: '',
      inactivity_nudge: '',
      lang_notes: ''
    });
  }

  function cancelNew() {
    setCreating(false);
    if (settings[0]) setActiveId(settings[0].id);
  }

  async function save() {
    if (!draft.id.trim() || !draft.name.trim()) return;
    await onUpsert(draft);
    setCreating(false);
    setActiveId(draft.id);
  }

  return (
    <>
      <div className="callout">
        <BookOpen size={18} />
        <div>
          <strong>Per-language settings</strong>
          <p>Edit the messages and style notes the bot uses when speaking each language. The "timeout message" is what the bot says when the 5-minute call timer fires. The "style notes" go into the system prompt to set tone and word choice.</p>
          <p className="muted">Changes apply to new calls within ~60s. Live calls finish on the snapshot they started with.</p>
        </div>
      </div>

      <div className="content-grid two-col">
        <div className="table-panel">
          <div className="panel-header">
            <div>
              <h2>Languages</h2>
              <p>{settings.length} configured. Pick one to edit or add a new language.</p>
            </div>
            <button onClick={startNew}><Plus size={16} /> New language</button>
          </div>
          <table>
            <thead>
              <tr><th>ID</th><th>Display name</th><th>Updated</th></tr>
            </thead>
            <tbody>
              {settings.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => { setCreating(false); setActiveId(row.id); }}
                  className={!creating && row.id === activeId ? 'selected-row' : ''}
                >
                  <td><code>{row.id}</code></td>
                  <td><strong>{row.name}</strong></td>
                  <td>{formatDate(row.updated_at)}</td>
                </tr>
              ))}
              {!settings.length && <tr><td colSpan={3}>No languages yet. Click "New language" to add the first.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>{creating ? 'New language' : (current ? `Editing ${current.name}` : 'Select a language')}</h2>
              <p>The ID is used as a key in Mongo. Keep it lowercase and stable (e.g. "hindi", "tamil"). The display name is what the dashboard shows.</p>
            </div>
          </div>
          <div className="form-section">
            <div className="form-grid">
              <label>
                ID
                <input
                  value={draft.id}
                  onChange={(e) => setDraft({ ...draft, id: e.target.value })}
                  disabled={!creating}
                  placeholder="lowercase, no spaces"
                />
              </label>
              <label>
                Display name
                <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
              </label>
            </div>
            <label className="full">
              Timeout message (what the bot says at the 5-min hard timeout)
              <textarea
                rows={3}
                value={draft.timeout_message || ''}
                onChange={(e) => setDraft({ ...draft, timeout_message: e.target.value })}
              />
            </label>
            <label className="full">
              Inactivity nudge (what the bot says when the caller goes silent)
              <textarea
                rows={2}
                value={draft.inactivity_nudge || ''}
                onChange={(e) => setDraft({ ...draft, inactivity_nudge: e.target.value })}
              />
            </label>
            <label className="full">
              Language style notes (added to the system prompt; describes tone, fillers, and example phrasing)
              <textarea
                rows={10}
                value={draft.lang_notes || ''}
                onChange={(e) => setDraft({ ...draft, lang_notes: e.target.value })}
              />
            </label>
          </div>
          <div className="button-row">
            <button
              className="primary"
              onClick={save}
              disabled={!draft.id.trim() || !draft.name.trim()}
            >
              <Save size={16} /> Save
            </button>
            {creating && <button onClick={cancelNew}>Cancel</button>}
          </div>
        </div>
      </div>
    </>
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
    observability: 'Observability',
    library: 'Phrase Library',
    settings: 'Settings'
  }[view];
}

function subtitleFor(view: View) {
  return {
    bots: 'Manage outbound-first voice agents and active published versions.',
    builder: 'Edit prompts, voice, language, and runtime settings safely.',
    campaigns: 'Connect one bot to one campaign and its lead/callback APIs.',
    test: 'Start a controlled browser call with helpful connection diagnostics.',
    transcripts: 'Inspect raw call transcripts, outcomes, and config snapshots.',
    observability: 'Track LiveKit health, Gemini latency, TTFW, and callback failures.',
    library: 'Edit voicemail, hold-music, and DNC trigger phrases without a code deploy.',
    settings: 'Control LiveKit routing and dashboard runtime options.'
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
  if (raw.toLowerCase().includes('network timeout')) {
    return `${raw}. The backend may be slow, down, or blocked by Mongo/LiveKit connectivity.`;
  }
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

function formatTime(value?: string) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function summarizeDetails(details: Record<string, unknown>) {
  return Object.entries(details)
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
    .join(' | ');
}

function average(values: number[]) {
  if (!values.length) return 0;
  return Math.round(values.reduce((sum, item) => sum + item, 0) / values.length);
}

function buildDiagnostic(scope: string, error: unknown, action?: string, severity: DiagnosticSeverity = 'error'): Diagnostic {
  const message = friendlyApiError(error);
  const normalized = message.length > 220 ? `${message.slice(0, 220)}...` : message;
  return {
    id: `${scope}-${Date.now()}`,
    scope,
    severity,
    message: normalized,
    action,
    createdAt: new Date().toISOString()
  };
}

const CACHE_VERSION = 'v2';
const CACHE_PREFIX = `jd-vb:${CACHE_VERSION}:`;

function cacheSet<T>(key: string, value: T) {
  try {
    window.localStorage.setItem(`${CACHE_PREFIX}${key}`, JSON.stringify(value));
  } catch {
    // Cache is best-effort only.
  }
}

function cacheGet<T>(key: string, fallback: T): T {
  try {
    const value = window.localStorage.getItem(`${CACHE_PREFIX}${key}`);
    return value ? JSON.parse(value) as T : fallback;
  } catch {
    return fallback;
  }
}

function pruneOldCache() {
  try {
    const drop: string[] = [];
    for (let i = 0; i < window.localStorage.length; i++) {
      const key = window.localStorage.key(i);
      if (key && key.startsWith('jd-vb:') && !key.startsWith(CACHE_PREFIX)) drop.push(key);
    }
    drop.forEach((key) => window.localStorage.removeItem(key));
  } catch {
    // ignore
  }
}

pruneOldCache();

createRoot(document.getElementById('root')!).render(
  <ResilientPanel name="Dashboard Shell" onDiagnostic={() => undefined}>
    <App />
  </ResilientPanel>
);
