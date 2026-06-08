import React, { Component, ErrorInfo, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  Ban,
  BarChart2,
  Bot,
  BookOpen,
  Braces,
  Check,
  Pencil,
  CheckCircle2,
  ChevronRight,
  Clock3,
  ClipboardList,
  Copy,
  Database,
  Download,
  FileText,
  Filter,
  Gauge,
  GitBranch,
  Headphones,
  Keyboard,
  Layers,
  Megaphone,
  MessageSquareText,
  Mic,
  Pause,
  PhoneCall,
  PhoneOff,
  Play,
  Plus,
  RefreshCw,
  Rocket,
  Save,
  Search,
  SendHorizontal,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  Trash2,
  Volume2,
  Wand2,
  Wifi,
  XCircle
} from 'lucide-react';
import {
  api,
  apiUrl,
  AttemptStep,
  Bot as BotType,
  BotVersion,
  CallWindow,
  Campaign,
  CallEvent,
  DialingStrategy,
  LangfuseSettings,
  LanguageOption,
  LanguageSettings,
  LibraryPhrase,
  OutcomeAnalytics,
  OutcomeEntry,
  OutcomeRule,
  PhraseCategory,
  QualityAlert,
  RuntimeSettings,
  TestRecordingLookup,
  Transcript,
  VoiceOption
} from './api';
import './styles.css';

type View = 'bots' | 'builder' | 'campaigns' | 'test' | 'transcripts' | 'analytics' | 'observability' | 'library' | 'settings';
type AgentWorkspaceMode = 'list' | 'builder';
type BuilderMode = 'pm' | 'advanced';
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
  agent_name?: string;
  organization_name?: string;
  ai_partner?: string;
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
  inactivity_end_text?: string;
  function_calling?: boolean;
  gemini_silence_duration_ms?: number;
  gemini_prefix_padding_ms?: number;
  post_speech_hold_ms?: number;
  recording?: {
    service_id?: number | string;
    dialer_city?: string;
  };
  prompt_config?: Record<string, unknown>;
  functions?: unknown[];
  close_markers?: string[];
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
  /** Pinned version id — empty string means "use active published version" */
  test_bot_version_id: string;
  custom_lead_json: string;
};

const defaultConfig: RuntimeConfig = {
  agent_name: '',
  organization_name: '',
  ai_partner: '',
  model: 'gemini-3.1-flash-live-preview',
  voice: 'Aoede',
  language: 'hindi',
  livekit_language: 'hi-IN',
  temperature: 0.7,
  max_call_duration: 300,
  system_prompt: 'You are a warm and professional call center agent. Greet the caller, understand their requirement, and collect key details.',
  initial_message: 'हेलो, मैं {agent_name} बोल रही हूँ {organization_name} से — आपको {product} की requirement है ना?',
  call_end_text: 'ठीक है जी, सारी details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए शुक्रिया.',
  inactivity_end_text: '',
  function_calling: true,
  gemini_silence_duration_ms: 1800,
  gemini_prefix_padding_ms: 300,
  post_speech_hold_ms: 800,
  recording: {
    service_id: 293,
    dialer_city: 'bangalore'
  }
};

function App() {
  const [view, setView] = useState<View>('bots');
  const [agentWorkspaceMode, setAgentWorkspaceMode] = useState<AgentWorkspaceMode>('list');
  const [campaignWorkspaceMode, setCampaignWorkspaceMode] = useState<'list' | 'strategy'>('list');
  const [selectedCampaignKey, setSelectedCampaignKey] = useState('');
  const [bots, setBots] = useState<BotType[]>([]);
  const [selectedBotId, setSelectedBotId] = useState('');
  const [versions, setVersions] = useState<BotVersion[]>([]);
  const [editingVersionId, setEditingVersionId] = useState<string | undefined>();
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
  const [selectedTranscriptDetail, setSelectedTranscriptDetail] = useState<Transcript | null>(null);
  const [localRecordingByRoom, setLocalRecordingByRoom] = useState<Record<string, TestRecordingLookup>>({});
  const [callEvents, setCallEvents] = useState<CallEvent[]>([]);
  const [selectedTranscriptId, setSelectedTranscriptId] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([]);
  const [actionState, setActionState] = useState<Record<string, 'idle' | 'running' | 'failed'>>({});
  const [searchText, setSearchText] = useState('');
  const [transcriptFilters, setTranscriptFilters] = useState<{
    status: string; outcome: string; campaign_id: string; bot_id: string;
    start_date: string; end_date: string;
  }>({ status: '', outcome: '', campaign_id: '', bot_id: '', start_date: '', end_date: '' });
  const [testForm, setTestForm] = useState<TestForm>({
    campaign_id: 'test',
    lead_id: '',
    call_id: `TEST-${Date.now()}`,
    mobile: '',
    srchterm: 'air conditioner',
    buyer_name: 'Test User',
    city: 'Mumbai',
    test_worker_agent_name: '',
    test_bot_version_id: '',
    custom_lead_json: ''
  });
  const [testStatus, setTestStatus] = useState('Idle');
  const [testError, setTestError] = useState('');
  const [testCloseNote, setTestCloseNote] = useState('');
  const [testRoomName, setTestRoomName] = useState('');
  const [testChatMessage, setTestChatMessage] = useState('');
  const [micEnabled, setMicEnabled] = useState(false);
  const [builderMode, setBuilderMode] = useState<BuilderMode>(
    () => (localStorage.getItem('builderMode') as BuilderMode) || 'pm'
  );
  const [deleteCandidate, setDeleteCandidate] = useState<BotType | null>(null);
  const [showNewAgentWizard, setShowNewAgentWizard] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [showCmdK, setShowCmdK] = useState(false);
  const [diffVersionIds, setDiffVersionIds] = useState<[string, string] | null>(null);
  const [showPublishConfirm, setShowPublishConfirm] = useState(false);
  const [newAgentForm, setNewAgentForm] = useState({
    name: '',
    description: '',
    agent_name: '',
    organization_name: '',
    language: 'hindi',
    voice: 'Aoede',
    initial_message: 'हेलो, मैं {agent_name} बोल रही हूँ {organization_name} से — आपको {product} की requirement है ना?'
  });
  const [remoteAudioReady, setRemoteAudioReady] = useState(false);
  const workspaceRef = useRef<HTMLElement>(null);
  const scrollPositions = useRef<Partial<Record<View, number>>>({});
  const livekitRoomRef = useRef<Room | null>(null);
  const localTrackRef = useRef<LocalAudioTrack | null>(null);
  const remoteAudioRef = useRef<HTMLDivElement | null>(null);
  const subscribedTracksRef = useRef<Set<RemoteTrack>>(new Set());
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recordingChunksRef = useRef<Blob[]>([]);
  const recordingAudioContextRef = useRef<AudioContext | null>(null);
  const recordingDestinationRef = useRef<MediaStreamAudioDestinationNode | null>(null);
  const recordingSourceNodesRef = useRef<MediaStreamAudioSourceNode[]>([]);
  const recordingFinalizedRoomsRef = useRef<Set<string>>(new Set());

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
    () => selectedTranscriptDetail || transcripts.find((item) => item._id === selectedTranscriptId) || transcripts[0],
    [selectedTranscriptDetail, transcripts, selectedTranscriptId]
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
        if (preferred) {
          setConfigText(JSON.stringify(preferred.config, null, 2));
          setEditingVersionId(preferred._id);
        }
      })
      .catch((error) => reportDiagnostic('Bot Versions API', error, 'Keep editing cached config or retry refresh'));
  }, [selectedBot?._id]);

  useEffect(() => {
    if (!selectedTranscriptId) {
      setSelectedTranscriptDetail(null);
      setCallEvents([]);
      return;
    }
    setSelectedTranscriptDetail(null);
    api.transcript(selectedTranscriptId)
      .then((transcript) => {
        setSelectedTranscriptDetail(transcript);
        clearDiagnostic('Transcript Detail API');
        if (!transcript.recording_url && transcript.room_name) {
          api.testRecording(transcript.room_name)
            .then((recording) => {
              setLocalRecordingByRoom((current) => ({ ...current, [transcript.room_name || '']: recording }));
            })
            .catch(() => {
              setLocalRecordingByRoom((current) => {
                if (!transcript.room_name || !current[transcript.room_name]) return current;
                const next = { ...current };
                delete next[transcript.room_name];
                return next;
              });
            });
        }
      })
      .catch((error) => {
        setSelectedTranscriptDetail(null);
        reportDiagnostic('Transcript Detail API', error, 'Use the transcript list data, then retry refresh after the call ends');
      });
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

  function openNewAgentWizard() {
    setNewAgentForm({
      name: '',
      description: '',
      agent_name: '',
      organization_name: '',
      language: 'hindi',
      voice: voices[0]?.id || 'Aoede',
      initial_message: 'हेलो, मैं {agent_name} बोल रही हूँ {organization_name} से — आपको {product} की requirement है ना?'
    });
    setShowNewAgentWizard(true);
  }

  async function createBot() {
    const lang = languages.find((l) => l.id === newAgentForm.language);
    const config: RuntimeConfig = {
      ...defaultConfig,
      agent_name: newAgentForm.agent_name.trim(),
      organization_name: newAgentForm.organization_name.trim(),
      voice: newAgentForm.voice,
      language: newAgentForm.language,
      livekit_language: lang?.livekit_code || defaultConfig.livekit_language,
      sarvam_language: lang?.sarvam_code || lang?.livekit_code || defaultConfig.livekit_language,
      initial_message: newAgentForm.initial_message.trim() || defaultConfig.initial_message,
      system_prompt: `You are ${newAgentForm.agent_name || 'a call center agent'}, a warm and professional outbound agent for ${newAgentForm.organization_name || 'the company'}. Greet the caller, understand their requirement for {product}, and collect key qualification details.`
    };
    await runAction('createBot', 'Create Bot', async () => {
      const bot = await api.createBot({
        name: newAgentForm.name.trim() || 'New Voice Agent',
        description: newAgentForm.description.trim() || 'Outbound voice agent',
        orchestration: { mode: 'prompt_settings', flow_provider: null, flow_id: null },
        config
      });
      setSelectedBotId(bot._id);
      setView('bots');
      setAgentWorkspaceMode('builder');
      setShowNewAgentWizard(false);
      setMessage('Draft bot created. Review the prompt, save draft, then publish.');
      await refresh();
    });
  }

  async function deleteAgent(bot: BotType) {
    await runAction('deleteAgent', 'Delete Agent', async () => {
      await api.deleteBot(bot._id);
      setDeleteCandidate(null);
      setAgentWorkspaceMode('list');
      if (selectedBotId === bot._id) {
        const nextBot = bots.find((item) => item._id !== bot._id);
        setSelectedBotId(nextBot?._id || '');
      }
      setMessage(`Deleted ${bot.name}. Historical transcripts remain available for audit.`);
      await refresh();
    });
  }

  /** Re-fetch the selected bot's full version list and update React state. */
  async function refreshBotVersions(opts?: { selectVersionId?: string }) {
    if (!selectedBot) return;
    try {
      const bundle = await api.bot(selectedBot._id);
      setVersions(bundle.versions);
      if (opts?.selectVersionId) {
        const target = bundle.versions.find((v) => v._id === opts.selectVersionId);
        if (target) {
          setEditingVersionId(target._id);
          setConfigText(JSON.stringify(target.config, null, 2));
        }
      }
    } catch (error) {
      reportDiagnostic('Bot Versions API', error, 'Retry or continue with current data');
    }
  }

  async function saveDraft() {
    if (!selectedBot || !parsedConfig.ok) return;
    await runAction('saveDraft', 'Save Draft', async () => {
      const version = await api.saveDraft(selectedBot._id, {
        config: parsedConfig.value,
        notes: 'Dashboard draft save'
      });
      setMessage(`Draft v${version.version} saved. Publish it when ready for new calls.`);
      // Reload versions so the new draft appears in the history panel and is
      // immediately selectable with its correct (fresh) config.
      await refreshBotVersions({ selectVersionId: version._id });
    });
  }

  async function renameBotMeta(name: string, description: string) {
    if (!selectedBot) return;
    await runAction('renameBotMeta', 'Rename Agent', async () => {
      await api.updateBotMeta(selectedBot._id, {
        name: name.trim() || selectedBot.name,
        description: description.trim()
      });
      setMessage(`Agent renamed to "${name.trim() || selectedBot.name}".`);
      await refresh();
    });
  }

  function selectVersion(version: BotVersion) {
    setEditingVersionId(version._id);
    setConfigText(JSON.stringify(version.config, null, 2));
  }

  async function updateVersionAction(versionId: string) {
    if (!selectedBot || !parsedConfig.ok) return;
    await runAction('updateVersion', 'Update Version', async () => {
      const updatedVersion = await api.updateVersion(selectedBot._id, versionId, {
        config: parsedConfig.value,
      });
      // Patch the in-memory versions list with the fresh version document so
      // clicking the version row in history reloads the SAVED config, not stale state.
      setVersions((prev) => prev.map((v) => v._id === updatedVersion._id ? updatedVersion : v));
      setMessage(`v${updatedVersion.version} updated.`);
      // configText intentionally NOT reset — user's current edits remain visible.
    });
  }

  async function doPublishDraft() {
    if (!selectedBot) return;
    await runAction('publishDraft', 'Publish Bot', async () => {
      const version = await api.publish(selectedBot._id, latestDraft?._id);
      setMessage(`Published v${version.version}. Live calls keep their old snapshot; new calls use this version.`);
      // Reload versions so published/draft states are accurate in the history panel.
      await refreshBotVersions();
      await refresh();
    });
  }

  function publishDraft() {
    setShowPublishConfirm(true);
  }

  const liveCallsByVersion = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const t of transcripts) {
      if (t.bot_version_id && t.status && !['completed', 'failed', 'cancelled', 'dnc'].includes(t.status)) {
        counts[t.bot_version_id] = (counts[t.bot_version_id] || 0) + 1;
      }
    }
    return counts;
  }, [transcripts]);

  async function unpublishActive() {
    if (!selectedBot) return;
    await runAction('unpublishActive', 'Unpublish', async () => {
      const version = await api.unpublish(selectedBot._id);
      setMessage(`v${version.version} moved back to draft. Edit it and re-publish when ready.`);
      await refreshBotVersions({ selectVersionId: version._id });
      await refresh();
    });
  }

  async function rollbackToVersion(versionId: string) {
    if (!selectedBot) return;
    await runAction(`rollback-${versionId}`, 'Rollback', async () => {
      const result = await api.rollbackVersion(selectedBot._id, versionId);
      setMessage(`Rolled back to v${result.active_version.version}. New calls now use this version.`);
      await refreshBotVersions();
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

  async function assignBotToCampaign(campaignKey: string, botId: string) {
    await runAction(`assignBot-${campaignKey}`, 'Assign Bot', async () => {
      const campaign = campaigns.find(c => c.campaign_key === campaignKey);
      await api.upsertCampaign({ campaign_key: campaignKey, name: campaign?.name || campaignKey, bot_id: botId });
      const next = await api.campaigns();
      setCampaigns(next);
      cacheSet('campaigns', next);
      setMessage(`Bot assigned to campaign "${campaignKey}".`);
    });
  }

  async function setCampaignStatus(campaignKey: string, status: string) {
    await runAction(`campaignStatus-${campaignKey}`, 'Campaign Status', async () => {
      await api.setCampaignStatus(campaignKey, status);
      const next = await api.campaigns();
      setCampaigns(next);
      cacheSet('campaigns', next);
      setMessage(`Campaign "${campaignKey}" is now ${status}.`);
    });
  }

  async function saveCampaignStrategy(campaignKey: string, campaignName: string, strategy: DialingStrategy) {
    await runAction('campaignStrategy', 'Campaign Strategy', async () => {
      await api.upsertCampaign({ campaign_key: campaignKey, name: campaignName, dialing_strategy: strategy });
      const next = await api.campaigns();
      setCampaigns(next);
      cacheSet('campaigns', next);
      clearDiagnostic('Campaigns API');
      setMessage(`Dialing strategy saved for "${campaignName}".`);
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
    setTestCloseNote('');
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
      room.on(RoomEvent.Disconnected, () => {
        setTestStatus('Disconnected');
        finalizeTestRecording(session.room_name, 'LiveKit disconnected.').catch((error) => {
          reportDiagnostic('Test Recording', friendlyTestError(error), 'The call ended before manual stop; recording finalization failed.');
        });
      });
      room.on(RoomEvent.Reconnecting, () => setTestStatus('Reconnecting to LiveKit...'));
      room.on(RoomEvent.Reconnected, () => setTestStatus('Reconnected. Continue testing.'));
      room.on(RoomEvent.ParticipantConnected, (participant) => {
        setTestStatus(`Thinking. Agent joined: ${participant.identity}`);
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
        const mediaStream = element.srcObject instanceof MediaStream ? element.srcObject : null;
        mediaStream?.getAudioTracks().forEach((audioTrack) => addTrackToTestRecording(audioTrack));
        subscribedTracksRef.current.add(track);
        setRemoteAudioReady(true);
        setTestStatus('Speaking. Bot audio connected.');
      });
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        for (const element of track.detach()) element.remove();
        subscribedTracksRef.current.delete(track);
        setTestStatus('Listening. Speak into your microphone.');
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
      await startTestRecording(session.room_name, micTrack.mediaStreamTrack);
      await room.localParticipant.publishTrack(micTrack);
      setMicEnabled(true);
      setTestStatus('Listening. Microphone is live.');
    } catch (error) {
      await stopWebRtcTest();
      const message = friendlyTestError(error);
      setTestError(message);
      reportDiagnostic('WebRTC Test Call', message, 'Retry room setup or skip LiveKit and keep editing metadata');
      setTestStatus('Failed to start test');
    }
  }

  async function startTestRecording(roomName: string, micTrack: MediaStreamTrack) {
    await stopTestRecording().catch(() => null);
    if (!window.MediaRecorder) {
      reportDiagnostic('Test Recording', 'This browser does not support MediaRecorder.', 'Use a Chromium-based browser for local test-call recordings.');
      return;
    }
    recordingChunksRef.current = [];
    const preferredType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : 'audio/webm';
    try {
      const audioContext = new AudioContext();
      const destination = audioContext.createMediaStreamDestination();
      recordingAudioContextRef.current = audioContext;
      recordingDestinationRef.current = destination;
      recordingSourceNodesRef.current = [];
      addTrackToTestRecording(micTrack);
      await audioContext.resume();
      const recorder = new MediaRecorder(destination.stream, { mimeType: preferredType });
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) recordingChunksRef.current.push(event.data);
      };
      recorder.onerror = (event) => {
        const recorderError = 'error' in event && event.error instanceof Error ? `: ${event.error.message}` : '';
        reportDiagnostic('Test Recording', `Browser recording failed during the test call${recorderError}.`, 'The call can continue, but this test may not have a playable local recording.');
      };
      recorder.onstart = () => {
        clearDiagnostic('Test Recording');
      };
      recorderRef.current = recorder;
      recorder.start(1000);
      setTestCloseNote(`Recording locally for ${shortId(roomName)}.`);
    } catch (error) {
      reportDiagnostic('Test Recording', friendlyTestError(error), 'The call can continue, but this test may not have a playable local recording.');
    }
  }

  function addTrackToTestRecording(track: MediaStreamTrack) {
    const audioContext = recordingAudioContextRef.current;
    const destination = recordingDestinationRef.current;
    if (!audioContext || !destination || track.kind !== 'audio') return;
    try {
      const source = audioContext.createMediaStreamSource(new MediaStream([track]));
      source.connect(destination);
      recordingSourceNodesRef.current.push(source);
    } catch (error) {
      reportDiagnostic('Test Recording', friendlyTestError(error), 'Recording will continue with the audio tracks that were already available.');
    }
  }

  async function stopTestRecording(): Promise<Blob | null> {
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (!recorder) return null;
    if (recorder.state !== 'inactive') {
      const stopped = new Promise<void>((resolve) => {
        recorder.onstop = () => resolve();
      });
      try { recorder.requestData(); } catch { /* not supported in every recorder state */ }
      recorder.stop();
      await stopped;
    }
    const chunks = recordingChunksRef.current;
    recordingChunksRef.current = [];
    for (const source of recordingSourceNodesRef.current) {
      try { source.disconnect(); } catch { /* best-effort cleanup */ }
    }
    recordingSourceNodesRef.current = [];
    recordingDestinationRef.current = null;
    if (recordingAudioContextRef.current) {
      try { await recordingAudioContextRef.current.close(); } catch { /* best-effort cleanup */ }
      recordingAudioContextRef.current = null;
    }
    if (!chunks.length) {
      reportDiagnostic('Test Recording', 'No recording data was captured by the browser.', 'Retry after a hard refresh. If this repeats, use Chrome and keep the tab active during the call.');
      return null;
    }
    return new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
  }

  async function finalizeTestRecording(roomName: string, prefix: string) {
    if (!roomName || recordingFinalizedRoomsRef.current.has(roomName)) return null;
    recordingFinalizedRoomsRef.current.add(roomName);
    const recordingBlob = await stopTestRecording();
    if (!recordingBlob) return null;
    const upload = await api.uploadTestRecording(roomName, recordingBlob);
    const attachNote = upload.transcripts_updated
      ? 'Recording saved and attached to transcript.'
      : 'Recording saved locally; refresh transcripts after the bot finishes saving.';
    setTestCloseNote(`${prefix} ${attachNote}`);
    return upload;
  }

  async function stopWebRtcTest() {
    const roomName = testRoomName;
    const recordingUpload = roomName
      ? await finalizeTestRecording(roomName, 'Manual stop.')
      : null;
    localTrackRef.current?.stop();
    localTrackRef.current = null;
    setMicEnabled(false);
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
        const closeResult = await api.closeWebRtcTestSession(roomName);
        if (recordingUpload) {
          const closeNote = closeResult.status === 'already_closed'
            ? 'LiveKit room was already closed.'
            : 'LiveKit close requested.';
          setTestCloseNote(`${closeNote} Recording saved locally.`);
        } else {
          setTestCloseNote(
            closeResult.status === 'already_closed'
              ? 'LiveKit room was already closed. Refresh transcripts after a few seconds.'
              : 'LiveKit close requested. Refresh transcripts after a few seconds.'
          );
        }
      } catch (error) {
        reportDiagnostic(
          'WebRTC Test Call',
          friendlyTestError(error),
          'The browser disconnected locally. Retry Stop once, then check worker logs if the transcript is missing.'
        );
        setTestCloseNote('Browser disconnected locally, but the backend could not confirm LiveKit room closure.');
      }
    }
    setRemoteAudioReady(false);
    setTestRoomName('');
    setTestStatus('Idle');
  }

  async function toggleTestMic() {
    const nextEnabled = !micEnabled;
    try {
      if (localTrackRef.current) {
        if (nextEnabled) {
          await localTrackRef.current.unmute();
        } else {
          await localTrackRef.current.mute();
        }
      } else if (livekitRoomRef.current) {
        await livekitRoomRef.current.localParticipant.setMicrophoneEnabled(nextEnabled);
      }
      setMicEnabled(nextEnabled);
      setTestStatus(nextEnabled ? 'Listening. Microphone is live.' : 'Listening. Microphone muted.');
    } catch (error) {
      reportDiagnostic('WebRTC Mic Control', error, 'Check microphone permission and retry the control');
    }
  }

  async function sendTestChatMessage(messageText: string) {
    const trimmed = messageText.trim();
    if (!trimmed || !livekitRoomRef.current) return;
    try {
      await livekitRoomRef.current.localParticipant.sendText(trimmed, { topic: 'dashboard-test-chat' });
      setTestChatMessage('');
      setTestStatus('Thinking. Chat message sent to the LiveKit room.');
    } catch (error) {
      reportDiagnostic('WebRTC Chat', error, 'The agent may not consume text chat yet; voice testing still works');
    }
  }

  const hasError = diagnostics.some((item) => item.severity === 'error');

  const navigate = useCallback((nextView: View) => {
    if (workspaceRef.current) {
      scrollPositions.current[view] = workspaceRef.current.scrollTop;
    }
    setView(nextView);
    if (nextView !== 'bots') setAgentWorkspaceMode('list');
    requestAnimationFrame(() => {
      if (workspaceRef.current) {
        workspaceRef.current.scrollTop = scrollPositions.current[nextView] ?? 0;
      }
    });
  }, [view]); // view needed so scroll-save captures current view correctly

  const closeTopModal = useCallback(() => {
    if (showCmdK) { setShowCmdK(false); return; }
    if (showShortcuts) { setShowShortcuts(false); return; }
    if (showPublishConfirm) { setShowPublishConfirm(false); return; }
    if (diffVersionIds) { setDiffVersionIds(null); return; }
    if (showNewAgentWizard) { setShowNewAgentWizard(false); return; }
    if (deleteCandidate) { setDeleteCandidate(null); return; }
  }, [showCmdK, showShortcuts, showPublishConfirm, diffVersionIds, showNewAgentWizard, deleteCandidate]);

  useKeyboardShortcuts(navigate, () => setShowShortcuts(v => !v), closeTopModal);

  // Cmd+K / Ctrl+K — open command palette
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setShowCmdK(v => !v);
      }
    }
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  return (
    <div className="app-shell">
      {/* ── Desktop sidebar ── */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="brand-mark"><Mic size={16} /></div>
          <div className="brand-text">
            <strong>Voice AI</strong>
            <span>No-code ops</span>
          </div>
        </div>

        <SidebarNav>
          <NavItem icon={<Bot />} label="Agents" active={view === 'bots'} onClick={() => navigate('bots')} />
          <NavItem icon={<Megaphone />} label="Campaigns" active={view === 'campaigns'} onClick={() => navigate('campaigns')} />
          <NavItem icon={<Play />} label="Test Call" active={view === 'test'} onClick={() => navigate('test')} />
          <NavItem icon={<FileText />} label="Transcripts" active={view === 'transcripts'} onClick={() => navigate('transcripts')} />
          <NavItem icon={<BarChart2 />} label="Analytics" active={view === 'analytics'} onClick={() => navigate('analytics')} />
          <NavItem icon={<Gauge />} label="Observability" active={view === 'observability'} onClick={() => navigate('observability')} />
          <NavItem icon={<BookOpen />} label="Library" active={view === 'library'} onClick={() => navigate('library')} />
          <NavItem icon={<ClipboardList />} label="Settings" active={view === 'settings'} onClick={() => navigate('settings')} />
        </SidebarNav>

        <div className="sidebar-footer">
          <span className={`status-dot ${hasError ? 'error' : ''}`} />
          <div className="sidebar-footer-text">
            <strong>{diagnostics.length ? `${diagnostics.length} issue${diagnostics.length > 1 ? 's' : ''}` : 'All systems go'}</strong>
            <small>{diagnostics[0]?.scope || 'ai_voice_bot_management'}</small>
          </div>
        </div>
      </aside>

      {/* ── Mobile topbar ── */}
      <div className="mobile-topbar">
        <div className="brand-mark" style={{ width: 30, height: 30 }}><Mic size={14} /></div>
        <span className="mobile-topbar-title">{titleFor(view)}</span>
        <button onClick={refresh} style={{ color: '#94A3B8' }}><RefreshCw size={17} /></button>
      </div>

      <main className="workspace" ref={workspaceRef}>
        {/* ── Page header ── */}
        <header className="page-header">
          <div className="page-header-top">
            <div className="page-title-group">
              <span className="page-eyebrow">JustDial · Voice AI</span>
              <h1 className="page-title">{titleFor(view)}</h1>
              <p className="page-subtitle">{subtitleFor(view)}</p>
            </div>
            <div className="page-actions">
              <select
                value={selectedBot?._id || ''}
                onChange={(event) => setSelectedBotId(event.target.value)}
                style={{ width: 200, fontSize: 13 }}
              >
                {bots.map((bot) => <option key={bot._id} value={bot._id}>{bot.name}</option>)}
              </select>

              <button className="cmdk-trigger" onClick={() => setShowCmdK(true)} title="Search everything (⌘K)">
                <Search size={14} />
                <span>Search…</span>
                <kbd className="kbd" style={{ fontSize: '10px', padding: '1px 5px', marginLeft: 4 }}>⌘K</kbd>
              </button>
              <button onClick={refresh}><RefreshCw size={14} /> Refresh</button>
              <button className="primary" onClick={openNewAgentWizard} disabled={actionState.createBot === 'running'}>
                <Rocket size={14} /> {actionState.createBot === 'failed' ? 'Retry' : 'New Agent'}
              </button>
            </div>
          </div>

          {/* ── Per-page dynamic summary ── */}
          <PageSummary
            view={view}
            bots={bots}
            transcripts={transcripts}
            campaigns={campaigns}
            selectedBot={selectedBot}
            testStatus={testStatus}
            roomName={testRoomName}
            micEnabled={micEnabled}
            remoteAudioReady={remoteAudioReady}
            loading={loading}
          />
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
            <AlertCircle size={15} /> {message}
          </div>
        )}

        {view === 'bots' && (
          <ResilientPanel name="Agents" onDiagnostic={reportDiagnostic}>
            {agentWorkspaceMode === 'builder' ? (
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
                onUnpublish={unpublishActive}
                onRename={renameBotMeta}
                saveState={actionState.saveDraft}
                publishState={actionState.publishDraft}
                unpublishState={actionState.unpublishActive}
                renameState={actionState.renameBotMeta}
                onBack={() => setAgentWorkspaceMode('list')}
                editingVersionId={editingVersionId}
                onSelectVersion={selectVersion}
                onUpdateVersion={updateVersionAction}
                updateVersionState={actionState.updateVersion}
                onRollback={rollbackToVersion}
                rollbackState={actionState}
                builderMode={builderMode}
                onToggleMode={(mode) => {
                  setBuilderMode(mode);
                  localStorage.setItem('builderMode', mode);
                }}
                onShowDiff={(a, b) => setDiffVersionIds([a, b])}
                liveCallsByVersion={liveCallsByVersion}
              />
            ) : (
              <BotsView
                bots={bots}
                selectedBot={selectedBot}
                transcripts={transcripts}
                loading={loading}
                onSelect={setSelectedBotId}
                onEdit={(botId) => {
                  setSelectedBotId(botId);
                  setAgentWorkspaceMode('builder');
                }}
                onDelete={(bot) => setDeleteCandidate(bot)}
                onNew={openNewAgentWizard}
              />
            )}
          </ResilientPanel>
        )}

        {view === 'campaigns' && (
          <ResilientPanel name="Campaigns" onDiagnostic={reportDiagnostic}>
            <CampaignsView
              campaigns={campaigns}
              bots={bots}
              languages={languages}
              outcomes={outcomes}
              loading={loading}
              workspaceMode={campaignWorkspaceMode}
              selectedCampaignKey={selectedCampaignKey}
              onSelectCampaign={(key) => { setSelectedCampaignKey(key); setCampaignWorkspaceMode('strategy'); }}
              onBackToList={() => setCampaignWorkspaceMode('list')}
              onSaveStrategy={saveCampaignStrategy}
              saveState={actionState.campaignStrategy}
              onAssignBot={assignBotToCampaign}
              assignBotState={actionState}
              onSetStatus={setCampaignStatus}
            />
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
              closeNote={testCloseNote}
              chatMessage={testChatMessage}
              setChatMessage={setTestChatMessage}
              micEnabled={micEnabled}
              roomName={testRoomName}
              remoteAudioReady={remoteAudioReady}
              remoteAudioRef={remoteAudioRef}
              onStart={startWebRtcTest}
              onStop={stopWebRtcTest}
              onToggleMic={toggleTestMic}
              onSendChat={sendTestChatMessage}
            />
          </ResilientPanel>
        )}

        {view === 'transcripts' && (
          <ResilientPanel name="Transcripts" onDiagnostic={reportDiagnostic}>
            <TranscriptsView
              transcripts={filteredTranscripts}
              selectedTranscript={selectedTranscript}
              localRecordingByRoom={localRecordingByRoom}
              callEvents={callEvents}
              bots={bots}
              campaigns={campaigns}
              loading={loading}
              searchText={searchText}
              onSearchText={setSearchText}
              filters={transcriptFilters}
              onFiltersChange={setTranscriptFilters}
              onSelect={setSelectedTranscriptId}
              onNavigateTest={() => navigate('test')}
            />
          </ResilientPanel>
        )}

        {view === 'analytics' && (
          <ResilientPanel name="Analytics" onDiagnostic={reportDiagnostic}>
            <AnalyticsView bots={bots} campaigns={campaigns} />
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
        {deleteCandidate && (
          <DeleteAgentDialog
            bot={deleteCandidate}
            campaigns={campaigns}
            transcriptCount={transcripts.filter(t => t.bot_id === deleteCandidate._id).length}
            busy={actionState.deleteAgent === 'running'}
            onCancel={() => setDeleteCandidate(null)}
            onConfirm={() => deleteAgent(deleteCandidate)}
          />
        )}
        {showNewAgentWizard && (
          <NewAgentWizard
            form={newAgentForm}
            onChange={setNewAgentForm}
            voices={voices}
            languages={languages}
            busy={actionState.createBot === 'running'}
            onCancel={() => setShowNewAgentWizard(false)}
            onConfirm={createBot}
          />
        )}
        {diffVersionIds && (() => {
          const verA = versions.find(v => v._id === diffVersionIds[0]);
          const verB = versions.find(v => v._id === diffVersionIds[1]);
          return verA && verB ? <VersionDiffModal versionA={verA} versionB={verB} onClose={() => setDiffVersionIds(null)} /> : null;
        })()}
        {showPublishConfirm && (
          <PublishConfirmModal
            latestDraft={latestDraft}
            activeVersion={activeVersion}
            activeCallCount={transcripts.filter(t => t.bot_id === selectedBot?._id && t.status && !['completed', 'failed', 'cancelled', 'dnc'].includes(t.status)).length}
            busy={actionState.publishDraft === 'running'}
            onCancel={() => setShowPublishConfirm(false)}
            onConfirm={async () => { setShowPublishConfirm(false); await doPublishDraft(); }}
          />
        )}
        {showShortcuts && <ShortcutsModal onClose={() => setShowShortcuts(false)} />}
        {showCmdK && (
          <CommandPalette
            bots={bots}
            campaigns={campaigns}
            transcripts={transcripts}
            onClose={() => setShowCmdK(false)}
            onNavigate={(view, extra) => {
              setShowCmdK(false);
              navigate(view);
              if (extra?.botId) setSelectedBotId(extra.botId);
              if (extra?.transcriptId) setSelectedTranscriptId(extra.transcriptId);
              if (extra?.campaignKey) setSelectedCampaignKey(extra.campaignKey);
            }}
          />
        )}
      </main>

      {/* ── Bottom nav (mobile only) ── */}
      <nav className="bottom-nav">
        <button className={`bottom-nav-item ${view === 'bots' ? 'active' : ''}`} onClick={() => navigate('bots')}><Bot size={20} /><span>Agents</span></button>
        <button className={`bottom-nav-item ${view === 'campaigns' ? 'active' : ''}`} onClick={() => navigate('campaigns')}><Megaphone size={20} /><span>Campaigns</span></button>
        <button className={`bottom-nav-item ${view === 'test' ? 'active' : ''}`} onClick={() => navigate('test')}><Play size={20} /><span>Test</span></button>
        <button className={`bottom-nav-item ${view === 'transcripts' ? 'active' : ''}`} onClick={() => navigate('transcripts')}><FileText size={20} /><span>Calls</span></button>
        <button className={`bottom-nav-item ${view === 'analytics' ? 'active' : ''}`} onClick={() => navigate('analytics')}><BarChart2 size={20} /><span>Analytics</span></button>
        <button className={`bottom-nav-item ${(view === 'observability' || view === 'library' || view === 'settings') ? 'active' : ''}`} onClick={() => navigate('library')}><BookOpen size={20} /><span>More</span></button>
      </nav>
    </div>
  );
}

function VersionDiffModal({ versionA, versionB, onClose }: { versionA: BotVersion; versionB: BotVersion; onClose: () => void }) {
  const allKeys = Array.from(new Set([...Object.keys(versionA.config), ...Object.keys(versionB.config)]));
  const diffs = allKeys.filter(k => JSON.stringify(versionA.config[k]) !== JSON.stringify(versionB.config[k]));
  const same = allKeys.filter(k => !diffs.includes(k));

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel" role="dialog" aria-modal="true" style={{ maxWidth: 720 }}>
        <div className="modal-header">
          <div className="modal-icon"><GitBranch size={17} /></div>
          <h2>Version diff — v{versionA.version} vs v{versionB.version}</h2>
        </div>
        <div className="modal-body" style={{ maxHeight: '70vh', overflowY: 'auto' }}>
          {diffs.length === 0 && <p className="muted">No differences found between these two versions.</p>}
          {diffs.length > 0 && (
            <>
              <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem' }}>Changed fields ({diffs.length})</div>
              {diffs.map(key => (
                <div key={key} style={{ marginBottom: '0.75rem', borderRadius: '6px', overflow: 'hidden', border: '1px solid var(--border)' }}>
                  <div style={{ background: 'var(--surface-2, #f4f4f5)', padding: '4px 10px', fontSize: '0.78rem', fontWeight: 700 }}>{key}</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0 }}>
                    <div style={{ background: '#fef2f2', padding: '8px 10px', fontSize: '0.78rem', borderRight: '1px solid var(--border)' }}>
                      <div style={{ fontSize: '0.68rem', color: '#b91c1c', marginBottom: '3px' }}>v{versionA.version}</div>
                      <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{JSON.stringify(versionA.config[key], null, 2)}</pre>
                    </div>
                    <div style={{ background: '#f0fdf4', padding: '8px 10px', fontSize: '0.78rem' }}>
                      <div style={{ fontSize: '0.68rem', color: '#15803d', marginBottom: '3px' }}>v{versionB.version}</div>
                      <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{JSON.stringify(versionB.config[key], null, 2)}</pre>
                    </div>
                  </div>
                </div>
              ))}
              {same.length > 0 && (
                <details style={{ marginTop: '0.5rem' }}>
                  <summary style={{ fontSize: '0.78rem', color: 'var(--muted)', cursor: 'pointer' }}>Unchanged fields ({same.length})</summary>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '4px' }}>
                    {same.map(k => <code key={k} style={{ fontSize: '0.72rem', background: 'var(--surface-2, #f4f4f5)', padding: '2px 6px', borderRadius: '4px' }}>{k}</code>)}
                  </div>
                </details>
              )}
            </>
          )}
        </div>
        <div className="modal-footer">
          <button onClick={onClose}>Close</button>
        </div>
      </section>
    </div>
  );
}

function PublishConfirmModal({
  latestDraft,
  activeVersion,
  activeCallCount,
  busy,
  onCancel,
  onConfirm
}: {
  latestDraft?: BotVersion;
  activeVersion?: BotVersion;
  activeCallCount: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel" role="dialog" aria-modal="true" style={{ maxWidth: 480 }}>
        <div className="modal-header">
          <div className="modal-icon"><Rocket size={17} /></div>
          <h2>Publish v{latestDraft?.version}?</h2>
        </div>
        <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {activeCallCount > 0 && (
            <div className="notice" style={{ background: 'var(--warning-bg, #fef3c7)', borderColor: 'var(--warning-border, #fcd34d)', color: 'var(--warning, #92400e)' }}>
              <AlertTriangle size={16} />
              <strong>{activeCallCount} call{activeCallCount > 1 ? 's' : ''} are currently in progress</strong> on {activeVersion ? `v${activeVersion.version}` : 'the active version'}. Those calls keep their snapshot and will not be interrupted. Only new calls will use v{latestDraft?.version}.
            </div>
          )}
          {activeCallCount === 0 && (
            <div className="callout success">
              <CheckCircle2 size={16} />
              No active calls detected. Safe to publish.
            </div>
          )}
          <p style={{ fontSize: '0.88rem', color: 'var(--text-2)' }}>
            Publishing makes v{latestDraft?.version} the active config for all <strong>new</strong> inbound and outbound calls. Existing in-flight calls are unaffected.
          </p>
        </div>
        <div className="modal-footer">
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="primary" onClick={onConfirm} disabled={busy}>
            <Rocket size={15} /> {busy ? 'Publishing…' : `Publish v${latestDraft?.version}`}
          </button>
        </div>
      </section>
    </div>
  );
}

// ─── Page Summary (dynamic per-page) ──────────────────────────────────────────
function PageSummary({
  view, bots, transcripts, campaigns, selectedBot,
  testStatus, roomName, micEnabled, remoteAudioReady, loading
}: {
  view: View;
  bots: BotType[];
  transcripts: Transcript[];
  campaigns: Campaign[];
  selectedBot?: BotType;
  testStatus: string;
  roomName: string;
  micEnabled: boolean;
  remoteAudioReady: boolean;
  loading: boolean;
}) {
  const L = loading ? '…' : null;

  if (view === 'bots' || view === 'builder') {
    const total = bots.length;
    const active = bots.filter((b) => b.status === 'active').length;
    const draft = bots.filter((b) => b.status === 'draft').length;
    const botCalls = transcripts.filter((t) => t.bot_id === selectedBot?._id).length;
    return (
      <div className="page-summary">
        <SummaryCard icon={<Bot size={17} />} color="blue" label="Total agents" value={L ?? total.toString()} sub="All configured bots" />
        <SummaryCard icon={<CheckCircle2 size={17} />} color="green" label="Active" value={L ?? active.toString()} sub="Published & running" />
        <SummaryCard icon={<Braces size={17} />} color="amber" label="Draft" value={L ?? draft.toString()} sub="Awaiting publish" />
        <SummaryCard icon={<PhoneCall size={17} />} color="slate" label="Calls (selected)" value={L ?? botCalls.toString()} sub={selectedBot?.name || 'Select a bot'} />
      </div>
    );
  }

  if (view === 'campaigns') {
    const total = campaigns.length;
    const withBot = campaigns.filter((c) => c.bot_id).length;
    const active = campaigns.filter((c) => c.status === 'active').length;
    const noBot = campaigns.filter((c) => !c.bot_id).length;
    return (
      <div className="page-summary">
        <SummaryCard icon={<Megaphone size={17} />} color="blue" label="Total campaigns" value={L ?? total.toString()} sub="All campaign keys" />
        <SummaryCard icon={<CheckCircle2 size={17} />} color="green" label="Active" value={L ?? active.toString()} sub="Status: active" />
        <SummaryCard icon={<Bot size={17} />} color="amber" label="Bot assigned" value={L ?? withBot.toString()} sub="Have a bot mapping" />
        <SummaryCard icon={<AlertCircle size={17} />} color="red" label="No bot" value={L ?? noBot.toString()} sub="Need assignment" />
      </div>
    );
  }

  if (view === 'test') {
    const connected = Boolean(roomName) && !testStatus.toLowerCase().includes('failed') && testStatus !== 'Idle';
    return (
      <div className="page-summary">
        <SummaryCard icon={<Bot size={17} />} color="blue" label="Selected bot" value={selectedBot?.name || '—'} sub="Active config" />
        <SummaryCard icon={<Wifi size={17} />} color={connected ? 'green' : 'slate'} label="Session" value={connected ? 'Live' : 'Idle'} sub={testStatus} />
        <SummaryCard icon={<Mic size={17} />} color={micEnabled ? 'green' : 'slate'} label="Microphone" value={micEnabled ? 'Live' : 'Off'} sub="Input status" />
        <SummaryCard icon={<Volume2 size={17} />} color={remoteAudioReady ? 'green' : 'slate'} label="Bot audio" value={remoteAudioReady ? 'Connected' : 'Waiting'} sub="Output status" />
      </div>
    );
  }

  if (view === 'transcripts') {
    const total = transcripts.length;
    const completed = transcripts.filter((t) => t.status === 'completed').length;
    const avgDur = average(transcripts.map((t) => Number(t.call_duration_sec || 0)).filter(Boolean));
    const today = transcripts.filter((t) => {
      if (!t.created_at) return false;
      const d = new Date(t.created_at);
      const n = new Date();
      return d.getDate() === n.getDate() && d.getMonth() === n.getMonth() && d.getFullYear() === n.getFullYear();
    }).length;
    return (
      <div className="page-summary">
        <SummaryCard icon={<FileText size={17} />} color="blue" label="Total calls" value={L ?? total.toString()} sub="All transcripts" />
        <SummaryCard icon={<CheckCircle2 size={17} />} color="green" label="Completed" value={L ?? completed.toString()} sub="Status: completed" />
        <SummaryCard icon={<Clock3 size={17} />} color="amber" label="Avg duration" value={avgDur ? `${avgDur}s` : '—'} sub="From saved records" />
        <SummaryCard icon={<Activity size={17} />} color="slate" label="Today" value={L ?? today.toString()} sub="Calls today" />
      </div>
    );
  }

  if (view === 'observability') {
    const total = transcripts.length;
    const nonCompleted = transcripts.filter((t) => t.status && t.status !== 'completed').length;
    const completed = transcripts.filter((t) => t.status === 'completed').length;
    return (
      <div className="page-summary">
        <SummaryCard icon={<Activity size={17} />} color="blue" label="Total traces" value={L ?? total.toString()} sub="Transcripts stored" />
        <SummaryCard icon={<CheckCircle2 size={17} />} color="green" label="Completed" value={L ?? completed.toString()} sub="Clean calls" />
        <SummaryCard icon={<AlertTriangle size={17} />} color="amber" label="Non-completed" value={L ?? nonCompleted.toString()} sub="Errors / incomplete" />
        <SummaryCard icon={<Bot size={17} />} color="slate" label="Agent" value={selectedBot?.name || '—'} sub="Selected bot" />
      </div>
    );
  }

  if (view === 'library') {
    return (
      <div className="page-summary">
        <SummaryCard icon={<BookOpen size={17} />} color="blue" label="Library" value="Phrase DB" sub="Voicemail · Hold · DNC" />
        <SummaryCard icon={<MessageSquareText size={17} />} color="green" label="Outcomes" value="AI labels" sub="Call classification" />
        <SummaryCard icon={<Wand2 size={17} />} color="amber" label="Languages" value="Style notes" sub="Per-language config" />
        <SummaryCard icon={<ShieldCheck size={17} />} color="slate" label="Live in" value="~60s" sub="Edits go live fast" />
      </div>
    );
  }

  if (view === 'settings') {
    return (
      <div className="page-summary">
        <SummaryCard icon={<ClipboardList size={17} />} color="blue" label="Runtime" value="LiveKit" sub="Connection settings" />
        <SummaryCard icon={<Wifi size={17} />} color="green" label="Agent name" value="Worker ID" sub="LiveKit dispatch key" />
        <SummaryCard icon={<ShieldCheck size={17} />} color="amber" label="Credentials" value="Backend .env" sub="Keys not shown here" />
        <SummaryCard icon={<Database size={17} />} color="slate" label="Mongo" value="Connected" sub="Auto-managed" />
      </div>
    );
  }

  // Fallback — global overview
  const activeBots = bots.filter((b) => b.status === 'active').length;
  const completedCalls = transcripts.filter((t) => t.status === 'completed').length;
  const avgDuration = average(transcripts.map((t) => Number(t.call_duration_sec || 0)).filter(Boolean));
  return (
    <div className="page-summary">
      <SummaryCard icon={<Bot size={17} />} color="blue" label="Active agents" value={L ?? activeBots.toString()} sub={`${bots.length} total`} />
      <SummaryCard icon={<Megaphone size={17} />} color="green" label="Campaigns" value={L ?? campaigns.length.toString()} sub="Mongo mappings" />
      <SummaryCard icon={<PhoneCall size={17} />} color="amber" label="Completed calls" value={L ?? completedCalls.toString()} sub={`${transcripts.length} transcripts`} />
      <SummaryCard icon={<Clock3 size={17} />} color="slate" label="Avg duration" value={avgDuration ? `${avgDuration}s` : '—'} sub="Saved transcripts" />
    </div>
  );
}

function SummaryCard({ icon, color, label, value, sub }: {
  icon: React.ReactNode;
  color: 'blue' | 'green' | 'amber' | 'red' | 'slate';
  label: string;
  value: string;
  sub: string;
}) {
  return (
    <div className="summary-card">
      <div className={`summary-icon ${color}`}>{icon}</div>
      <div className="summary-body">
        <div className="summary-label">{label}</div>
        <div className="summary-value">{value}</div>
        <div className="summary-sub">{sub}</div>
      </div>
    </div>
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

function BotsView({ bots, selectedBot, transcripts, loading, onSelect, onEdit, onDelete, onNew }: {
  bots: BotType[];
  selectedBot?: BotType;
  transcripts: Transcript[];
  loading?: boolean;
  onSelect: (botId: string) => void;
  onEdit: (botId: string) => void;
  onDelete: (bot: BotType) => void;
  onNew?: () => void;
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
        <div className="table-scroll"><table>
          <thead>
            <tr><th>Name</th><th>Status</th><th>Assistant</th><th>Updated</th><th>Actions</th></tr>
          </thead>
          <tbody>
            {loading && !bots.length ? (
              <SkeletonTableBody cols={5} rows={4} />
            ) : bots.length === 0 ? (
              <tr><td colSpan={5}>
                <EmptyState
                  icon={<Bot size={32} />}
                  heading="No agents yet"
                  description="Create your first voice agent to get started. Each agent has its own prompt, voice, and published versions."
                  action={onNew ? { label: 'Create first agent', onClick: onNew } : undefined}
                />
              </td></tr>
            ) : bots.map((bot) => (
              <tr key={bot._id} onClick={() => onSelect(bot._id)} className={bot._id === selectedBot?._id ? 'selected-row' : ''}>
                <td>
                  <strong>{bot.name}</strong>
                  <small>{bot.description || 'Prompt + settings agent'}</small>
                </td>
                <td><StatusPill value={bot.status} /></td>
                <td><CopyableId value={bot.assistant_id} /></td>
                <td><TimeAgo value={bot.updated_at} /></td>
                <td>
                  <div className="table-actions">
                    <button onClick={(event) => { event.stopPropagation(); onEdit(bot._id); }}><Pencil size={13} /> Edit</button>
                    <button className="danger-button" onClick={(event) => { event.stopPropagation(); onDelete(bot); }}><Trash2 size={14} /> Delete</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
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
        {selectedBot && (
          <div className="button-row">
            <button className="primary" onClick={() => onEdit(selectedBot._id)}><Pencil size={15} /> Edit agent</button>
            <button className="danger-button" onClick={() => onDelete(selectedBot)}><Trash2 size={16} /> Delete agent</button>
          </div>
        )}
      </div>
    </section>
  );
}

function NewAgentWizard({
  form,
  onChange,
  voices,
  languages,
  busy,
  onCancel,
  onConfirm
}: {
  form: {
    name: string;
    description: string;
    agent_name: string;
    organization_name: string;
    language: string;
    voice: string;
    initial_message: string;
  };
  onChange: (value: typeof form) => void;
  voices: VoiceOption[];
  languages: LanguageOption[];
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const canCreate = form.name.trim().length > 0 && form.agent_name.trim().length > 0;
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    onChange({ ...form, [key]: e.target.value });

  const previewOpening = form.initial_message
    .replace('{agent_name}', form.agent_name || '<agent_name>')
    .replace('{organization_name}', form.organization_name || '<org_name>')
    .replace('{product}', 'air conditioner');

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="new-agent-title" style={{ maxWidth: 560 }}>
        <div className="modal-header">
          <div className="modal-icon"><Rocket size={17} /></div>
          <h2 id="new-agent-title">New voice agent</h2>
        </div>
        <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div className="form-grid">
            <label>
              Display name <span style={{ color: 'var(--danger)' }}>*</span>
              <input value={form.name} onChange={set('name')} placeholder="e.g. JD Outbound — Hindi" autoFocus />
            </label>
            <label>
              Description
              <input value={form.description} onChange={set('description')} placeholder="Short note (optional)" />
            </label>
          </div>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Persona</div>
            <div className="form-grid">
              <label>
                Agent persona name <span style={{ color: 'var(--danger)' }}>*</span>
                <input value={form.agent_name} onChange={set('agent_name')} placeholder="e.g. Tarun, Priya, Aman" />
                <small>Used in the opening line and system prompt.</small>
              </label>
              <label>
                Organization name
                <input value={form.organization_name} onChange={set('organization_name')} placeholder="e.g. JustDial" />
              </label>
            </div>
          </div>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Voice & Language</div>
            <div className="form-grid">
              <label>
                Language
                <select value={form.language} onChange={set('language')}>
                  {languages.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                </select>
              </label>
              <label>
                Voice
                <select value={form.voice} onChange={set('voice')}>
                  {voices.map((v) => <option key={v.id} value={v.id}>{v.label} {v.gender ? `(${v.gender})` : ''}</option>)}
                </select>
              </label>
            </div>
          </div>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Opening line</div>
            <label>
              <textarea
                value={form.initial_message}
                onChange={set('initial_message')}
                rows={2}
                style={{ fontFamily: 'inherit', fontSize: '0.85rem' }}
              />
              <small>Use <code style={{ fontSize: '0.75rem' }}>{'{product}'}</code>, <code style={{ fontSize: '0.75rem' }}>{'{agent_name}'}</code>, <code style={{ fontSize: '0.75rem' }}>{'{organization_name}'}</code> as placeholders.</small>
            </label>
            {previewOpening && (
              <div style={{ background: 'var(--surface-2, #f4f4f5)', borderRadius: '6px', padding: '0.5rem 0.75rem', fontSize: '0.82rem', fontStyle: 'italic', marginTop: '0.4rem', color: 'var(--text-2)' }}>
                Preview: "{previewOpening}"
              </div>
            )}
          </div>
        </div>
        <div className="modal-footer">
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="primary" onClick={onConfirm} disabled={!canCreate || busy}>
            <Rocket size={15} /> {busy ? 'Creating…' : 'Create agent'}
          </button>
        </div>
      </section>
    </div>
  );
}

function DeleteAgentDialog({
  bot,
  campaigns,
  transcriptCount,
  busy,
  onCancel,
  onConfirm
}: {
  bot: BotType;
  campaigns: Campaign[];
  transcriptCount: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [confirmText, setConfirmText] = useState('');
  const canDelete = confirmText.trim() === bot.name;
  const assignedCampaigns = campaigns.filter(c => c.bot_id === bot._id);

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel critical" role="dialog" aria-modal="true" aria-labelledby="delete-agent-title">

        <div className="modal-header">
          <div className="modal-icon"><AlertTriangle size={17} /></div>
          <h2 id="delete-agent-title">Delete agent?</h2>
        </div>

        <div className="modal-body">
          <p>
            This will permanently remove <strong style={{ color: 'var(--text)' }}>{bot.name}</strong> from the
            dashboard and disable its runtime config lookup. Existing transcripts and call records are kept for audit.
          </p>

          {/* Blast radius */}
          {(assignedCampaigns.length > 0 || transcriptCount > 0) && (
            <div className="blast-radius">
              <strong>What breaks if you delete this:</strong>
              <ul>
                {assignedCampaigns.length > 0 && (
                  <li>
                    <AlertTriangle size={13} />
                    <span>
                      <strong>{assignedCampaigns.length} campaign{assignedCampaigns.length > 1 ? 's' : ''}</strong> will lose their bot assignment:{' '}
                      {assignedCampaigns.map(c => c.name).join(', ')}
                    </span>
                  </li>
                )}
                {transcriptCount > 0 && (
                  <li>
                    <FileText size={13} />
                    <span><strong>{transcriptCount} transcript{transcriptCount > 1 ? 's' : ''}</strong> will be orphaned (kept, but no longer linked to a bot config)</span>
                  </li>
                )}
              </ul>
            </div>
          )}

          <label>
            Type <strong style={{ fontFamily: 'monospace', fontWeight: 700, color: 'var(--text-2)' }}>{bot.name}</strong> to confirm
            <input
              value={confirmText}
              onChange={(event) => setConfirmText(event.target.value)}
              placeholder={bot.name}
              autoFocus
            />
          </label>
        </div>

        <div className="modal-footer">
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="danger-button" onClick={onConfirm} disabled={!canDelete || busy}>
            <Trash2 size={15} /> {busy ? 'Deleting…' : 'Delete agent'}
          </button>
        </div>

      </section>
    </div>
  );
}

function CloseMarkersEditor({ markers, onChange }: { markers: string[]; onChange: (value: string[]) => void }) {
  const [input, setInput] = useState('');
  function add() {
    const trimmed = input.trim();
    if (trimmed && !markers.includes(trimmed)) onChange([...markers, trimmed]);
    setInput('');
  }
  return (
    <label className="full">
      Call-end close phrases
      <small>Bot ends the call when it detects any of these phrases. Override the hardcoded Hindi defaults for non-Hindi bots.</small>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.4rem', marginBottom: '0.4rem', minHeight: '2rem' }}>
        {markers.map(m => (
          <span key={m} style={{ background: 'var(--surface-2, #f4f4f5)', borderRadius: '4px', padding: '2px 8px', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '4px' }}>
            {m}
            <button style={{ padding: 0, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)', lineHeight: 1 }} onClick={() => onChange(markers.filter(x => x !== m))}>×</button>
          </span>
        ))}
        {markers.length === 0 && <span style={{ fontSize: '0.78rem', color: 'var(--muted)', fontStyle: 'italic' }}>Using hardcoded defaults (Hindi)</span>}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="e.g. thank you, goodbye, dhanyavaad"
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          style={{ flex: 1 }}
        />
        <button onClick={add} style={{ whiteSpace: 'nowrap' }}><Plus size={14} /> Add</button>
      </div>
    </label>
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
  onUnpublish,
  onRename,
  onBack,
  saveState,
  publishState,
  unpublishState,
  renameState,
  editingVersionId,
  onSelectVersion,
  onUpdateVersion,
  updateVersionState,
  onRollback,
  rollbackState,
  onShowDiff,
  liveCallsByVersion,
  builderMode,
  onToggleMode
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
  onUnpublish?: () => void;
  onRename?: (name: string, description: string) => void;
  onBack?: () => void;
  saveState?: 'idle' | 'running' | 'failed';
  publishState?: 'idle' | 'running' | 'failed';
  unpublishState?: 'idle' | 'running' | 'failed';
  renameState?: 'idle' | 'running' | 'failed';
  editingVersionId?: string;
  onSelectVersion?: (version: BotVersion) => void;
  onUpdateVersion?: (versionId: string) => void;
  updateVersionState?: 'idle' | 'running' | 'failed';
  onRollback?: (versionId: string) => void;
  rollbackState?: Record<string, 'idle' | 'running' | 'failed'>;
  onShowDiff?: (versionIdA: string, versionIdB: string) => void;
  liveCallsByVersion?: Record<string, number>;
  builderMode?: BuilderMode;
  onToggleMode?: (mode: BuilderMode) => void;
}) {
  const value = config.ok ? config.value : defaultConfig;
  const isAdvanced = builderMode === 'advanced';

  const [draftName, setDraftName] = useState(selectedBot?.name || '');
  const [draftDescription, setDraftDescription] = useState(selectedBot?.description || '');

  useEffect(() => {
    setDraftName(selectedBot?.name || '');
    setDraftDescription(selectedBot?.description || '');
  }, [selectedBot?._id]);

  const nameChanged = draftName.trim() !== (selectedBot?.name || '').trim()
    || draftDescription.trim() !== (selectedBot?.description || '').trim();

  // ── Dirty tracking: detect unsaved config changes ──────────────────
  const [baseConfigText, setBaseConfigText] = useState(configText);
  const prevSaveState = useRef(saveState);
  const prevUpdateState = useRef(updateVersionState);
  // Capture config at the moment a save starts so we can reset base to
  // exactly what was saved, not what the user may have typed since.
  const configAtSaveStartRef = useRef(configText);

  // Reset base when a new version is selected (editingVersionId changes)
  useEffect(() => { setBaseConfigText(configText); }, [editingVersionId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Track config at save start, reset base on successful completion
  useEffect(() => {
    if (saveState === 'running' && prevSaveState.current !== 'running') {
      configAtSaveStartRef.current = configText;
    }
    if (prevSaveState.current === 'running' && saveState === 'idle') {
      setBaseConfigText(configAtSaveStartRef.current);
    }
    prevSaveState.current = saveState;
  }, [saveState]); // intentionally omit configText — we capture it via ref at save-start

  useEffect(() => {
    if (updateVersionState === 'running' && prevUpdateState.current !== 'running') {
      configAtSaveStartRef.current = configText;
    }
    if (prevUpdateState.current === 'running' && updateVersionState === 'idle') {
      setBaseConfigText(configAtSaveStartRef.current);
    }
    prevUpdateState.current = updateVersionState;
  }, [updateVersionState]); // intentionally omit configText — captured via ref

  const isDirtyConfig = configText !== baseConfigText;
  const isDirty = isDirtyConfig || nameChanged;

  // Auto-save to localStorage (debounced 2s)
  useEffect(() => {
    if (!selectedBot?._id || !isDirtyConfig) return;
    const id = window.setTimeout(() => {
      localStorage.setItem(`draft-autosave-${selectedBot._id}`, configText);
    }, 2000);
    return () => clearTimeout(id);
  }, [configText, selectedBot?._id, isDirtyConfig]);

  // Warn before tab close when there are unsaved changes
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isDirty]);

  // Cmd/Ctrl+S to save draft
  useEffect(() => {
    function handleSave(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        if (config.ok && saveState !== 'running') onSaveDraft();
      }
    }
    window.addEventListener('keydown', handleSave);
    return () => window.removeEventListener('keydown', handleSave);
  }, [config.ok, saveState, onSaveDraft]);

  const editingVer = versions.find(v => v._id === editingVersionId);
  const isPublishedVer = editingVer?.state === 'published';

  return (
    <section className="builder-layout">
      {/* ── Back nav + action bar ─────────────────────────────── */}
      <div className="builder-action-bar">
        {onBack && (
          <button className="back-link" onClick={onBack}>
            <ChevronRight className="rotate-180" size={15} /> Back to agents
          </button>
        )}
        {onToggleMode && (
          <div className="mode-toggle" role="group" aria-label="Builder mode">
            <button
              className={!isAdvanced ? 'mode-btn active' : 'mode-btn'}
              onClick={() => onToggleMode('pm')}
              title="PM view — core fields only"
            >
              <Pencil size={13} /> PM
            </button>
            <button
              className={isAdvanced ? 'mode-btn active' : 'mode-btn'}
              onClick={() => onToggleMode('advanced')}
              title="Advanced — all config fields including admin settings"
            >
              <SlidersHorizontal size={13} /> Advanced
            </button>
          </div>
        )}
        <div className="builder-action-bar-right">
          {isDirty && (
            <span className="unsaved-indicator" title="You have unsaved changes. Press ⌘S to save a new draft.">
              <span className="unsaved-dot" />
              Unsaved changes
            </span>
          )}
          {!isPublishedVer && editingVersionId && onUpdateVersion && (
            <button
              disabled={!config.ok || updateVersionState === 'running'}
              onClick={() => onUpdateVersion(editingVersionId)}
            >
              <Save size={15} />
              {updateVersionState === 'running' ? 'Saving…' : updateVersionState === 'failed' ? 'Retry' : `Update v${editingVer?.version ?? ''}`}
            </button>
          )}
          {activeVersion && !latestDraft && onUnpublish && (
            <button
              className="fallback-button"
              disabled={unpublishState === 'running'}
              onClick={onUnpublish}
              title="Move the published version back to draft so you can edit it"
            >
              <Pencil size={15} /> {unpublishState === 'running' ? 'Unpublishing…' : 'Edit published'}
            </button>
          )}
          <button disabled={!config.ok || saveState === 'running'} onClick={onSaveDraft}>
            <Plus size={15} /> {saveState === 'failed' ? 'Retry' : saveState === 'running' ? 'Saving…' : 'New version'}
          </button>
          <button
            className={publishState === 'failed' ? 'fallback-button' : 'primary'}
            disabled={publishState === 'running' || (!latestDraft && versions.length > 0)}
            onClick={onPublish}
          >
            <Rocket size={15} /> {publishState === 'failed' ? 'Retry' : publishState === 'running' ? 'Publishing…' : 'Publish'}
          </button>
        </div>
      </div>

      <div className="builder-main">
        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>{draftName || selectedBot?.name || 'Bot Builder'}</h2>
              <p>PMs edit the spoken behavior here. Developers can use the JSON panel for advanced runtime settings.</p>
            </div>
          </div>
          {!config.ok && <div className="notice error"><AlertTriangle size={16} /> JSON is invalid: {config.error}</div>}
          {isPublishedVer && (
            <div className="notice" style={{ background: 'var(--warning-bg)', borderColor: 'var(--warning-border)', color: 'var(--warning)' }}>
              <ShieldCheck size={15} />
              Viewing published v{editingVer?.version} — changes here create a new draft. The live version is unaffected.
            </div>
          )}
          <div className="form-section">
            <div className="form-grid agent-identity">
              <label>
                Agent name
                <input
                  value={draftName}
                  placeholder={selectedBot?.name || 'Agent name'}
                  onChange={(e) => setDraftName(e.target.value)}
                />
              </label>
              <label>
                Description
                <input
                  value={draftDescription}
                  placeholder="Short description (optional)"
                  onChange={(e) => setDraftDescription(e.target.value)}
                />
              </label>
              {onRename && (
                <div className="rename-action">
                  <button
                    className={renameState === 'failed' ? 'fallback-button' : nameChanged ? 'primary' : ''}
                    disabled={!nameChanged || renameState === 'running'}
                    onClick={() => onRename(draftName, draftDescription)}
                  >
                    <Pencil size={14} />
                    {renameState === 'running' ? 'Saving…' : renameState === 'failed' ? 'Retry rename' : 'Save name'}
                  </button>
                </div>
              )}
            </div>

            {/* ── Persona & Identity — always visible ── */}
            <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem', marginBottom: '0.25rem' }}>
              <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Persona &amp; Identity</div>
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
                {isAdvanced && (
                  <>
                    <label>
                      AI partner key
                      <input value={String(value.ai_partner || '')} placeholder="e.g. inh-suny-bot" onChange={(e) => onUpdateConfig('ai_partner', e.target.value)} />
                      <small>Dialer lead fetch tag. Leave blank to use platform default.</small>
                    </label>
                    <label>
                      Inactivity end phrase
                      <input value={String(value.inactivity_end_text || '')} placeholder="Platform default used if blank" onChange={(e) => onUpdateConfig('inactivity_end_text', e.target.value)} />
                      <small>Spoken after extended silence. Language-specific.</small>
                    </label>
                  </>
                )}
              </div>
            </div>

            {/* ── Core content — always visible ── */}
            <label className="full">
              System prompt
              <textarea className="prompt-editor" value={String(value.system_prompt || '')} onChange={(event) => onUpdateConfig('system_prompt', event.target.value)} />
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
                Max call duration: {Number(value.max_call_duration || 300)}s ({Math.round(Number(value.max_call_duration || 300) / 60)} min)
                <input type="range" min={60} max={600} step={30} value={Number(value.max_call_duration || 300)} onChange={(event) => onUpdateConfig('max_call_duration', Number(event.target.value))} />
                <small>Also update the "X minutes" mention in your system prompt.</small>
              </label>
            </div>

            {/* ── Advanced-only fields ── */}
            {isAdvanced && (
              <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem', marginTop: '0.5rem' }}>
                <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <SlidersHorizontal size={12} /> Advanced / Admin settings
                </div>
                <div className="form-grid">
                  <label>
                    Model
                    <input value={String(value.model || '')} onChange={(event) => onUpdateConfig('model', event.target.value)} />
                  </label>
                  <label>
                    Temperature
                    <input type="number" min="0" max="2" step="0.1" value={Number(value.temperature || 0)} onChange={(event) => onUpdateConfig('temperature', Number(event.target.value))} />
                  </label>
                  <label>
                    Silence duration ms
                    <input type="number" value={Number(value.gemini_silence_duration_ms || 1800)} onChange={(event) => onUpdateConfig('gemini_silence_duration_ms', Number(event.target.value))} />
                    <small>VAD: how long silence triggers end-of-speech.</small>
                  </label>
                  <label>
                    Prefix padding ms
                    <input type="number" value={Number(value.gemini_prefix_padding_ms || 300)} onChange={(event) => onUpdateConfig('gemini_prefix_padding_ms', Number(event.target.value))} />
                  </label>
                  <label>
                    Post-speech hold ms
                    <input type="number" value={Number(value.post_speech_hold_ms || 800)} onChange={(event) => onUpdateConfig('post_speech_hold_ms', Number(event.target.value))} />
                  </label>
                  <label>
                    Dialer service ID
                    <input
                      type="number"
                      value={Number((value.recording as RuntimeConfig['recording'] | undefined)?.service_id || 293)}
                      onChange={(event) => onUpdateConfig('recording', {
                        ...(typeof value.recording === 'object' && value.recording ? value.recording : {}),
                        service_id: Number(event.target.value)
                      })}
                    />
                  </label>
                  <label>
                    Dialer city
                    <input
                      value={String((value.recording as RuntimeConfig['recording'] | undefined)?.dialer_city || 'bangalore')}
                      onChange={(event) => onUpdateConfig('recording', {
                        ...(typeof value.recording === 'object' && value.recording ? value.recording : {}),
                        dialer_city: event.target.value
                      })}
                    />
                  </label>
                  <label>
                    MIS API base URL
                    <input
                      value={String((value.api_urls as Record<string,string> | undefined)?.mis_api_base || '')}
                      placeholder="Leave blank to use platform default"
                      onChange={(event) => onUpdateConfig('api_urls', {
                        ...(typeof value.api_urls === 'object' && value.api_urls ? value.api_urls : {}),
                        mis_api_base: event.target.value
                      })}
                    />
                    <small>Per-bot override for MIS lead fetch endpoint.</small>
                  </label>
                </div>
                <CloseMarkersEditor
                  markers={Array.isArray(value.close_markers) ? value.close_markers as string[] : []}
                  onChange={v => onUpdateConfig('close_markers', v)}
                />
                <label style={{ marginTop: '0.5rem', display: 'block' }}>
                  Function calling
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.3rem' }}>
                    <input type="checkbox" checked={Boolean(value.function_calling)} onChange={(e) => onUpdateConfig('function_calling', e.target.checked)} style={{ width: 'auto' }} />
                    <span style={{ fontSize: '0.85rem' }}>Enable function calling (requires functions list in JSON)</span>
                  </div>
                </label>
              </div>
            )}
          </div>
        </div>
        {isAdvanced && (
          <div className="panel json-panel">
            <div className="panel-header">
              <div>
                <h2>Developer JSON</h2>
                <p>Full runtime config. Only visible in Advanced mode.</p>
              </div>
              <Database size={18} />
            </div>
            <textarea className="json-editor" value={configText} onChange={(event) => onConfigTextChange(event.target.value)} spellCheck={false} />
          </div>
        )}
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
            {versions.map((version, i) => {
              const isActive = version._id === selectedBot?.active_version_id;
              const rollbackKey = `rollback-${version._id}`;
              const rolling = rollbackState?.[rollbackKey] === 'running';
              const liveCalls = liveCallsByVersion?.[version._id] || 0;
              return (
                <div
                  className={`version-row${version._id === editingVersionId ? ' active' : ''}`}
                  key={version._id}
                  onClick={() => onSelectVersion?.(version)}
                  style={{ cursor: 'pointer' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
                    <span>v{version.version}</span>
                    <StatusPill value={version.state} />
                    {isActive && <span style={{ fontSize: '0.68rem', background: 'var(--primary-bg, #dbeafe)', color: 'var(--primary, #2563eb)', borderRadius: '4px', padding: '1px 5px', fontWeight: 600 }}>LIVE</span>}
                    {liveCalls > 0 && (
                      <span style={{ fontSize: '0.68rem', background: '#dcfce7', color: '#15803d', borderRadius: '4px', padding: '1px 5px', fontWeight: 600 }}>
                        {liveCalls} live
                      </span>
                    )}
                  </div>
                  <small>{version.published_at ? <>Published <TimeAgo value={version.published_at} /></> : <>Created <TimeAgo value={version.created_at} /></>}</small>
                  {version.notes && <small style={{ color: 'var(--muted)', fontStyle: 'italic' }}>{version.notes}</small>}
                  <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap', marginTop: '0.2rem' }}>
                    {version.state === 'published' && !isActive && onRollback && (
                      <button
                        style={{ fontSize: '0.72rem', padding: '2px 8px' }}
                        disabled={rolling}
                        onClick={(e) => { e.stopPropagation(); onRollback(version._id); }}
                      >
                        {rolling ? 'Rolling back…' : '↩ Rollback to this'}
                      </button>
                    )}
                    {i > 0 && onShowDiff && (
                      <button
                        style={{ fontSize: '0.72rem', padding: '2px 8px' }}
                        onClick={(e) => { e.stopPropagation(); onShowDiff(versions[i - 1]._id, version._id); }}
                      >
                        <GitBranch size={11} /> vs prev
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
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

function CampaignsView({
  campaigns,
  bots,
  languages,
  outcomes,
  loading,
  workspaceMode,
  selectedCampaignKey,
  onSelectCampaign,
  onBackToList,
  onSaveStrategy,
  saveState,
  onAssignBot,
  assignBotState,
  onSetStatus,
}: {
  campaigns: Campaign[];
  bots: BotType[];
  languages: LanguageOption[];
  outcomes: OutcomeEntry[];
  loading?: boolean;
  workspaceMode: 'list' | 'strategy';
  selectedCampaignKey: string;
  onSelectCampaign: (key: string) => void;
  onBackToList: () => void;
  onSaveStrategy: (key: string, name: string, strategy: DialingStrategy) => Promise<void>;
  saveState?: 'idle' | 'running' | 'failed';
  onAssignBot?: (campaignKey: string, botId: string) => void;
  assignBotState?: Record<string, 'idle' | 'running' | 'failed'>;
  onSetStatus?: (campaignKey: string, status: string) => void;
}) {
  const selectedCampaign = campaigns.find((c) => c.campaign_key === selectedCampaignKey) || campaigns[0];

  if (workspaceMode === 'strategy' && selectedCampaign) {
    return (
      <DialingStrategyBuilder
        campaign={selectedCampaign}
        bots={bots}
        languages={languages}
        outcomes={outcomes}
        onBack={onBackToList}
        onSave={(strategy) => onSaveStrategy(selectedCampaign.campaign_key, selectedCampaign.name, strategy)}
        saveState={saveState}
      />
    );
  }

  return (
    <section className="content-grid two-col">
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Campaign mappings</h2>
            <p>One bot belongs to one campaign, with lead API and callback mapping owned in Mongo.</p>
          </div>
        </div>
        <div className="table-scroll"><table>
          <thead>
            <tr><th>Campaign</th><th>Bot</th><th>Status</th><th>Strategy</th><th>Lead API</th><th>Actions</th></tr>
          </thead>
          <tbody>
            {loading && !campaigns.length ? (
              <SkeletonTableBody cols={6} rows={3} />
            ) : !campaigns.length ? (
              <tr><td colSpan={6}>
                <EmptyState
                  icon={<Megaphone size={32} />}
                  heading="No campaigns yet"
                  description="Campaigns are created via the backend API. Each campaign maps a bot to a lead source and callback endpoint."
                />
              </td></tr>
            ) : campaigns.map((campaign) => (
              <tr key={campaign._id}>
                <td><strong>{campaign.name}</strong><small>{campaign.campaign_key}</small></td>
                <td>
                  {onAssignBot ? (
                    <select
                      value={campaign.bot_id || ''}
                      onChange={e => onAssignBot(campaign.campaign_key, e.target.value)}
                      style={{ fontSize: '0.82rem', width: '100%' }}
                    >
                      <option value="">— unassigned —</option>
                      {bots.map(b => <option key={b._id} value={b._id}>{b.name}</option>)}
                    </select>
                  ) : (
                    bots.find((bot) => bot._id === campaign.bot_id)?.name || '-'
                  )}
                </td>
                <td><StatusPill value={campaign.status || 'draft'} /></td>
                <td>
                  {campaign.dialing_strategy
                    ? <span className={`pill ${campaign.dialing_strategy.enabled ? 'active' : 'draft'}`}>{campaign.dialing_strategy.enabled ? 'Active' : 'Paused'}</span>
                    : <span className="pill draft">Default</span>
                  }
                </td>
                <td><code>{String(campaign.lead_api?.url || campaign.lead_api?.endpoint || '-')}</code></td>
                <td style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {onSetStatus && campaign.status !== 'active' && (
                    <button
                      className="btn-success"
                      title="Activate campaign"
                      onClick={() => onSetStatus(campaign.campaign_key, 'active')}
                    >
                      <Play size={13} /> Activate
                    </button>
                  )}
                  {onSetStatus && campaign.status === 'active' && (
                    <button
                      className="btn-warn"
                      title="Pause campaign"
                      onClick={() => onSetStatus(campaign.campaign_key, 'paused')}
                    >
                      <Pause size={13} /> Pause
                    </button>
                  )}
                  <button onClick={() => onSelectCampaign(campaign.campaign_key)}>
                    <Layers size={14} /> Strategy
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>
      <div className="panel">
        <h2>Dispatch contract</h2>
        <div className="timeline">
          <Step title="Lead event/API" text="Campaign fetches lead id, mobile, product, buyer name, city, and call id." />
          <Step title="Room metadata" text="Dialer creates LiveKit room with assistant_id, campaign_id, lead_id and call_id." />
          <Step title="Agent joins" text="Runtime fetches active published config and stores immutable call snapshot." />
          <Step title="Strategy applied" text="Dialing strategy rules decide whether to retry, stop, or mark DNC based on call outcome." />
          <Step title="Callback" text="Outcome and raw transcript are saved, then callback mapping runs per campaign." />
        </div>
        <div className="callout">
          <Layers size={18} />
          Click "Edit Strategy" on any campaign to configure retry rules, time windows, and attempt sequencing.
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
  closeNote,
  chatMessage,
  setChatMessage,
  micEnabled,
  roomName,
  remoteAudioReady,
  remoteAudioRef,
  onStart,
  onStop,
  onToggleMic,
  onSendChat
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
  remoteAudioRef: React.RefObject<HTMLDivElement>;
  onStart: () => void;
  onStop: () => void;
  onToggleMic: () => void;
  onSendChat: (messageText: string) => void;
}) {
  const [botVersions, setBotVersions] = useState<BotVersion[]>([]);

  useEffect(() => {
    if (!selectedBot) { setBotVersions([]); return; }
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
      .catch(() => setBotVersions([]));
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
              {botVersions.length === 0 && <option value="">Loading…</option>}
              {botVersions.map((v) => (
                <option key={v._id} value={v._id}>
                  v{v.version} — {v.state === 'published' ? '✓ Published' : '✏ Draft'}
                </option>
              ))}
            </select>
            <small>
              {selectedVersion
                ? selectedVersion.state === 'published'
                  ? `Active published version`
                  : `Draft — not yet live in production`
                : 'No versions found'}
            </small>
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
          <button onClick={() => updateField('test_worker_agent_name', 'voice-bot-justdial-test')}>Use safe test worker</button>
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
          <AgentAudioVisualizerWave state={agentState} />
          <div className="session-meta-grid">
            <Metric label="Room" value={roomName ? shortId(roomName) : 'not created'} />
            <Metric label="Worker" value={effectiveWorker} />
            <Metric label="Mic" value={micEnabled ? 'live' : 'muted'} />
            <Metric label="Bot audio" value={remoteAudioReady ? 'connected' : 'waiting'} />
          </div>
          <AgentControlBar
            connected={connected}
            micEnabled={micEnabled}
            chatMessage={chatMessage}
            onChatMessageChange={setChatMessage}
            onToggleMic={onToggleMic}
            onSendChat={onSendChat}
            onDisconnect={onStop}
          />
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
        <div ref={remoteAudioRef} />
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

function PromptPreview({ version, srchterm }: { version: BotVersion; srchterm: string }) {
  const cfg = version.config as Record<string, string | undefined>;
  const rawOpening = cfg.initial_message || '(no opening line set)';
  const opening = rawOpening
    .replace('{product}', srchterm || '<product>')
    .replace('{agent_name}', cfg.agent_name || '<agent_name>')
    .replace('{organization_name}', cfg.organization_name || '<org_name>');
  const systemPrompt = cfg.system_prompt || '(no system_prompt set in this version)';
  const voice = cfg.voice || 'Aoede (default)';
  const model = cfg.model || 'gemini-3.1-flash-live-preview (default)';
  const agentName = cfg.agent_name;
  const orgName = cfg.organization_name;
  const aiPartner = cfg.ai_partner;

  const warnings: string[] = [];
  if (!agentName) warnings.push('agent_name not set — bot may use hardcoded persona');
  if (!orgName) warnings.push('organization_name not set');
  if (!cfg.initial_message) warnings.push('initial_message not set — bot will use fallback');

  const configSource = version.state === 'published'
    ? `embedded_test_config:${version._id.slice(-6)}`
    : `draft:v${version.version}`;

  return (
    <details className="prompt-preview" style={{ marginTop: '1rem' }}>
      <summary style={{ cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem', padding: '0.5rem 0', userSelect: 'none' }}>
        Prompt preview — v{version.version} ({version.state})
      </summary>
      <div style={{ marginTop: '0.5rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          <code style={{ fontSize: '0.72rem', background: 'var(--surface-2, #f4f4f5)', padding: '2px 8px', borderRadius: '4px', color: 'var(--muted)' }}>
            config: {configSource}
          </code>
          {aiPartner && <code style={{ fontSize: '0.72rem', background: 'var(--surface-2, #f4f4f5)', padding: '2px 8px', borderRadius: '4px', color: 'var(--muted)' }}>ai_partner: {aiPartner}</code>}
        </div>
        {warnings.length > 0 && (
          <div style={{ background: 'var(--warning-bg, #fef3c7)', border: '1px solid var(--warning-border, #fcd34d)', borderRadius: '6px', padding: '0.5rem 0.75rem' }}>
            {warnings.map((w) => <div key={w} style={{ fontSize: '0.78rem', color: 'var(--warning, #92400e)' }}>⚠ {w}</div>)}
          </div>
        )}
        <div>
          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.25rem' }}>Persona</div>
          <code style={{ fontSize: '0.8rem' }}>{agentName || '(not set)'} · {orgName || '(org not set)'}</code>
        </div>
        <div>
          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.25rem' }}>Voice / Model</div>
          <code style={{ fontSize: '0.8rem' }}>{voice} · {model}</code>
        </div>
        <div>
          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.25rem' }}>Opening line</div>
          <div style={{ background: 'var(--surface-2, #f4f4f5)', borderRadius: '6px', padding: '0.5rem 0.75rem', fontSize: '0.85rem', fontStyle: 'italic' }}>
            "{opening}"
          </div>
        </div>
        <div>
          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.25rem' }}>System prompt</div>
          <pre style={{ background: 'var(--surface-2, #f4f4f5)', borderRadius: '6px', padding: '0.75rem', fontSize: '0.78rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '300px', overflowY: 'auto', margin: 0 }}>{systemPrompt}</pre>
        </div>
      </div>
    </details>
  );
}

type ConversationItem =
  | {
      kind: 'turn';
      id: string;
      role: string;
      text: string;
      time: string;
      sortAt: number;
      interrupted: boolean;
    }
  | {
      kind: 'event';
      id: string;
      title: string;
      text: string;
      time: string;
      sortAt: number;
      severity: CallEvent['severity'];
    };

// Counts a number up from 0 to `target` over `duration`ms.
// Returns the current display value and a ref to attach to the element
// (adds/removes .counting CSS class so the shimmer animation fires).
function useCountUp(target: number, duration = 600) {
  const [value, setValue] = useState(0);
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (target === 0) { setValue(0); return; }
    const start = performance.now();
    ref.current?.classList.add('counting');
    const tick = (now: number) => {
      const progress = Math.min((now - start) / duration, 1);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(Math.round(target * eased));
      if (progress < 1) requestAnimationFrame(tick);
      else ref.current?.classList.remove('counting');
    };
    requestAnimationFrame(tick);
  }, [target, duration]);
  return { value, ref };
}

function AnalyticsView({ bots, campaigns }: { bots: BotType[]; campaigns: Campaign[] }) {
  const [analytics, setAnalytics] = useState<OutcomeAnalytics | null>(null);
  const [alert, setAlert] = useState<QualityAlert | null>(null);
  const [loading, setLoading] = useState(false);
  const [botId, setBotId] = useState('');
  const [campaignId, setCampaignId] = useState('');
  const [hours, setHours] = useState<number | ''>('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');

  async function load() {
    setLoading(true);
    try {
      const params: Parameters<typeof api.outcomeAnalytics>[0] = {};
      if (botId) params.bot_id = botId;
      if (campaignId) params.campaign_id = campaignId;
      if (hours) params.hours = Number(hours);
      if (startDate) params.start_date = startDate;
      if (endDate) params.end_date = endDate;
      const [a, q] = await Promise.all([
        api.outcomeAnalytics(params),
        api.qualityAlerts(1, 30),
      ]);
      setAnalytics(a);
      setAlert(q);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  const statusEntries = analytics ? Object.entries(analytics.by_status).sort((a, b) => b[1] - a[1]) : [];
  const outcomeEntries = analytics ? Object.entries(analytics.by_outcome).sort((a, b) => b[1] - a[1]) : [];
  const total = analytics?.total || 0;
  const { value: animatedTotal, ref: totalRef } = useCountUp(total);
  const { value: animatedNatural, ref: naturalRef } = useCountUp(analytics?.ended_naturally || 0);

  return (
    <section className="content-grid two-col">
      <div className="panel">
        <div className="panel-header">
          <div><h2>Call outcome analytics</h2><p>Aggregated over selected time window.</p></div>
          <button onClick={load} disabled={loading}><RefreshCw size={14} /> {loading ? 'Loading…' : 'Refresh'}</button>
        </div>

        {alert?.alert && (
          <div className="callout" style={{ background: 'rgba(239,68,68,0.1)', borderColor: 'var(--error)', marginBottom: '12px' }}>
            <AlertTriangle size={18} style={{ color: 'var(--error)' }} />
            <strong style={{ color: 'var(--error)' }}>Quality alert:</strong> {alert.message}
          </div>
        )}

        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '14px' }}>
          <select value={botId} onChange={e => setBotId(e.target.value)} style={{ fontSize: '0.82rem' }}>
            <option value="">All bots</option>
            {bots.map(b => <option key={b._id} value={b._id}>{b.name}</option>)}
          </select>
          <select value={campaignId} onChange={e => setCampaignId(e.target.value)} style={{ fontSize: '0.82rem' }}>
            <option value="">All campaigns</option>
            {campaigns.map(c => <option key={c._id} value={c.campaign_key}>{c.name}</option>)}
          </select>
          <select value={hours} onChange={e => setHours(e.target.value === '' ? '' : Number(e.target.value))} style={{ fontSize: '0.82rem' }}>
            <option value="">Custom date range</option>
            <option value={1}>Last 1 hour</option>
            <option value={6}>Last 6 hours</option>
            <option value={24}>Last 24 hours</option>
            <option value={168}>Last 7 days</option>
            <option value={720}>Last 30 days</option>
          </select>
          {!hours && (
            <>
              <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} style={{ fontSize: '0.82rem' }} />
              <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} style={{ fontSize: '0.82rem' }} />
            </>
          )}
          <button onClick={load} disabled={loading}>Apply</button>
        </div>

        {analytics && (
          <>
            <div className="metric-board" style={{ marginBottom: '16px' }}>
              <div className="metric" ref={totalRef as React.RefObject<HTMLDivElement>}>
                <span>Total calls</span>
                <strong>{animatedTotal.toLocaleString('en-IN')}</strong>
              </div>
              <div className="metric" ref={naturalRef as React.RefObject<HTMLDivElement>}>
                <span>Ended naturally</span>
                <strong>{animatedNatural} <small style={{ fontWeight: 400, fontSize: '0.75rem' }}>({total ? Math.round(analytics.ended_naturally / total * 100) : 0}%)</small></strong>
              </div>
              <div className="metric">
                <span>Avg duration</span>
                <strong>{analytics.avg_duration_sec}s</strong>
              </div>
            </div>

            <h3 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>By status</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '16px' }}>
              {statusEntries.map(([status, count]) => (
                <div key={status} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <StatusPill value={status} />
                  <div style={{ flex: 1, background: 'var(--bg-tertiary)', borderRadius: '3px', height: '8px', overflow: 'hidden' }}>
                    <div style={{ width: `${total ? count / total * 100 : 0}%`, background: 'var(--accent)', height: '100%', transition: 'width 0.3s' }} />
                  </div>
                  <span style={{ fontSize: '0.82rem', minWidth: '50px', textAlign: 'right' }}>{count} ({total ? Math.round(count / total * 100) : 0}%)</span>
                </div>
              ))}
              {!statusEntries.length && <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>No data for this window.</p>}
            </div>

            {outcomeEntries.length > 0 && (
              <>
                <h3 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>By outcome tag</h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {outcomeEntries.map(([outcome, count]) => (
                    <div key={outcome} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span style={{ minWidth: '140px', fontSize: '0.82rem' }}>{outcome}</span>
                      <div style={{ flex: 1, background: 'var(--bg-tertiary)', borderRadius: '3px', height: '8px', overflow: 'hidden' }}>
                        <div style={{ width: `${total ? count / total * 100 : 0}%`, background: 'var(--success)', height: '100%', transition: 'width 0.3s' }} />
                      </div>
                      <span style={{ fontSize: '0.82rem', minWidth: '40px', textAlign: 'right' }}>{count}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        )}
        {loading && !analytics && <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Loading…</p>}
      </div>

      <div className="panel">
        <h2>Quality monitoring</h2>
        <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '16px' }}>
          Alert fires when disconnected/error calls exceed 30% in the last hour (min 5 calls).
        </p>
        {alert && (
          <div className="detail-list">
            <Detail label="Window" value={`Last ${alert.hours}h`} />
            <Detail label="Total calls" value={String(alert.total_calls)} />
            <Detail label="Bad calls" value={String(alert.bad_calls)} />
            <Detail label="Bad rate" value={`${alert.bad_pct}%`} />
            <Detail label="Status" value={alert.alert ? '🔴 ALERT' : '🟢 OK'} />
          </div>
        )}
        <div className="callout" style={{ marginTop: '16px' }}>
          <BarChart2 size={18} />
          Run this endpoint from a cron job to get Slack/email alerts: <code>GET /api/analytics/quality-alerts?hours=1&threshold_pct=30</code>
        </div>
      </div>
    </section>
  );
}

function TranscriptsView({
  transcripts,
  selectedTranscript,
  localRecordingByRoom,
  callEvents,
  bots,
  campaigns,
  loading,
  searchText,
  onSearchText,
  filters,
  onFiltersChange,
  onSelect,
  onNavigateTest
}: {
  transcripts: Transcript[];
  selectedTranscript?: Transcript;
  localRecordingByRoom: Record<string, TestRecordingLookup>;
  callEvents: CallEvent[];
  bots: BotType[];
  campaigns: Campaign[];
  loading?: boolean;
  searchText: string;
  onSearchText: (value: string) => void;
  filters: { status: string; outcome: string; campaign_id: string; bot_id: string; start_date: string; end_date: string };
  onFiltersChange: (f: typeof filters) => void;
  onSelect: (id: string) => void;
  onNavigateTest?: () => void;
}) {
  const [showFilters, setShowFilters] = useState(false);
  const conversationItems = useMemo(
    () => buildConversationItems(selectedTranscript, callEvents),
    [selectedTranscript, callEvents]
  );
  const localRecording = selectedTranscript?.room_name
    ? localRecordingByRoom[selectedTranscript.room_name]
    : undefined;
  const recordingUrl = selectedTranscript?.recording_url || localRecording?.recording_url || '';
  const recordingSource = selectedTranscript?.recording_source || localRecording?.recording_source || '';

  const activeFilterCount = Object.values(filters).filter(Boolean).length;

  // Client-side apply active filters on top of the already-searched list
  const displayedTranscripts = useMemo(() => {
    return transcripts.filter(t => {
      if (filters.status && t.status !== filters.status) return false;
      if (filters.campaign_id && t.campaign_id !== filters.campaign_id) return false;
      if (filters.bot_id && t.bot_id !== filters.bot_id) return false;
      return true;
    });
  }, [transcripts, filters]);

  const csvUrl = api.exportCsvUrl({
    bot_id: filters.bot_id || undefined,
    campaign_id: filters.campaign_id || undefined,
    status: filters.status || undefined,
    outcome: filters.outcome || undefined,
    start_date: filters.start_date || undefined,
    end_date: filters.end_date || undefined,
    text: searchText.trim() || undefined,
  });

  return (
    <section className="content-grid transcripts-grid">
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Saved transcripts</h2>
            <p>Raw transcripts are stored forever with call config snapshots.</p>
          </div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            <div className="search-box"><Search size={15} /><input value={searchText} onChange={(event) => onSearchText(event.target.value)} placeholder="Search call, lead, campaign or text" /></div>
            <button
              title="Filters"
              onClick={() => setShowFilters(v => !v)}
              style={{ position: 'relative' }}
            >
              <Filter size={14} /> Filters {activeFilterCount > 0 && <span className="pill active" style={{ marginLeft: '4px', fontSize: '0.72rem', padding: '0 6px' }}>{activeFilterCount}</span>}
            </button>
            <a href={csvUrl} download style={{ textDecoration: 'none' }}>
              <button title="Export CSV"><Download size={14} /> Export</button>
            </a>
          </div>
        </div>
        {showFilters && (
          <div style={{ padding: '10px 16px', background: 'var(--bg-tertiary)', borderBottom: '1px solid var(--border)', display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '0.8rem' }}>
              Status
              <select value={filters.status} onChange={e => onFiltersChange({ ...filters, status: e.target.value })} style={{ fontSize: '0.8rem' }}>
                <option value="">All</option>
                {['completed', 'not_interested', 'disconnected', 'voicemail', 'dnc', 'error', 'busy', 'no_answer'].map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '0.8rem' }}>
              Campaign
              <select value={filters.campaign_id} onChange={e => onFiltersChange({ ...filters, campaign_id: e.target.value })} style={{ fontSize: '0.8rem' }}>
                <option value="">All</option>
                {campaigns.map(c => <option key={c._id} value={c.campaign_key}>{c.name}</option>)}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '0.8rem' }}>
              Bot
              <select value={filters.bot_id} onChange={e => onFiltersChange({ ...filters, bot_id: e.target.value })} style={{ fontSize: '0.8rem' }}>
                <option value="">All</option>
                {bots.map(b => <option key={b._id} value={b._id}>{b.name}</option>)}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '0.8rem' }}>
              From date
              <input type="date" value={filters.start_date} onChange={e => onFiltersChange({ ...filters, start_date: e.target.value })} style={{ fontSize: '0.8rem' }} />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '0.8rem' }}>
              To date
              <input type="date" value={filters.end_date} onChange={e => onFiltersChange({ ...filters, end_date: e.target.value })} style={{ fontSize: '0.8rem' }} />
            </label>
            <button onClick={() => onFiltersChange({ status: '', outcome: '', campaign_id: '', bot_id: '', start_date: '', end_date: '' })} style={{ fontSize: '0.8rem' }}>
              Clear
            </button>
          </div>
        )}

        <div className="table-scroll"><table>
          <thead>
            <tr><th>Call</th><th>Lead</th><th>Status</th><th>Duration</th><th>Turns</th><th>Created</th></tr>
          </thead>
          <tbody>
            {loading && !transcripts.length ? (
              <SkeletonTableBody cols={6} rows={5} />
            ) : displayedTranscripts.length === 0 ? (
              <tr><td colSpan={6}>
                {transcripts.length === 0 ? (
                  <EmptyState
                    icon={<FileText size={32} />}
                    heading="No transcripts yet"
                    description="Transcripts appear here after calls complete. Start a test call to see your first one."
                    action={onNavigateTest ? { label: 'Go to Test Call', onClick: onNavigateTest } : undefined}
                  />
                ) : (
                  <EmptyState
                    icon={<Search size={28} />}
                    heading="No matching transcripts"
                    description="Try adjusting your filters or search text."
                  />
                )}
              </td></tr>
            ) : displayedTranscripts.map((item) => (
              <tr key={item._id} onClick={() => onSelect(item._id)} className={item._id === selectedTranscript?._id ? 'selected-row' : ''}>
                <td>
                  <CopyableId value={item.call_id} label={item.call_id} />
                  <small>{item.campaign_id || '-'}</small>
                </td>
                <td><CopyableId value={item.lead_id} /></td>
                <td><StatusPill value={item.status || 'unknown'} /></td>
                <td>{item.call_duration_sec || 0}s</td>
                <td>{item.transcript_count ?? item.transcript?.length ?? 0}</td>
                <td><TimeAgo value={item.created_at} /></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>
      <div className="panel transcript-detail">
        <div className="conversation-header">
          <div>
            <h2>Transcript detail</h2>
            <p>{selectedTranscript?.call_id || 'Select a call'} · {selectedTranscript?.status || 'unknown'}</p>
          </div>
          {selectedTranscript && <StatusPill value={selectedTranscript.status || 'unknown'} />}
        </div>
        {selectedTranscript ? (
          <>
            <div className="detail-list">
              <Detail label="Call ID" value={selectedTranscript.call_id || '-'} copyable />
              <Detail label="Bot version" value={selectedTranscript.bot_version_id ? shortId(selectedTranscript.bot_version_id) : '-'} />
              <Detail label="Callback" value={selectedTranscript.callback_status || '-'} />
              <Detail label="Transcript source" value={transcriptSourceLabel(selectedTranscript)} />
              <Detail label="Verification" value={selectedTranscript.verified_transcript_status || 'legacy'} />
              <Detail label="Max response delay" value={maxResponseDelayLabel(selectedTranscript)} />
              <Detail label="Recording" value={recordingUrl || 'No recording saved'} />
            </div>
            <div className="source-strip">
              <span className={`source-badge ${selectedTranscript.verified_transcript_status || 'legacy'}`}>
                {transcriptSourceLabel(selectedTranscript)}
              </span>
              {(selectedTranscript.transcript_quality_flags || []).map((flag) => (
                <span className="quality-flag" key={flag}>{titleCase(flag)}</span>
              ))}
              {selectedTranscript.verified_transcript_error && (
                <span className="quality-flag error">{selectedTranscript.verified_transcript_error}</span>
              )}
            </div>
            {recordingUrl && (
              <div className="recording-player">
                <div>
                  <strong>Call recording</strong>
                  <span>{recordingSource === 'dashboard_test_local' ? 'Local dashboard test recording' : 'Dialer recording'}</span>
                </div>
                <audio controls preload="metadata" src={apiUrl(recordingUrl)} />
              </div>
            )}
            <div className="chat-transcript">
              {conversationItems.map((item) => (
                item.kind === 'event' ? (
                  <div className={`chat-event ${item.severity}`} key={item.id}>
                    <span>{item.time}</span>
                    <strong>{item.title}</strong>
                    <p>{item.text}</p>
                  </div>
                ) : (
                  <div className={`chat-turn ${item.role}`} key={item.id}>
                    <div className="chat-avatar">{item.role === 'assistant' ? <Bot size={15} /> : <Mic size={15} />}</div>
                    <div className="chat-bubble">
                      <div className="chat-meta">
                        <strong>{item.role === 'assistant' ? 'Tanya / Assistant' : item.role === 'user' ? 'User' : item.role === 'recording' ? 'Verified recording' : titleCase(item.role)}</strong>
                        <span>{item.time}</span>
                      </div>
                      <p>{item.text}</p>
                      {item.interrupted && <small>Interrupted during this turn</small>}
                    </div>
                  </div>
                )
              ))}
              {!conversationItems.length && <p className="muted">No transcript turns saved for this call yet.</p>}
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
  const [adminDraft, setAdminDraft] = useState({
    mis_api_base: '',
    default_inactivity_phrase: '',
    default_close_markers: ''
  });
  const [adminSaved, setAdminSaved] = useState(false);

  useEffect(() => {
    setDraft({
      livekit_api_url: runtimeSettings?.livekit_api_url || '',
      livekit_browser_url: runtimeSettings?.livekit_browser_url || '',
      livekit_agent_name: runtimeSettings?.livekit_agent_name || ''
    });
  }, [runtimeSettings?._id, runtimeSettings?.livekit_api_url, runtimeSettings?.livekit_browser_url, runtimeSettings?.livekit_agent_name]);

  // Load admin settings from localStorage (no backend yet — these are UI-layer hints)
  useEffect(() => {
    const stored = localStorage.getItem('adminPlatformSettings');
    if (stored) {
      try { setAdminDraft(JSON.parse(stored)); } catch { /* ignore */ }
    }
  }, []);

  function saveAdminSettings() {
    localStorage.setItem('adminPlatformSettings', JSON.stringify(adminDraft));
    setAdminSaved(true);
    setTimeout(() => setAdminSaved(false), 2000);
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
          <span style={{ fontSize: '0.72rem', background: 'var(--warning-bg, #fef3c7)', color: 'var(--warning, #92400e)', border: '1px solid var(--warning-border, #fcd34d)', borderRadius: '4px', padding: '2px 8px', fontWeight: 600 }}>Admin only</span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
          <div>
            <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Platform defaults</div>
            <div className="form-grid">
              <label>
                MIS API base URL
                <input
                  value={adminDraft.mis_api_base}
                  onChange={(e) => setAdminDraft({ ...adminDraft, mis_api_base: e.target.value })}
                  placeholder="http://192.168.8.67:8000"
                />
                <small>Platform default for lead fetch. Set MIS_API_BASE in .env for production.</small>
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
            <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Role & access</div>
            <div className="callout" style={{ marginBottom: '0.75rem' }}>
              <Settings size={16} />
              <div>
                <strong>Builder modes</strong>
                <p style={{ margin: '4px 0 0', fontSize: '0.82rem' }}>The <strong>PM</strong> toggle shows only core fields (prompt, voice, opening/closing line). The <strong>Advanced</strong> toggle reveals all admin fields. Mode is remembered per browser session.</p>
              </div>
            </div>
            <div className="callout">
              <ShieldCheck size={16} />
              <div>
                <strong>Coming soon: role-based access</strong>
                <p style={{ margin: '4px 0 0', fontSize: '0.82rem' }}>Once auth is added, PMs will be locked to PM mode. Admins will see the Advanced toggle. The admin tools section will require an admin role to view.</p>
              </div>
            </div>
          </div>
        </div>

        <div className="button-row" style={{ marginTop: '1rem' }}>
          <button className="primary" onClick={saveAdminSettings}>
            <Save size={15} /> {adminSaved ? 'Saved ✓' : 'Save admin settings'}
          </button>
          <span style={{ fontSize: '0.78rem', color: 'var(--muted)' }}>
            Note: MIS API base and phrase defaults require a bot worker restart to take effect.
          </span>
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
        <div className="table-scroll"><table>
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
                  <td><TimeAgo value={row.updated_at} /></td>
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
                  <td><TimeAgo value={row.updated_at} /></td>
                  <td>
                    <div className="button-row">
                      <button onClick={() => onStartEdit(row)}><Pencil size={13} /> Edit</button>
                      <button className="fallback-button" onClick={() => onConfirmDelete(row)}><Trash2 size={14} /> Delete</button>
                    </div>
                  </td>
                </tr>
              )
            ))}
            {!rows.length && <tr><td colSpan={5}>No phrases yet for this category. Add the first one above.</td></tr>}
          </tbody>
        </table></div>
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
        <div className="table-scroll"><table>
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
                  <td><TimeAgo value={row.updated_at} /></td>
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
                  <td><TimeAgo value={row.updated_at} /></td>
                  <td><button onClick={() => startEditOutcome(row)}><Pencil size={13} /> Edit</button></td>
                </tr>
              )
            ))}
            {!outcomes.length && <tr><td colSpan={5}>Outcome catalog is empty. The backend seeds defaults on next startup.</td></tr>}
          </tbody>
        </table></div>
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
          <div className="table-scroll"><table>
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
                  <td><TimeAgo value={row.updated_at} /></td>
                </tr>
              ))}
              {!settings.length && <tr><td colSpan={3}>No languages yet. Click "New language" to add the first.</td></tr>}
            </tbody>
          </table></div>
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
            </button>            {creating && <button onClick={cancelNew}>Cancel</button>}
          </div>
        </div>
      </div>
    </>
  );
}

const OUTCOME_CATEGORIES: Record<string, { label: string; group: 'completed' | 'retry' | 'stop' | 'dnc' }> = {
  'Approved': { label: 'Approved', group: 'completed' },
  'Enriched': { label: 'Enriched', group: 'completed' },
  'Interested': { label: 'Interested', group: 'completed' },
  'Short Hangup': { label: 'Short Hangup', group: 'retry' },
  'Voicemail': { label: 'Voicemail', group: 'retry' },
  'Could Not Confirm': { label: 'Could Not Confirm', group: 'retry' },
  'Call Rescheduled': { label: 'Call Rescheduled', group: 'retry' },
  'Technical Issue - Call Connected': { label: 'Technical Issue', group: 'retry' },
  'Language Issue': { label: 'Language Issue', group: 'retry' },
  'Other Cases': { label: 'Other Cases', group: 'retry' },
  'Not Interested': { label: 'Not Interested', group: 'stop' },
  'Wrong Number': { label: 'Wrong Number', group: 'stop' },
  'Already Spoken': { label: 'Already Spoken', group: 'stop' },
  'Will do it Myself': { label: 'Will do it Myself', group: 'stop' },
  'Alternate Number': { label: 'Alternate Number', group: 'stop' },
  'Seller Intent': { label: 'Seller Intent', group: 'stop' },
  'Abusive Lead': { label: 'Abusive Lead', group: 'dnc' },
  "DNC Client : Don't Call Further": { label: "DNC Client", group: 'dnc' },
};

const DEFAULT_OUTCOME_RULES: OutcomeRule[] = [
  { outcome: 'Short Hangup', action: 'retry', max_attempts: 3, retry_after_min: 30 },
  { outcome: 'Voicemail', action: 'retry', max_attempts: 2, retry_after_min: 120 },
  { outcome: 'Wrong Number', action: 'stop' },
  { outcome: 'Approved', action: 'completed' },
  { outcome: 'Enriched', action: 'completed' },
  { outcome: 'Interested', action: 'completed' },
  { outcome: 'Not Interested', action: 'stop' },
  { outcome: 'Could Not Confirm', action: 'retry', max_attempts: 2, retry_after_min: 60 },
  { outcome: 'Alternate Number', action: 'stop' },
  { outcome: 'Already Spoken', action: 'stop' },
  { outcome: 'Will do it Myself', action: 'stop' },
  { outcome: 'Call Rescheduled', action: 'retry', max_attempts: 1, retry_after_min: 0 },
  { outcome: 'Seller Intent', action: 'stop' },
  { outcome: 'Abusive Lead', action: 'dnc' },
  { outcome: "DNC Client : Don't Call Further", action: 'dnc' },
  { outcome: 'Other Cases', action: 'retry', max_attempts: 1, retry_after_min: 60 },
  { outcome: 'Technical Issue - Call Connected', action: 'retry', max_attempts: 2, retry_after_min: 15 },
  { outcome: 'Language Issue', action: 'retry', max_attempts: 1, retry_after_min: 60 },
];

function buildDefaultStrategy(): DialingStrategy {
  return {
    enabled: true,
    outcome_rules: DEFAULT_OUTCOME_RULES,
    call_windows: [{ days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat'], start_time: '09:00', end_time: '20:00', timezone: 'Asia/Kolkata' }],
    attempt_sequence: [
      { attempt: 1, language: 'hindi' },
      { attempt: 2, language: 'hindi' },
      { attempt: 3, language: 'english' },
    ],
    max_attempts_total: 5,
    max_attempts_per_day: 2,
    lead_expiry_days: 30,
    priority: 'normal',
  };
}

function mergeWithDefaults(existing: DialingStrategy | undefined): DialingStrategy {
  const defaults = buildDefaultStrategy();
  if (!existing) return defaults;
  // Merge outcome_rules: start from defaults, overlay existing rules by outcome key
  const existingByOutcome = new Map((existing.outcome_rules || []).map((r) => [r.outcome, r]));
  const mergedRules = defaults.outcome_rules.map((defaultRule) => existingByOutcome.get(defaultRule.outcome) || defaultRule);
  // Also include any rules in existing that aren't in defaults
  (existing.outcome_rules || []).forEach((r) => {
    if (!mergedRules.find((mr) => mr.outcome === r.outcome)) mergedRules.push(r);
  });
  return {
    ...defaults,
    ...existing,
    outcome_rules: mergedRules,
  };
}

function formatDelay(min?: number): string {
  if (min === undefined || min === null) return '-';
  if (min === 0) return 'immediate';
  if (min < 60) return `${min}m`;
  const hours = Math.floor(min / 60);
  const remainder = min % 60;
  return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
}

function DialingStrategyBuilder({
  campaign,
  bots,
  languages,
  outcomes,
  onBack,
  onSave,
  saveState
}: {
  campaign: Campaign;
  bots: BotType[];
  languages: LanguageOption[];
  outcomes: OutcomeEntry[];
  onBack: () => void;
  onSave: (strategy: DialingStrategy) => Promise<void>;
  saveState?: 'idle' | 'running' | 'failed';
}) {
  const [strategy, setStrategy] = useState<DialingStrategy>(() =>
    mergeWithDefaults(campaign.dialing_strategy)
  );
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setStrategy(mergeWithDefaults(campaign.dialing_strategy));
    setDirty(false);
  }, [campaign.campaign_key]);

  function updateStrategy(patch: Partial<DialingStrategy>) {
    setStrategy((prev) => ({ ...prev, ...patch }));
    setDirty(true);
  }

  function updateRule(outcome: string, patch: Partial<OutcomeRule>) {
    setStrategy((prev) => ({
      ...prev,
      outcome_rules: prev.outcome_rules.map((r) =>
        r.outcome === outcome ? { ...r, ...patch } : r
      )
    }));
    setDirty(true);
  }

  async function handleSave() {
    await onSave(strategy);
    setDirty(false);
  }

  const retryCount = strategy.outcome_rules.filter((r) => r.action === 'retry').length;
  const stopCount = strategy.outcome_rules.filter((r) => r.action === 'stop').length;
  const dncCount = strategy.outcome_rules.filter((r) => r.action === 'dnc').length;
  const completedCount = strategy.outcome_rules.filter((r) => r.action === 'completed').length;

  return (
    <section className="strategy-builder">
      <div className="strategy-topbar">
        <div className="strategy-breadcrumb">
          <button onClick={onBack}><ChevronRight className="rotate-180" size={16} /> Campaigns</button>
          <ChevronRight size={14} />
          <strong>{campaign.name}</strong>
        </div>
        <div className="strategy-header-right">
          <div className="strategy-stats">
            <span className="strategy-stat retry">{retryCount} retry</span>
            <span className="strategy-stat stop">{stopCount} stop</span>
            <span className="strategy-stat dnc">{dncCount} DNC</span>
            <span className="strategy-stat completed">{completedCount} done</span>
          </div>
          <label className="toggle-inline">
            <input
              type="checkbox"
              checked={strategy.enabled}
              onChange={(e) => updateStrategy({ enabled: e.target.checked })}
            />
            {strategy.enabled ? 'Strategy active' : 'Strategy paused'}
          </label>
          <button
            className={saveState === 'failed' ? 'fallback-button' : 'primary'}
            onClick={handleSave}
            disabled={saveState === 'running' || !dirty}
          >
            <Save size={16} />
            {saveState === 'running' ? 'Saving...' : saveState === 'failed' ? 'Retry save' : dirty ? 'Save strategy' : 'Saved'}
          </button>
        </div>
      </div>

      <div className="strategy-layout">
        <div className="strategy-main">
          <div className="panel">
            <div className="panel-header">
              <div>
                <h2>Outcome rules</h2>
                <p>For each call outcome, set whether to retry, stop, or block the number (DNC).</p>
              </div>
              <GitBranch size={18} />
            </div>
            <OutcomeRulesTable
              rules={strategy.outcome_rules}
              bots={bots}
              languages={languages}
              onUpdate={updateRule}
            />
          </div>
        </div>

        <aside className="strategy-rail">
          <div className="panel compact">
            <div className="panel-header">
              <div><h2>Global limits</h2><p>Caps that apply across all outcome rules.</p></div>
            </div>
            <div className="form-section">
              <div className="form-grid">
                <label>
                  Max total attempts
                  <input
                    type="number"
                    min={1} max={50}
                    value={strategy.max_attempts_total}
                    onChange={(e) => updateStrategy({ max_attempts_total: Number(e.target.value) })}
                  />
                </label>
                <label>
                  Max attempts per day
                  <input
                    type="number"
                    min={1} max={20}
                    value={strategy.max_attempts_per_day}
                    onChange={(e) => updateStrategy({ max_attempts_per_day: Number(e.target.value) })}
                  />
                </label>
                <label>
                  Lead expiry (days)
                  <input
                    type="number"
                    min={1} max={365}
                    value={strategy.lead_expiry_days}
                    onChange={(e) => updateStrategy({ lead_expiry_days: Number(e.target.value) })}
                  />
                </label>
                <label>
                  Priority
                  <select
                    value={strategy.priority}
                    onChange={(e) => updateStrategy({ priority: e.target.value as DialingStrategy['priority'] })}
                  >
                    <option value="low">Low</option>
                    <option value="normal">Normal</option>
                    <option value="high">High</option>
                    <option value="urgent">Urgent</option>
                  </select>
                </label>
              </div>
            </div>
          </div>

          <div className="panel compact">
            <div className="panel-header">
              <div><h2>Call windows</h2><p>Only dial during these hours.</p></div>
            </div>
            <CallWindowsEditor
              windows={strategy.call_windows}
              onChange={(windows) => { updateStrategy({ call_windows: windows }); }}
            />
          </div>

          <div className="panel compact">
            <div className="panel-header">
              <div><h2>Attempt sequence</h2><p>Override bot or language per attempt number.</p></div>
              <button
                onClick={() => {
                  const nextAttempt = (strategy.attempt_sequence.length ? Math.max(...strategy.attempt_sequence.map((s) => s.attempt)) : 0) + 1;
                  updateStrategy({ attempt_sequence: [...strategy.attempt_sequence, { attempt: nextAttempt }] });
                }}
              >
                <Plus size={14} /> Add step
              </button>
            </div>
            <AttemptSequenceEditor
              steps={strategy.attempt_sequence}
              bots={bots}
              languages={languages}
              onChange={(steps) => { updateStrategy({ attempt_sequence: steps }); }}
            />
          </div>
        </aside>
      </div>
    </section>
  );
}

function OutcomeRulesTable({
  rules,
  bots,
  languages,
  onUpdate
}: {
  rules: OutcomeRule[];
  bots: BotType[];
  languages: LanguageOption[];
  onUpdate: (outcome: string, patch: Partial<OutcomeRule>) => void;
}) {
  const ruleByOutcome = new Map(rules.map((r) => [r.outcome, r]));

  return (
    <div className="outcome-rules-table">
      <div className="outcome-group-header completed-group">Positive outcomes — stop calling, lead is qualified</div>
      {['Approved', 'Enriched', 'Interested'].map((outcome) => {
        const rule = ruleByOutcome.get(outcome) || { outcome, action: 'completed' as const };
        return <OutcomeRuleRow key={outcome} rule={rule} bots={bots} languages={languages} onUpdate={onUpdate} />;
      })}

      <div className="outcome-group-header retry-group">Retry outcomes — call again after a delay</div>
      {['Short Hangup', 'Voicemail', 'Could Not Confirm', 'Call Rescheduled', 'Technical Issue - Call Connected', 'Language Issue', 'Other Cases'].map((outcome) => {
        const rule = ruleByOutcome.get(outcome) || { outcome, action: 'retry' as const, max_attempts: 2, retry_after_min: 60 };
        return <OutcomeRuleRow key={outcome} rule={rule} bots={bots} languages={languages} onUpdate={onUpdate} />;
      })}

      <div className="outcome-group-header stop-group">Stop outcomes — no more calls for this lead</div>
      {['Not Interested', 'Wrong Number', 'Already Spoken', 'Will do it Myself', 'Alternate Number', 'Seller Intent'].map((outcome) => {
        const rule = ruleByOutcome.get(outcome) || { outcome, action: 'stop' as const };
        return <OutcomeRuleRow key={outcome} rule={rule} bots={bots} languages={languages} onUpdate={onUpdate} />;
      })}

      <div className="outcome-group-header dnc-group">Do Not Call — permanently block this number</div>
      {["DNC Client : Don't Call Further", 'Abusive Lead'].map((outcome) => {
        const rule = ruleByOutcome.get(outcome) || { outcome, action: 'dnc' as const };
        return <OutcomeRuleRow key={outcome} rule={rule} bots={bots} languages={languages} onUpdate={onUpdate} />;
      })}
    </div>
  );
}

function OutcomeRuleRow({
  rule,
  bots,
  languages,
  onUpdate
}: {
  rule: OutcomeRule;
  bots: BotType[];
  languages: LanguageOption[];
  onUpdate: (outcome: string, patch: Partial<OutcomeRule>) => void;
}) {
  const cat = OUTCOME_CATEGORIES[rule.outcome];
  const group = cat?.group || 'stop';
  const isRetry = rule.action === 'retry';

  return (
    <div className={`outcome-rule-row outcome-row-${group}`}>
      <div className="outcome-rule-name">
        <span className={`outcome-action-icon ${rule.action}`}>
          {rule.action === 'completed' && <CheckCircle2 size={14} />}
          {rule.action === 'retry' && <RefreshCw size={14} />}
          {rule.action === 'stop' && <PhoneOff size={14} />}
          {rule.action === 'dnc' && <Ban size={14} />}
        </span>
        <strong>{cat?.label || rule.outcome}</strong>
      </div>
      <div className="outcome-rule-controls">
        <select
          value={rule.action}
          onChange={(e) => onUpdate(rule.outcome, { action: e.target.value as OutcomeRule['action'] })}
          className="outcome-action-select"
        >
          <option value="retry">Retry</option>
          <option value="stop">Stop</option>
          <option value="dnc">DNC</option>
          <option value="completed">Completed</option>
        </select>
        {isRetry && (
          <>
            <label className="inline-label">
              <span>Attempts</span>
              <input
                type="number"
                min={1} max={20}
                value={rule.max_attempts ?? 2}
                onChange={(e) => onUpdate(rule.outcome, { max_attempts: Number(e.target.value) })}
                className="attempts-input"
              />
            </label>
            <label className="inline-label">
              <span>Delay (min)</span>
              <input
                type="number"
                min={0} max={10080}
                value={rule.retry_after_min ?? 60}
                onChange={(e) => onUpdate(rule.outcome, { retry_after_min: Number(e.target.value) })}
                className="delay-input"
              />
            </label>
            <label className="inline-label">
              <span>Language</span>
              <select
                value={rule.language_override || ''}
                onChange={(e) => onUpdate(rule.outcome, { language_override: e.target.value || undefined })}
                className="language-override-select"
              >
                <option value="">Same as strategy</option>
                {languages.map((lang) => (
                  <option key={lang.id} value={lang.id}>{lang.label}</option>
                ))}
              </select>
            </label>
          </>
        )}
        {!isRetry && (
          <span className="outcome-rule-summary">
            {rule.action === 'completed' ? 'Mark qualified, no follow-up' :
             rule.action === 'dnc' ? 'Block number permanently' :
             'Remove from queue'}
          </span>
        )}
      </div>
    </div>
  );
}

const DAY_OPTIONS = [
  { id: 'mon', label: 'Mon' },
  { id: 'tue', label: 'Tue' },
  { id: 'wed', label: 'Wed' },
  { id: 'thu', label: 'Thu' },
  { id: 'fri', label: 'Fri' },
  { id: 'sat', label: 'Sat' },
  { id: 'sun', label: 'Sun' },
];

function CallWindowsEditor({
  windows,
  onChange
}: {
  windows: CallWindow[];
  onChange: (windows: CallWindow[]) => void;
}) {
  const window = windows[0] || { days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat'], start_time: '09:00', end_time: '20:00', timezone: 'Asia/Kolkata' };

  function updateWindow(patch: Partial<CallWindow>) {
    onChange([{ ...window, ...patch }]);
  }

  function toggleDay(day: string) {
    const days = window.days.includes(day)
      ? window.days.filter((d) => d !== day)
      : [...window.days, day];
    updateWindow({ days });
  }

  return (
    <div className="call-window-editor">
      <div className="day-picker">
        {DAY_OPTIONS.map((day) => (
          <button
            key={day.id}
            className={window.days.includes(day.id) ? 'day-btn active' : 'day-btn'}
            onClick={() => toggleDay(day.id)}
            type="button"
          >
            {day.label}
          </button>
        ))}
      </div>
      <div className="form-grid">
        <label>
          From
          <input
            type="time"
            value={window.start_time}
            onChange={(e) => updateWindow({ start_time: e.target.value })}
          />
        </label>
        <label>
          To
          <input
            type="time"
            value={window.end_time}
            onChange={(e) => updateWindow({ end_time: e.target.value })}
          />
        </label>
        <label className="full">
          Timezone
          <input
            value={window.timezone}
            onChange={(e) => updateWindow({ timezone: e.target.value })}
            placeholder="Asia/Kolkata"
          />
        </label>
      </div>
    </div>
  );
}

function AttemptSequenceEditor({
  steps,
  bots,
  languages,
  onChange
}: {
  steps: AttemptStep[];
  bots: BotType[];
  languages: LanguageOption[];
  onChange: (steps: AttemptStep[]) => void;
}) {
  function updateStep(attempt: number, patch: Partial<AttemptStep>) {
    onChange(steps.map((s) => s.attempt === attempt ? { ...s, ...patch } : s));
  }

  function removeStep(attempt: number) {
    onChange(steps.filter((s) => s.attempt !== attempt));
  }

  if (!steps.length) {
    return <p className="muted" style={{ padding: '12px 0' }}>No steps defined. All attempts use the campaign default bot and language.</p>;
  }

  return (
    <div className="attempt-sequence">
      {steps.sort((a, b) => a.attempt - b.attempt).map((step) => (
        <div key={step.attempt} className="attempt-step">
          <span className="attempt-badge">#{step.attempt}</span>
          <div className="attempt-controls">
            <select
              value={step.language || ''}
              onChange={(e) => updateStep(step.attempt, { language: e.target.value || undefined })}
            >
              <option value="">Default language</option>
              {languages.map((lang) => (
                <option key={lang.id} value={lang.id}>{lang.label}</option>
              ))}
            </select>
            <select
              value={step.bot_id || ''}
              onChange={(e) => updateStep(step.attempt, { bot_id: e.target.value || undefined })}
            >
              <option value="">Default bot</option>
              {bots.map((bot) => (
                <option key={bot._id} value={bot._id}>{bot.name}</option>
              ))}
            </select>
          </div>
          <button className="danger-button" onClick={() => removeStep(step.attempt)} style={{ padding: '0 8px', minHeight: 32 }}>
            <Trash2 size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}

function NavItem({ icon, label, active, onClick }: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button className={`nav-item ${active ? 'active' : ''}`} onClick={onClick}>
      {icon}<span>{label}</span>
    </button>
  );
}

// Wraps the sidebar <nav> and drives the --proximity CSS variable on each
// child based on how close the pointer is. Each item independently scales
// and shifts — items far from the cursor stay still, nearby ones lift.
function SidebarNav({ children }: { children: React.ReactNode }) {
  const navRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;

    // Radius (px) within which items respond. Beyond this they get proximity=0.
    const RADIUS = 100;

    function handleMove(e: PointerEvent) {
      const items = nav!.querySelectorAll<HTMLElement>('.nav-item');
      items.forEach(el => {
        const rect = el.getBoundingClientRect();
        const centerY = rect.top + rect.height / 2;
        const dist = Math.abs(e.clientY - centerY);
        const proximity = Math.max(0, 1 - dist / RADIUS);
        el.style.setProperty('--proximity', proximity.toFixed(3));
      });
    }

    function handleLeave() {
      nav!.querySelectorAll<HTMLElement>('.nav-item').forEach(el => {
        el.style.setProperty('--proximity', '0');
      });
    }

    nav.addEventListener('pointermove', handleMove);
    nav.addEventListener('pointerleave', handleLeave);
    return () => {
      nav.removeEventListener('pointermove', handleMove);
      nav.removeEventListener('pointerleave', handleLeave);
    };
  }, []);

  return (
    <nav className="sidebar-nav" ref={navRef}>
      {children}
    </nav>
  );
}

function NavButton(props: { icon: React.ReactNode; label: string; active: boolean; onClick: () => void }) {
  return <NavItem {...props} />;
}

function Detail({ label, value, copyable }: { label: string; value: string; copyable?: boolean }) {
  return (
    <div className="detail">
      <span>{label}</span>
      {copyable && value !== '-' ? <CopyableId value={value} label={value} /> : <strong>{value}</strong>}
    </div>
  );
}

// ─── StatusPill with icon ────────────────────────────────────────────────────

const STATUS_ICONS: Record<string, React.ReactNode> = {
  completed:    <CheckCircle2 size={12} aria-hidden />,
  active:       <CheckCircle2 size={12} aria-hidden />,
  published:    <CheckCircle2 size={12} aria-hidden />,
  enabled:      <CheckCircle2 size={12} aria-hidden />,
  ready:        <CheckCircle2 size={12} aria-hidden />,
  running:      <Activity size={12} aria-hidden />,
  connecting:   <Activity size={12} aria-hidden />,
  disconnected: <XCircle size={12} aria-hidden />,
  failed:       <XCircle size={12} aria-hidden />,
  error:        <XCircle size={12} aria-hidden />,
  not_interested: <XCircle size={12} aria-hidden />,
  draft:        <Clock3 size={12} aria-hidden />,
  paused:       <Clock3 size={12} aria-hidden />,
  pending:      <Clock3 size={12} aria-hidden />,
  voicemail:    <Clock3 size={12} aria-hidden />,
  busy:         <Clock3 size={12} aria-hidden />,
  no_answer:    <Clock3 size={12} aria-hidden />,
};

function StatusPill({ value }: { value: string }) {
  const key = value.toLowerCase().replace(/\s+/g, '_');
  const icon = STATUS_ICONS[key];
  return (
    <span className={`pill ${key}`}>
      {icon && <span className="pill-icon">{icon}</span>}
      {value}
    </span>
  );
}

// ─── TimeAgo ─────────────────────────────────────────────────────────────────

function timeAgo(value?: string): string {
  if (!value) return '-';
  const date = parseApiDate(value);
  if (Number.isNaN(date.getTime())) return value;
  const diffSec = (Date.now() - date.getTime()) / 1000;
  if (diffSec < 60) return 'just now';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} min ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} hr ago`;
  if (diffSec < 172800) return 'yesterday';
  return formatDate(value);
}

function TimeAgo({ value }: { value?: string }) {
  const [, forceUpdate] = useState(0);
  useEffect(() => {
    const id = setInterval(() => forceUpdate(n => n + 1), 60_000);
    return () => clearInterval(id);
  }, []);
  const abs = value ? formatDate(value) : '-';
  return <span title={abs}>{timeAgo(value)}</span>;
}

// ─── CopyableId ──────────────────────────────────────────────────────────────

function CopyableId({ value, label }: { value?: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  if (!value || value === '-') return <span>{value || '-'}</span>;
  const display = label ?? value;
  function handleCopy(e: React.MouseEvent) {
    e.stopPropagation();
    if (!navigator.clipboard) {
      // Fallback for non-HTTPS or older browsers
      try {
        const ta = document.createElement('textarea');
        ta.value = value;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      } catch { /* silent */ }
      return;
    }
    navigator.clipboard.writeText(value)
      .then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })
      .catch(() => { /* permission denied or insecure context — fail silently */ });
  }
  return (
    <span className="copyable-id" title={`Click to copy: ${value}`} onClick={handleCopy}>
      <span className="copyable-id-text">{display}</span>
      <span className="copyable-id-icon">{copied ? <Check size={11} /> : <Copy size={11} />}</span>
    </span>
  );
}

// ─── SkeletonTableBody ────────────────────────────────────────────────────────

function SkeletonTableBody({ cols, rows = 4 }: { cols: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className="skeleton-row" aria-hidden>
          {Array.from({ length: cols }).map((_, c) => (
            <td key={c}><span className="skeleton-cell" style={{ width: `${55 + ((r * 37 + c * 29) % 35)}%` }} /></td>
          ))}
        </tr>
      ))}
    </>
  );
}

// ─── EmptyState ───────────────────────────────────────────────────────────────

function EmptyState({ icon, heading, description, action }: {
  icon: React.ReactNode;
  heading: string;
  description: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon">{icon}</div>
      <strong>{heading}</strong>
      <p>{description}</p>
      {action && <button className="primary" onClick={action.onClick}>{action.label}</button>}
    </div>
  );
}

// ─── Command Palette (Cmd+K) ──────────────────────────────────────────────────

type CmdKResult =
  | { kind: 'view';       view: View;       label: string; icon: React.ReactNode; description?: string }
  | { kind: 'bot';        bot: BotType }
  | { kind: 'campaign';   campaign: Campaign }
  | { kind: 'transcript'; transcript: Transcript };

type CmdKExtra = { botId?: string; transcriptId?: string; campaignKey?: string };

const CMD_VIEWS: CmdKResult[] = [
  { kind: 'view', view: 'bots',          label: 'Agents',        icon: <Bot size={15} />,          description: 'Manage voice agents' },
  { kind: 'view', view: 'campaigns',     label: 'Campaigns',     icon: <Megaphone size={15} />,    description: 'Campaign mappings' },
  { kind: 'view', view: 'test',          label: 'Test Call',     icon: <PhoneCall size={15} />,    description: 'Run a browser call' },
  { kind: 'view', view: 'transcripts',   label: 'Transcripts',   icon: <FileText size={15} />,     description: 'Browse call transcripts' },
  { kind: 'view', view: 'analytics',     label: 'Analytics',     icon: <BarChart2 size={15} />,    description: 'Outcomes and quality' },
  { kind: 'view', view: 'observability', label: 'Observability', icon: <Gauge size={15} />,        description: 'LiveKit and latency' },
  { kind: 'view', view: 'library',       label: 'Library',       icon: <BookOpen size={15} />,     description: 'Phrase library' },
  { kind: 'view', view: 'settings',      label: 'Settings',      icon: <Settings size={15} />,     description: 'Runtime settings' },
];

function matchScore(haystack: string, needle: string): number {
  if (!needle) return 1;
  const h = haystack.toLowerCase();
  const n = needle.toLowerCase();
  if (h === n) return 3;
  if (h.startsWith(n)) return 2;
  if (h.includes(n)) return 1;
  return 0;
}

function highlight(text: string, query: string): React.ReactNode {
  if (!query.trim()) return text;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return text;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="cmdk-mark">{text.slice(idx, idx + query.length)}</mark>
      {text.slice(idx + query.length)}
    </>
  );
}

function CommandPalette({ bots, campaigns, transcripts, onClose, onNavigate }: {
  bots: BotType[];
  campaigns: Campaign[];
  transcripts: Transcript[];
  onClose: () => void;
  onNavigate: (view: View, extra?: CmdKExtra) => void;
}) {
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const q = query.trim();

  const results: CmdKResult[] = useMemo(() => {
    const out: CmdKResult[] = [];

    // Views — always shown when query is empty, or when it matches
    CMD_VIEWS.forEach(v => {
      if (!q || matchScore(v.label, q) > 0 || matchScore(v.description || '', q) > 0) {
        out.push(v);
      }
    });

    if (q) {
      // Bots
      bots.forEach(bot => {
        const score = Math.max(
          matchScore(bot.name, q),
          matchScore(bot.description || '', q),
          matchScore(bot.assistant_id || '', q)
        );
        if (score > 0) out.push({ kind: 'bot', bot });
      });

      // Campaigns
      campaigns.forEach(campaign => {
        const score = Math.max(
          matchScore(campaign.name, q),
          matchScore(campaign.campaign_key, q)
        );
        if (score > 0) out.push({ kind: 'campaign', campaign });
      });

      // Transcripts — search recent ones (last 200, which is already what's loaded)
      const recent = transcripts.slice(0, 200);
      recent.forEach(t => {
        const score = Math.max(
          matchScore(t.call_id || '', q),
          matchScore(t.lead_id || '', q),
          matchScore(t.campaign_id || '', q),
          matchScore(t.status || '', q)
        );
        if (score > 0) out.push({ kind: 'transcript', transcript: t });
      });
    }

    return out.slice(0, 12);
  }, [q, bots, campaigns, transcripts]);

  // Reset cursor when results change
  useEffect(() => { setCursor(0); }, [results.length, q]);

  function selectResult(result: CmdKResult) {
    if (result.kind === 'view') { onNavigate(result.view); return; }
    if (result.kind === 'bot') { onNavigate('bots', { botId: result.bot._id }); return; }
    if (result.kind === 'campaign') { onNavigate('campaigns', { campaignKey: result.campaign.campaign_key }); return; }
    if (result.kind === 'transcript') { onNavigate('transcripts', { transcriptId: result.transcript._id }); return; }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setCursor(c => Math.min(c + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setCursor(c => Math.max(c - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (results[cursor]) selectResult(results[cursor]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  }

  // Scroll active item into view
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${cursor}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [cursor]);

  // Group results by kind for display
  const grouped: Array<{ label: string; items: Array<{ result: CmdKResult; idx: number }> }> = [];
  let globalIdx = 0;
  const groups: Record<string, { label: string; items: Array<{ result: CmdKResult; idx: number }> }> = {};
  results.forEach(r => {
    const g = r.kind === 'view' ? 'Views' : r.kind === 'bot' ? 'Agents' : r.kind === 'campaign' ? 'Campaigns' : 'Transcripts';
    if (!groups[g]) { groups[g] = { label: g, items: [] }; grouped.push(groups[g]); }
    groups[g].items.push({ result: r, idx: globalIdx++ });
  });

  function resultIcon(r: CmdKResult) {
    if (r.kind === 'view') return r.icon;
    if (r.kind === 'bot') return <Bot size={15} />;
    if (r.kind === 'campaign') return <Megaphone size={15} />;
    return <FileText size={15} />;
  }

  function resultLabel(r: CmdKResult) {
    if (r.kind === 'view') return highlight(r.label, q);
    if (r.kind === 'bot') return highlight(r.bot.name, q);
    if (r.kind === 'campaign') return highlight(r.campaign.name, q);
    return highlight(r.transcript.call_id || r.transcript._id, q);
  }

  function resultSub(r: CmdKResult) {
    if (r.kind === 'view') return r.description || '';
    if (r.kind === 'bot') return r.bot.description || r.bot.assistant_id || '';
    if (r.kind === 'campaign') return r.campaign.campaign_key;
    return `${r.transcript.status || 'unknown'} · ${r.transcript.campaign_id || ''}`;
  }

  return (
    <div className="cmdk-backdrop" onClick={onClose}>
      <div className="cmdk-panel" onClick={e => e.stopPropagation()}>
        <div className="cmdk-input-row">
          <Search size={16} className="cmdk-search-icon" />
          <input
            ref={inputRef}
            className="cmdk-input"
            placeholder="Search agents, campaigns, transcripts…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          {query && (
            <button className="cmdk-clear" onClick={() => { setQuery(''); inputRef.current?.focus(); }}>
              <XCircle size={15} />
            </button>
          )}
        </div>

        <div className="cmdk-results" ref={listRef}>
          {results.length === 0 && (
            <div className="cmdk-empty">No results for "{query}"</div>
          )}
          {grouped.map(group => (
            <div key={group.label} className="cmdk-group">
              <div className="cmdk-group-label">{group.label}</div>
              {group.items.map(({ result, idx }) => (
                <button
                  key={idx}
                  data-idx={idx}
                  className={`cmdk-item ${cursor === idx ? 'active' : ''}`}
                  onClick={() => selectResult(result)}
                  onMouseEnter={() => setCursor(idx)}
                >
                  <span className="cmdk-item-icon">{resultIcon(result)}</span>
                  <span className="cmdk-item-body">
                    <span className="cmdk-item-label">{resultLabel(result)}</span>
                    {resultSub(result) && <span className="cmdk-item-sub">{resultSub(result)}</span>}
                  </span>
                  <ChevronRight size={13} className="cmdk-item-arrow" />
                </button>
              ))}
            </div>
          ))}
        </div>

        <div className="cmdk-footer">
          <span><kbd className="kbd" style={{ fontSize: '10px' }}>↑↓</kbd> navigate</span>
          <span><kbd className="kbd" style={{ fontSize: '10px' }}>↵</kbd> open</span>
          <span><kbd className="kbd" style={{ fontSize: '10px' }}>Esc</kbd> close</span>
        </div>
      </div>
    </div>
  );
}

// ─── Keyboard shortcuts ───────────────────────────────────────────────────────

const SHORTCUT_MAP: Array<{ key: string; view: View; label: string }> = [
  { key: 'b', view: 'bots',          label: 'Go to Agents' },
  { key: 'c', view: 'campaigns',     label: 'Go to Campaigns' },
  { key: 't', view: 'test',          label: 'Go to Test Call' },
  { key: 'x', view: 'transcripts',   label: 'Go to Transcripts' },
  { key: 'a', view: 'analytics',     label: 'Go to Analytics' },
  { key: 'o', view: 'observability', label: 'Go to Observability' },
];

function useKeyboardShortcuts(
  navigate: (v: View) => void,
  toggleShortcuts: () => void,
  closeModal: () => void
) {
  // Store callbacks in refs so the event listener is registered once and always
  // calls the latest version — avoids re-registering on every render.
  const navigateRef = useRef(navigate);
  const toggleRef = useRef(toggleShortcuts);
  const closeRef = useRef(closeModal);
  navigateRef.current = navigate;
  toggleRef.current = toggleShortcuts;
  closeRef.current = closeModal;

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (target.isContentEditable) return;

      if (e.key === 'Escape') { closeRef.current(); return; }
      if (e.key === '?') { toggleRef.current(); return; }
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const key = e.key.toLowerCase();
      const match = SHORTCUT_MAP.find(s => s.key === key);
      if (match) { e.preventDefault(); navigateRef.current(match.view); }
    }

    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, []); // registered once, callbacks always current via refs
}

function ShortcutsModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 420 }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <Keyboard size={18} />
          <h2>Keyboard shortcuts</h2>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div style={{ padding: '16px 20px 20px' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {SHORTCUT_MAP.map(s => (
                <tr key={s.key} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 0', width: 80 }}><kbd className="kbd">{s.key.toUpperCase()}</kbd></td>
                  <td style={{ padding: '8px 0', color: 'var(--text-2)' }}>{s.label}</td>
                </tr>
              ))}
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '8px 0' }}><kbd className="kbd">Esc</kbd></td>
                <td style={{ padding: '8px 0', color: 'var(--text-2)' }}>Close modal</td>
              </tr>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '8px 0' }}><kbd className="kbd">⌘S</kbd></td>
                <td style={{ padding: '8px 0', color: 'var(--text-2)' }}>Save draft (in builder)</td>
              </tr>
              <tr>
                <td style={{ padding: '8px 0' }}><kbd className="kbd">?</kbd></td>
                <td style={{ padding: '8px 0', color: 'var(--text-2)' }}>Toggle this help</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
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

type AgentUiState = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking';

function AgentControlBar({
  connected,
  micEnabled,
  chatMessage,
  onChatMessageChange,
  onToggleMic,
  onSendChat,
  onDisconnect
}: {
  connected: boolean;
  micEnabled: boolean;
  chatMessage: string;
  onChatMessageChange: (value: string) => void;
  onToggleMic: () => void;
  onSendChat: (messageText: string) => void;
  onDisconnect: () => void;
}) {
  function submitChat(event: React.FormEvent) {
    event.preventDefault();
    onSendChat(chatMessage);
  }

  return (
    <div className="agent-control-bar" aria-label="Agent session controls">
      <button className={micEnabled ? 'control-button active' : 'control-button'} onClick={onToggleMic} disabled={!connected}>
        <Mic size={15} /> {micEnabled ? 'Mute' : 'Unmute'}
      </button>
      <form className="agent-chat-input" onSubmit={submitChat}>
        <MessageSquareText size={15} />
        <input
          value={chatMessage}
          onChange={(event) => onChatMessageChange(event.target.value)}
          placeholder="Send chat message"
          disabled={!connected}
        />
        <button className="control-button send" type="submit" disabled={!connected || !chatMessage.trim()}>
          <SendHorizontal size={15} />
        </button>
      </form>
      <button className="control-button danger" onClick={onDisconnect} disabled={!connected}>
        <Square size={15} /> End
      </button>
    </div>
  );
}

function AgentAudioVisualizerWave({ state }: { state: AgentUiState }) {
  return (
    <div className={`agent-wave ${state}`} aria-label={`Agent is ${state}`}>
      <div className="wave-line">
        {Array.from({ length: 34 }).map((_, index) => (
          <span key={index} style={{ animationDelay: `${index * 38}ms` }} />
        ))}
      </div>
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
    builder: 'Agents',
    campaigns: 'Campaigns',
    test: 'WebRTC Test Call',
    transcripts: 'Transcripts',
    analytics: 'Analytics',
    observability: 'Observability',
    library: 'Phrase Library',
    settings: 'Settings'
  }[view];
}

function subtitleFor(view: View) {
  return {
    bots: 'Manage, edit, publish, and safely delete voice agents.',
    builder: 'Manage, edit, publish, and safely delete voice agents.',
    campaigns: 'Connect one bot to one campaign and its lead/callback APIs.',
    test: 'Start a controlled browser call with helpful connection diagnostics.',
    transcripts: 'Inspect raw call transcripts, outcomes, and config snapshots.',
    analytics: 'Outcome aggregation, quality alerts, and call performance trends.',
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
  const apiBase = import.meta.env.VITE_API_BASE || 'the Vite /api proxy';
  if (raw.toLowerCase().includes('failed to fetch')) {
    return `Cannot reach the FastAPI backend at ${apiBase}. Start the local API on port 8010 or open the SSH tunnel, then refresh and retry.`;
  }
  if (raw.toLowerCase().includes('network timeout')) {
    return `${raw}. The backend is reachable but too slow; check Mongo/LiveKit connectivity and backend logs before retrying.`;
  }
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

function deriveAgentState(status: string, remoteAudioReady: boolean, hasRoom: boolean): AgentUiState {
  const value = status.toLowerCase();
  if (!hasRoom || value === 'idle' || value.includes('disconnected')) return 'idle';
  if (value.includes('creating') || value.includes('connecting') || value.includes('requesting') || value.includes('reconnecting')) return 'connecting';
  if (value.includes('speaking') || value.includes('bot audio connected') || remoteAudioReady) return 'speaking';
  if (value.includes('thinking') || value.includes('waiting for bot') || value.includes('participant joined')) return 'thinking';
  return 'listening';
}

function shortId(value: string) {
  if (!value) return '-';
  return value.length > 12 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}

function formatDate(value?: string) {
  if (!value) return '-';
  const date = parseApiDate(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.toLocaleString('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Asia/Kolkata'
  })} IST`;
}

function formatTime(value?: string) {
  if (!value) return '-';
  const date = parseApiDate(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: 'Asia/Kolkata'
  })} IST`;
}

function parseApiDate(value: string) {
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}

const INLINE_EVENT_TYPES = new Set([
  'call_started',
  'first_user_audio_received',
  'first_user_transcript_received',
  'first_agent_response',
  'first_word_spoken',
  'user_interrupted',
  'interruption',
  'transcript_save_succeeded',
  'transcript_save_failed',
  'callback_failed',
  'callback_succeeded',
  'call_ended'
]);

const START_EVENT_TYPES = new Set(['call_started', 'first_user_audio_received', 'first_user_transcript_received']);
const END_EVENT_TYPES = new Set(['transcript_save_succeeded', 'transcript_save_failed', 'callback_failed', 'callback_succeeded', 'call_ended']);

function buildConversationItems(transcript?: Transcript, events: CallEvent[] = []): ConversationItem[] {
  if (!transcript) return [];
  const inlineEvents = events.filter((event) => INLINE_EVENT_TYPES.has(event.event_type));
  const selectedTurns = transcript.verified_transcript_status === 'succeeded' && transcript.verified_transcript?.length
    ? transcript.verified_transcript
    : transcript.transcript || transcript.live_transcript || [];
  const turnItems: ConversationItem[] = selectedTurns.map((turn, index) => ({
    kind: 'turn',
    id: `turn-${index}`,
    role: normalizeTranscriptRole(turn.role),
    text: turn.text || '',
    time: formatTime(turn.created_at),
    sortAt: timestampValue(turn.created_at),
    interrupted: Boolean(turn.interrupted || turn.event_type?.toLowerCase().includes('interrupt'))
  }));
  const eventItems = inlineEvents.map((event) => eventToConversationItem(event));
  const hasTurnTimes = selectedTurns.some((turn) => Boolean(turn.created_at));
  if (hasTurnTimes) {
    return [...eventItems, ...turnItems].sort((a, b) => a.sortAt - b.sortAt);
  }
  return [
    ...eventItems.filter((event) => event.kind === 'event' && START_EVENT_TYPES.has(event.title)),
    ...turnItems,
    ...eventItems.filter((event) => event.kind === 'event' && !START_EVENT_TYPES.has(event.title) && !END_EVENT_TYPES.has(event.title)),
    ...eventItems.filter((event) => event.kind === 'event' && END_EVENT_TYPES.has(event.title))
  ];
}

function eventToConversationItem(event: CallEvent): ConversationItem {
  return {
    kind: 'event',
    id: event._id,
    title: event.event_type,
    text: event.message || titleCase(event.event_type.replace(/_/g, ' ')),
    time: formatTime(event.created_at),
    sortAt: timestampValue(event.created_at),
    severity: event.severity || 'info'
  };
}

function timestampValue(value?: string) {
  if (!value) return Number.MAX_SAFE_INTEGER;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.MAX_SAFE_INTEGER : parsed;
}

function normalizeTranscriptRole(role: string) {
  const value = (role || '').toLowerCase();
  if (['assistant', 'agent', 'bot', 'model'].includes(value)) return 'assistant';
  if (['user', 'customer', 'caller', 'human'].includes(value)) return 'user';
  if (['recording', 'verified_recording'].includes(value)) return 'recording';
  return value || 'system';
}

function transcriptSourceLabel(transcript: Transcript) {
  if (transcript.verified_transcript_status === 'succeeded') return 'Verified recording';
  if (transcript.verified_transcript_status === 'pending') return 'Live transcript, verification pending';
  if (transcript.verified_transcript_status === 'failed') return 'Live transcript, verification failed';
  if (transcript.verified_transcript_status === 'unavailable') return 'Live transcript, recording unavailable';
  return titleCase(transcript.analysis_transcript_source || transcript.transcript_source || 'gemini live');
}

function maxResponseDelayLabel(transcript: Transcript) {
  const metrics = transcript.latency_metrics || {};
  const delay = Number(metrics.max_response_delay_ms || metrics.first_response_delay_ms || 0);
  if (!delay) return '-';
  return delay >= 8000 ? `${delay}ms high` : delay >= 3000 ? `${delay}ms slow` : `${delay}ms`;
}

function titleCase(value: string) {
  return value
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
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
