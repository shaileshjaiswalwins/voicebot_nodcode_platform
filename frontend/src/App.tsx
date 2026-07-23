import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BrowserRouter, useLocation, useNavigate } from 'react-router-dom';
import {
  Activity, BarChart2, BookOpen, Bot as BotIcon, ClipboardList, FileText, Gauge, GitBranch,
  IndianRupee, Keyboard, Link2, Megaphone, Menu, Phone, PhoneCall, Settings as SettingsIcon, X
} from 'lucide-react';

import { api, ApiError, getToken, setToken, API_BASE } from './api';
import type {
  AnalysisPromptEntry, AnalysisPromptKey, Bot, BotVersion, Campaign, CallEvent, EvalRun, LangfuseSettings, LanguageOption,
  LanguageSettings, LibraryPhrase, NumberMapping, OutcomeEntry, PhoneNumber, PhoneNumberEnvironment,
  PlatformSettings, RuntimeSettings, Transcript, TestRecordingLookup, DialingStrategy, PricingConfig
} from './api';
import type { AgentWorkspaceMode, BuilderMode, CmdKExtra, Diagnostic, RuntimeConfig, TestForm, View } from './types';

import { CMD_VIEWS, SHORTCUT_MAP, defaultConfig } from './constants/ui';
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts';
import { parseConfig } from './utils/config';
import { buildDiagnostic, friendlyApiError, friendlyTestError } from './utils/errors';
import { buildPath, parsePath } from './utils/routes';

import { SidebarNav } from './components/SidebarNav';
import { NavButton } from './components/NavButton';
import { CommandPalette } from './components/CommandPalette';
import { DiagnosticsBar } from './components/DiagnosticsBar';
import { ToastContainer } from './components/Toast';
import { useToasts } from './hooks/useToasts';
import { ShortcutsModal } from './components/ShortcutsModal';
import { ResilientPanel } from './components/ResilientPanel';
import { PageSummary } from './components/PageSummary';
import { PublishConfirmModal } from './components/PublishConfirmModal';
import { EvalsPanel } from './components/EvalsPanel';
import { VersionDiffModal } from './components/VersionDiffModal';

import { BotsView, NewAgentWizard, DeleteAgentDialog } from './views/BotsView';
import { BuilderView } from './views/BuilderView';
import { FlowBuilderView } from './views/FlowBuilderView';
import { CampaignsView, buildDefaultStrategy } from './views/CampaignsView';
import { PhoneNumbersView } from './views/PhoneNumbersView';
import { NumberMappingView } from './views/NumberMappingView';
import { LibraryView } from './views/LibraryView';
import { TranscriptsView } from './views/TranscriptsView';
import { AnalyticsView } from './views/AnalyticsView';
import { ObservabilityView } from './views/ObservabilityView';
import { TestCallPanel, titleFor, subtitleFor } from './views/TestCallPanel';
import { SettingsView } from './views/SettingsView';
import { AuditLogView } from './views/AuditLogView';
import { AdminView } from './views/AdminView';

// Real WebRTC test-call connection is owned by <LiveKitRoom> inside
// components/LiveKitTestSession.tsx, not managed manually here.

type AsyncState = 'idle' | 'running' | 'failed';

const NAV_ICONS: Record<View, React.ReactNode> = {
  bots: <BotIcon size={17} />,
  builder: <BotIcon size={17} />,
  flow: <GitBranch size={17} />,
  campaigns: <Megaphone size={17} />,
  phone_numbers: <Phone size={17} />,
  number_mapping: <Link2 size={17} />,
  test: <PhoneCall size={17} />,
  transcripts: <FileText size={17} />,
  analytics: <BarChart2 size={17} />,
  observability: <Gauge size={17} />,
  library: <BookOpen size={17} />,
  settings: <SettingsIcon size={17} />,
  audit_log: <ClipboardList size={17} />,
  admin: <IndianRupee size={17} />,
};

const EMPTY_TEST_FORM: TestForm = {
  campaign_id: '',
  lead_id: '',
  call_id: `TEST-${Date.now()}`,
  mobile: '',
  srchterm: '',
  buyer_name: '',
  city: '',
  test_worker_agent_name: '',
  test_bot_version_id: '',
  custom_lead_json: '',
};

// Hindi only — the runtime (bot_dev_param.py) hardcodes HINDI_LANG_CONFIG and Sarvam
// hi-IN STT/TTS, so any other option here would save to Mongo and then be ignored on the
// call. Add a language back only once the runtime can actually speak it.
const DEFAULT_LANGUAGES: LanguageOption[] = [
  { id: 'hindi', label: 'Hindi' },
];

// ---------------------------------------------------------------------------
// Login gate
// ---------------------------------------------------------------------------

function LoginForm({ onLoggedIn, ssoError }: { onLoggedIn: () => void; ssoError?: string }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(ssoError || '');

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api.login(email.trim(), password);
      onLoggedIn();
    } catch (err) {
      setError(friendlyApiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
      <form onSubmit={submit} className="panel" style={{ maxWidth: 360, width: '100%', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div>
          <h2>JustDial Voice AI Platform</h2>
          <p>Sign in to manage voice agents, campaigns and transcripts.</p>
        </div>
        {error && <div className="notice error">{error}</div>}
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        <button className="primary" type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <div className="sso-divider"><span>or</span></div>
        <a className="sso-button" href={`${API_BASE}/api/auth/sso/login`}>
          Sign in with Justdial SSO
        </a>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}

function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const lastSyncedPathRef = useRef<string>('');

  const [authed, setAuthed] = useState<boolean>(Boolean(getToken()));
  const [ssoError, setSsoError] = useState<string>('');

  // Consume the token (or error) the backend's /api/auth/sso/callback redirected back with
  // (see backend/routers/auth.py sso_callback) and strip it from the URL immediately so it
  // never lingers in browser history/referrer headers.
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const ssoToken = params.get('sso_token');
    const err = params.get('sso_error');
    if (ssoToken) {
      setToken(ssoToken);
      setAuthed(true);
    }
    if (ssoToken || err) {
      if (err) setSsoError(err);
      params.delete('sso_token');
      params.delete('sso_error');
      navigate({ pathname: location.pathname, search: params.toString() }, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Navigation ──────────────────────────────────────────────────────
  const [view, setView] = useState<View>('bots');
  const [showCmdK, setShowCmdK] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // ── Diagnostics ─────────────────────────────────────────────────────
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([]);
  function pushDiagnostic(scope: string, error: unknown, action?: string, severity: Diagnostic['severity'] = 'error') {
    setDiagnostics((prev) => [buildDiagnostic(scope, error, action, severity), ...prev].slice(0, 20));
  }

  const { toasts, showToast, dismissToast } = useToasts();

  // ── Core data ───────────────────────────────────────────────────────
  const [bots, setBots] = useState<Bot[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [phoneNumbers, setPhoneNumbers] = useState<PhoneNumber[]>([]);
  const [numberMappings, setNumberMappings] = useState<NumberMapping[]>([]);
  const [transcripts, setTranscripts] = useState<Transcript[]>([]);
  const [platformSettings, setPlatformSettings] = useState<PlatformSettings | null>(null);
  const [runtimeSettings, setRuntimeSettings] = useState<RuntimeSettings | null>(null);
  const [langfuseSettings, setLangfuseSettings] = useState<LangfuseSettings | null>(null);
  const [pricingConfig, setPricingConfig] = useState<PricingConfig | null>(null);
  const [phrases, setPhrases] = useState<LibraryPhrase[]>([]);
  const [outcomes, setOutcomes] = useState<OutcomeEntry[]>([]);
  const [languageSettings, setLanguageSettings] = useState<LanguageSettings[]>([]);
  const [analysisPrompts, setAnalysisPrompts] = useState<AnalysisPromptEntry[]>([]);

  const [loadingBots, setLoadingBots] = useState(false);
  const [loadingCampaigns, setLoadingCampaigns] = useState(false);
  const [loadingTranscripts, setLoadingTranscripts] = useState(false);
  const [loadingPhoneNumbers, setLoadingPhoneNumbers] = useState(false);
  const [loadingNumberMappings, setLoadingNumberMappings] = useState(false);
  const [mapAgentState, setMapAgentState] = useState<Record<string, AsyncState>>({});

  const languages = DEFAULT_LANGUAGES;

  // ── Bots / builder state ────────────────────────────────────────────
  const [selectedBotId, setSelectedBotId] = useState<string>('');
  const [workspaceMode, setWorkspaceMode] = useState<AgentWorkspaceMode>('list');
  const [builderTab, setBuilderTab] = useState<'prompt' | 'flow' | 'evals'>('prompt');
  const [versions, setVersions] = useState<BotVersion[]>([]);
  const [editingVersionId, setEditingVersionId] = useState<string>('');
  const [configText, setConfigText] = useState<string>(JSON.stringify(defaultConfig, null, 2));
  const [builderMode, setBuilderMode] = useState<BuilderMode>(
    (localStorage.getItem('builderMode') as BuilderMode) || 'pm'
  );
  const [saveState, setSaveState] = useState<AsyncState>('idle');
  const [publishState, setPublishState] = useState<AsyncState>('idle');
  const [rollbackState, setRollbackState] = useState<Record<string, AsyncState>>({});
  const [updateVersionState, setUpdateVersionState] = useState<AsyncState>('idle');
  const [unpublishState, setUnpublishState] = useState<AsyncState>('idle');
  const [renameState, setRenameState] = useState<AsyncState>('idle');
  const [evalRuns, setEvalRuns] = useState<EvalRun[]>([]);
  const [evalsRunning, setEvalsRunning] = useState(false);
  const [showPublishConfirm, setShowPublishConfirm] = useState(false);
  const [diffPair, setDiffPair] = useState<{ a: BotVersion; b: BotVersion } | null>(null);

  const [showNewAgent, setShowNewAgent] = useState(false);
  const [newAgentBusy, setNewAgentBusy] = useState(false);
  const [newAgentForm, setNewAgentForm] = useState<{
    name: string; description: string; agent_name: string; organization_name: string;
    persona_gender: 'female' | 'male'; language: string; initial_message: string;
  }>({
    name: '', description: '', agent_name: '', organization_name: '',
    persona_gender: 'female', language: 'hindi', initial_message: defaultConfig.initial_message || ''
  });
  const [deleteTarget, setDeleteTarget] = useState<Bot | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const selectedBot = bots.find((b) => b._id === selectedBotId);

  // ── Campaigns ───────────────────────────────────────────────────────
  const [selectedCampaignKey, setSelectedCampaignKey] = useState<string>('');
  const [campaignWorkspaceMode, setCampaignWorkspaceMode] = useState<'list' | 'strategy' | 'leads'>('list');
  const [campaignSaveState, setCampaignSaveState] = useState<AsyncState>('idle');
  const [assignBotState, setAssignBotState] = useState<Record<string, AsyncState>>({});
  const [campaignCreateState, setCampaignCreateState] = useState<AsyncState>('idle');

  // ── Phone numbers ───────────────────────────────────────────────────
  const [createPhoneNumberState, setCreatePhoneNumberState] = useState<AsyncState>('idle');
  const [updatePhoneNumberState, setUpdatePhoneNumberState] = useState<AsyncState>('idle');
  const [reassignPhoneNumberState, setReassignPhoneNumberState] = useState<Record<string, AsyncState>>({});

  // ── Transcripts ─────────────────────────────────────────────────────
  const [selectedTranscriptId, setSelectedTranscriptId] = useState<string>('');
  const [transcriptSearchText, setTranscriptSearchText] = useState('');
  const [transcriptFilters, setTranscriptFilters] = useState({
    status: '', outcome: '', campaign_id: '', bot_id: '', start_date: '', end_date: ''
  });
  const [callEvents, setCallEvents] = useState<CallEvent[]>([]);
  const [localRecordingByRoom, setLocalRecordingByRoom] = useState<Record<string, TestRecordingLookup>>({});

  // ── URL routing sync ────────────────────────────────────────────────
  // Single effect handles both directions to avoid ordering races between separate effects:
  // on first login/refresh the URL wins (may be a deep link like /settings); after that,
  // external URL changes (back/forward) update state, and state changes push new URLs.
  const initializedFromUrlRef = useRef(false);

  useEffect(() => {
    if (!authed) return;

    const statePath =
      view === 'bots' || view === 'builder' ? buildPath({ view: 'bots', botId: workspaceMode === 'builder' ? selectedBotId : '' })
      : view === 'campaigns' ? buildPath({ view: 'campaigns', campaignKey: campaignWorkspaceMode === 'strategy' ? selectedCampaignKey : '' })
      : view === 'transcripts' ? buildPath({ view: 'transcripts', transcriptId: selectedTranscriptId })
      : buildPath({ view } as Parameters<typeof buildPath>[0]);

    function applyRouteToState(pathname: string) {
      const route = parsePath(pathname);
      setView(route.view);
      if (route.view === 'bots') {
        setSelectedBotId(route.botId);
        setWorkspaceMode(route.botId ? 'builder' : 'list');
        if (route.botId) loadBotVersions(route.botId);
      } else if (route.view === 'campaigns') {
        setSelectedCampaignKey(route.campaignKey);
        setCampaignWorkspaceMode(route.campaignKey ? 'strategy' : 'list');
      } else if (route.view === 'transcripts') {
        setSelectedTranscriptId(route.transcriptId);
      }
    }

    if (!initializedFromUrlRef.current) {
      initializedFromUrlRef.current = true;
      const normalizedPath = buildPath(parsePath(location.pathname));
      applyRouteToState(location.pathname);
      if (normalizedPath !== location.pathname) {
        lastSyncedPathRef.current = normalizedPath;
        navigate(normalizedPath, { replace: true });
      } else {
        lastSyncedPathRef.current = location.pathname;
      }
      return;
    }

    if (location.pathname !== lastSyncedPathRef.current) {
      // The URL changed externally (browser back/forward) — adopt it into state.
      applyRouteToState(location.pathname);
      lastSyncedPathRef.current = location.pathname;
      return;
    }

    if (statePath !== location.pathname) {
      lastSyncedPathRef.current = statePath;
      navigate(statePath);
    }
    document.title = `${titleFor(view === 'builder' ? 'bots' : view)} — Voice AI Platform`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed, view, selectedBotId, workspaceMode, selectedCampaignKey, campaignWorkspaceMode, selectedTranscriptId, location.pathname, navigate]);

  // ── Test call (LiveKit) ─────────────────────────────────────────────
  const [testBotId, setTestBotId] = useState<string>('');
  const [testForm, setTestForm] = useState<TestForm>(EMPTY_TEST_FORM);
  const [testStatus, setTestStatus] = useState('Idle');
  const [testError, setTestError] = useState('');
  const [testCloseNote, setTestCloseNote] = useState('');
  const [chatMessage, setChatMessage] = useState('');
  const [micEnabled, setMicEnabled] = useState(false);
  const [roomName, setRoomName] = useState('');
  const [remoteAudioReady, setRemoteAudioReady] = useState(false);
  const [testLivekitUrl, setTestLivekitUrl] = useState('');
  const [testLivekitToken, setTestLivekitToken] = useState('');

  const testBot = bots.find((b) => b._id === testBotId);

  // testBotId starts empty and nothing else ever picks a default — without this, the
  // <select> in TestCallPanel visually shows the first bot (a bare <select> with no
  // placeholder option defaults to displaying its first <option>) while testBotId/testBot
  // stay '' /undefined underneath, so loadVersions() silently no-ops and the "Version to
  // test" dropdown is stuck on "Loading…" forever with no error ever surfacing.
  useEffect(() => {
    if (!testBotId && bots.length > 0) {
      setTestBotId(bots[0]._id);
    }
  }, [bots, testBotId]);

  // ── Data loading ─────────────────────────────────────────────────────
  const loadBots = useCallback(async () => {
    setLoadingBots(true);
    try {
      const data = await api.bots();
      setBots(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) { setAuthed(false); return; }
      pushDiagnostic('Agents', err, 'Retry loading agents', 'error');
    } finally {
      setLoadingBots(false);
    }
  }, []);

  const loadCampaigns = useCallback(async () => {
    setLoadingCampaigns(true);
    try {
      setCampaigns(await api.campaigns());
    } catch (err) {
      pushDiagnostic('Campaigns', err, 'Retry loading campaigns', 'warning');
    } finally {
      setLoadingCampaigns(false);
    }
  }, []);

  const loadPhoneNumbers = useCallback(async () => {
    setLoadingPhoneNumbers(true);
    try {
      setPhoneNumbers(await api.phoneNumbers());
    } catch (err) {
      pushDiagnostic('Phone Numbers', err, 'Retry loading phone numbers', 'warning');
    } finally {
      setLoadingPhoneNumbers(false);
    }
  }, []);

  const loadNumberMappings = useCallback(async () => {
    setLoadingNumberMappings(true);
    try {
      setNumberMappings(await api.numberMapping());
    } catch (err) {
      pushDiagnostic('Number Mapping', err, 'Retry loading number mapping', 'warning');
    } finally {
      setLoadingNumberMappings(false);
    }
  }, []);

  const loadTranscripts = useCallback(async () => {
    setLoadingTranscripts(true);
    try {
      setTranscripts(await api.transcripts({ text: transcriptSearchText || undefined, limit: 200 }));
    } catch (err) {
      pushDiagnostic('Transcripts', err, 'Retry loading transcripts', 'warning');
    } finally {
      setLoadingTranscripts(false);
    }
  }, [transcriptSearchText]);

  const loadSettings = useCallback(async () => {
    try {
      const [platform, runtime, langfuse] = await Promise.all([
        api.platformSettings(), api.runtimeSettings(), api.langfuseSettings()
      ]);
      setPlatformSettings(platform);
      setRuntimeSettings(runtime);
      setLangfuseSettings(langfuse);
    } catch (err) {
      pushDiagnostic('Settings', err, 'Retry loading settings', 'warning');
    }
  }, []);

  const loadPricingConfig = useCallback(async () => {
    try {
      setPricingConfig(await api.getPricingAdminConfig());
    } catch (err) {
      pushDiagnostic('Pricing config', err, 'Retry loading pricing', 'warning');
    }
  }, []);

  const loadLibrary = useCallback(async () => {
    try {
      const [p, o, l, a] = await Promise.all([
        api.phrases(), api.outcomes(), api.languageSettings(), api.analysisPrompts()
      ]);
      setPhrases(p); setOutcomes(o); setLanguageSettings(l); setAnalysisPrompts(a);
    } catch (err) {
      pushDiagnostic('Library', err, 'Retry loading library', 'warning');
    }
  }, []);

  async function handleUpdateAnalysisPrompt(key: AnalysisPromptKey, promptTemplate: string) {
    const updated = await api.updateAnalysisPrompt(key, promptTemplate);
    setAnalysisPrompts((prev) => {
      const next = prev.filter((p) => p.key !== key);
      next.push(updated);
      return next;
    });
  }

  // Initial load once authenticated
  useEffect(() => {
    if (!authed) return;
    loadBots();
    loadCampaigns();
    loadPhoneNumbers();
    loadNumberMappings();
    loadTranscripts();
    loadSettings();
    loadLibrary();
    loadPricingConfig();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed]);

  // Reload transcripts on search change (debounced)
  useEffect(() => {
    if (!authed) return;
    const id = window.setTimeout(() => { loadTranscripts(); }, 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transcriptSearchText, authed]);

  // Load bot versions when a bot is selected / edited
  const loadBotVersions = useCallback(async (botId: string) => {
    try {
      const bundle = await api.bot(botId);
      setVersions(bundle.versions);
      const active = bundle.versions.find((v) => v._id === bundle.bot.active_version_id);
      const draft = bundle.versions.find((v) => v.state === 'draft');
      const initial = draft || active || bundle.versions[0];
      if (initial) {
        setEditingVersionId(initial._id);
        setConfigText(JSON.stringify(initial.config, null, 2));
      } else {
        setEditingVersionId('');
        setConfigText(JSON.stringify(defaultConfig, null, 2));
      }
    } catch (err) {
      pushDiagnostic('Bot versions', err, 'Retry loading versions', 'error');
    }
    try {
      setEvalRuns(await api.listEvals(botId));
    } catch {
      setEvalRuns([]);
    }
  }, []);

  async function handleRunEvals(scenarios?: import('./api').EvalScenario[]) {
    if (!selectedBot) return;
    setEvalsRunning(true);
    try {
      const run = await api.runEvals(selectedBot._id, editingVersionId || undefined, scenarios);
      setEvalRuns((prev) => [run, ...prev]);
    } catch (err) {
      pushDiagnostic('Run evals', err, 'Retry evals', 'error');
    } finally {
      setEvalsRunning(false);
    }
  }

  // Load call events when a transcript is selected
  useEffect(() => {
    if (!selectedTranscriptId) { setCallEvents([]); return; }
    api.callEvents(selectedTranscriptId).then(setCallEvents).catch((err) => {
      setCallEvents([]);
      pushDiagnostic('Call events', err, undefined, 'warning');
    });
  }, [selectedTranscriptId]);

  const selectedTranscript = transcripts.find((t) => t._id === selectedTranscriptId);

  // ── Command palette navigation ───────────────────────────────────────
  function navigateFromCmdK(target: View, extra?: CmdKExtra) {
    setShowCmdK(false);
    setView(target);
    if (extra?.botId) { setSelectedBotId(extra.botId); setWorkspaceMode('list'); }
    if (extra?.transcriptId) setSelectedTranscriptId(extra.transcriptId);
    if (extra?.campaignKey) { setSelectedCampaignKey(extra.campaignKey); setCampaignWorkspaceMode('list'); }
  }

  // Cmd+K listener
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setShowCmdK((v) => !v);
      }
    }
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  useKeyboardShortcuts(
    (v) => setView(v),
    () => setShowShortcuts((v) => !v),
    () => { setShowShortcuts(false); setShowCmdK(false); }
  );

  // ── Bots: callbacks ───────────────────────────────────────────────────
  function handleSelectBot(botId: string) {
    setSelectedBotId(botId);
  }

  function handleEditBot(botId: string) {
    setSelectedBotId(botId);
    setWorkspaceMode('builder');
    setBuilderTab('prompt');
    loadBotVersions(botId);
  }

  function handleBackFromBuilder() {
    setWorkspaceMode('list');
  }

  function handleNewAgent() {
    setNewAgentForm({
      name: '', description: '', agent_name: '', organization_name: '',
      persona_gender: 'female', language: 'hindi', initial_message: defaultConfig.initial_message || ''
    });
    setShowNewAgent(true);
  }

  async function confirmNewAgent() {
    setNewAgentBusy(true);
    try {
      const config: RuntimeConfig = {
        ...defaultConfig,
        agent_name: newAgentForm.agent_name,
        organization_name: newAgentForm.organization_name,
        persona_gender: newAgentForm.persona_gender,
        language: newAgentForm.language,
        initial_message: newAgentForm.initial_message,
      };
      const bot = await api.createBot({ name: newAgentForm.name, description: newAgentForm.description, config });
      setShowNewAgent(false);
      await loadBots();
      setSelectedBotId(bot._id);
    } catch (err) {
      pushDiagnostic('New agent', err, 'Retry creating agent', 'error');
    } finally {
      setNewAgentBusy(false);
    }
  }

  function handleDeleteBotRequest(bot: Bot) {
    setDeleteTarget(bot);
  }

  async function confirmDeleteBot() {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    try {
      await api.deleteBot(deleteTarget._id);
      setDeleteTarget(null);
      if (selectedBotId === deleteTarget._id) { setSelectedBotId(''); setWorkspaceMode('list'); }
      await loadBots();
      showToast('Agent deleted');
    } catch (err) {
      pushDiagnostic('Delete agent', err, 'Retry delete', 'error');
    } finally {
      setDeleteBusy(false);
    }
  }

  // ── Builder: callbacks ────────────────────────────────────────────────
  const parsedConfig = parseConfig(configText);

  function updateConfig(key: keyof RuntimeConfig, value: unknown) {
    // Functional update so multiple updateConfig calls in the same tick compose instead of
    // clobbering each other. The Functions tab, for instance, sets `functions` and
    // `function_calling` back-to-back; deriving each from the pre-update config (parsedConfig)
    // made the second overwrite the first, silently dropping the functions edit.
    setConfigText((prev) => {
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(prev);
      } catch {
        return prev;
      }
      return JSON.stringify({ ...parsed, [key]: value }, null, 2);
    });
  }

  function updateLanguage(value: string) {
    updateConfig('language', value);
  }

  async function handleSaveDraft() {
    if (!selectedBot || !parsedConfig.ok) return;
    setSaveState('running');
    try {
      const result = await api.saveDraft(selectedBot._id, parsedConfig.value);
      await loadBots();
      await loadBotVersions(selectedBot._id);
      setEditingVersionId(result.draft_version_id);
      setSaveState('idle');
      showToast('Draft saved');
    } catch (err) {
      setSaveState('failed');
      pushDiagnostic('Save draft', err, 'Retry save', 'error');
    }
  }

  async function handlePublish() {
    setShowPublishConfirm(true);
  }

  async function confirmPublish() {
    if (!selectedBot) return;
    setPublishState('running');
    try {
      await api.publish(selectedBot._id);
      await loadBots();
      await loadBotVersions(selectedBot._id);
      setPublishState('idle');
      setShowPublishConfirm(false);
      showToast('Agent published');
    } catch (err) {
      setPublishState('failed');
      pushDiagnostic('Publish', err, 'Retry publish', 'error');
    }
  }

  async function handleRollback(versionId: string) {
    if (!selectedBot) return;
    const key = `rollback-${versionId}`;
    setRollbackState((prev) => ({ ...prev, [key]: 'running' }));
    try {
      const result = await api.rollback(selectedBot._id, versionId);
      await loadBots();
      await loadBotVersions(selectedBot._id);
      setEditingVersionId(result.draft_version_id);
      setRollbackState((prev) => ({ ...prev, [key]: 'idle' }));
      showToast('Rolled back to a new draft');
    } catch (err) {
      setRollbackState((prev) => ({ ...prev, [key]: 'failed' }));
      pushDiagnostic('Rollback', err, 'Retry rollback', 'error');
    }
  }

  function handleSelectVersion(v: BotVersion) {
    setEditingVersionId(v._id);
    setConfigText(JSON.stringify(v.config, null, 2));
  }

  function handleShowDiff(versionIdA: string, versionIdB: string) {
    const a = versions.find((v) => v._id === versionIdA);
    const b = versions.find((v) => v._id === versionIdB);
    if (a && b) setDiffPair({ a, b });
  }

  function handleToggleBuilderMode(mode: BuilderMode) {
    setBuilderMode(mode);
    localStorage.setItem('builderMode', mode);
  }

  async function handleUpdateVersion(versionId: string) {
    if (!selectedBot || !parsedConfig.ok) return;
    setUpdateVersionState('running');
    try {
      await api.updateVersion(selectedBot._id, versionId, parsedConfig.value);
      await loadBotVersions(selectedBot._id);
      setUpdateVersionState('idle');
      showToast('Version updated');
    } catch (err) {
      setUpdateVersionState('failed');
      pushDiagnostic('Update version', err, 'Retry update', 'error');
    }
  }

  async function handleUnpublish() {
    if (!selectedBot) return;
    setUnpublishState('running');
    try {
      const result = await api.unpublish(selectedBot._id);
      await loadBots();
      await loadBotVersions(selectedBot._id);
      setEditingVersionId(result.draft_version_id);
      setUnpublishState('idle');
      showToast('Moved back to draft');
    } catch (err) {
      setUnpublishState('failed');
      pushDiagnostic('Unpublish', err, 'Retry unpublish', 'error');
    }
  }

  async function handleSaveFlow() {
    if (!selectedBot || !parsedConfig.ok) return;
    const editingVer = versions.find((v) => v._id === editingVersionId);
    if (editingVer && editingVer.state === 'draft') {
      await handleUpdateVersion(editingVersionId);
    } else {
      await handleSaveDraft();
    }
  }

  async function handleRename(name: string, description: string) {
    if (!selectedBot) return;
    setRenameState('running');
    try {
      await api.renameBot(selectedBot._id, name, description);
      await loadBots();
      setRenameState('idle');
      showToast('Agent renamed');
    } catch (err) {
      setRenameState('failed');
      pushDiagnostic('Rename', err, 'Retry rename', 'error');
    }
  }

  const activeVersion = versions.find((v) => v._id === selectedBot?.active_version_id);
  const latestDraft = versions.find((v) => v.state === 'draft');
  const publishedCount = versions.filter((v) => v.state === 'published').length;

  // ── Campaigns: callbacks ──────────────────────────────────────────────
  function handleSelectCampaign(key: string) {
    setSelectedCampaignKey(key);
    setCampaignWorkspaceMode('strategy');
  }

  function handleSelectCampaignLeads(key: string) {
    setSelectedCampaignKey(key);
    setCampaignWorkspaceMode('leads');
  }

  function handleBackToCampaignList() {
    setCampaignWorkspaceMode('list');
  }

  async function handleSaveStrategy(key: string, name: string, strategy: DialingStrategy) {
    setCampaignSaveState('running');
    try {
      await api.saveCampaignStrategy(key, name, strategy);
      await loadCampaigns();
      setCampaignSaveState('idle');
      showToast('Dialing strategy saved');
    } catch (err) {
      setCampaignSaveState('failed');
      pushDiagnostic('Save strategy', err, 'Retry save', 'error');
    }
  }

  async function handleCreateCampaign(campaignKey: string, name: string, botId?: string) {
    setCampaignCreateState('running');
    try {
      await api.saveCampaignStrategy(campaignKey, name, buildDefaultStrategy());
      if (botId) await api.assignCampaignBot(campaignKey, botId);
      await loadCampaigns();
      setCampaignCreateState('idle');
      showToast('Campaign created');
    } catch (err) {
      setCampaignCreateState('failed');
      pushDiagnostic('Create campaign', err, 'Retry create', 'error');
    }
  }

  async function handleAssignBot(campaignKey: string, botId: string) {
    setAssignBotState((prev) => ({ ...prev, [campaignKey]: 'running' }));
    try {
      await api.assignCampaignBot(campaignKey, botId);
      await loadCampaigns();
      setAssignBotState((prev) => ({ ...prev, [campaignKey]: 'idle' }));
      showToast(botId ? 'Bot assigned to campaign' : 'Bot unassigned from campaign');
    } catch (err) {
      setAssignBotState((prev) => ({ ...prev, [campaignKey]: 'failed' }));
      pushDiagnostic('Assign bot', err, 'Retry assignment', 'error');
    }
  }

  async function handleSetCampaignStatus(campaignKey: string, status: string) {
    try {
      // Activating must enqueue call_jobs for any pending leads, not just flip the status
      // label — startCampaign does both (idempotent), so Activate here matches what
      // CampaignLeadsPanel's own Start/Resume button does.
      if (status === 'active') {
        await api.startCampaign(campaignKey);
      } else {
        await api.setCampaignStatus(campaignKey, status);
      }
      await loadCampaigns();
      showToast(status === 'active' ? 'Campaign activated' : 'Campaign paused');
    } catch (err) {
      pushDiagnostic('Campaign status', err, 'Retry status change', 'error');
    }
  }

  async function handleDeleteCampaign(campaignKey: string) {
    try {
      await api.deleteCampaign(campaignKey);
      await loadCampaigns();
      showToast('Campaign deleted');
    } catch (err) {
      pushDiagnostic('Delete campaign', err, 'Retry delete', 'error');
    }
  }

  // ── Phone numbers: callbacks ──────────────────────────────────────────
  async function handleCreatePhoneNumber(payload: {
    number: string;
    environment: PhoneNumberEnvironment;
    service_id?: string;
    aod_ports?: number;
    name?: string;
    ip?: string;
    sip_trunk?: string;
    sip_username?: string;
    sip_password?: string;
  }) {
    setCreatePhoneNumberState('running');
    try {
      await api.createPhoneNumber(payload);
      await loadPhoneNumbers();
      setCreatePhoneNumberState('idle');
      showToast('Phone number added');
    } catch (err) {
      setCreatePhoneNumberState('failed');
      pushDiagnostic('Add phone number', err, 'Retry add', 'error');
    }
  }

  async function handleReassignPhoneNumber(id: string, botId: string) {
    setReassignPhoneNumberState((prev) => ({ ...prev, [id]: 'running' }));
    try {
      await api.reassignPhoneNumber(id, botId);
      await loadPhoneNumbers();
      setReassignPhoneNumberState((prev) => ({ ...prev, [id]: 'idle' }));
      showToast('Phone number reassigned');
    } catch (err) {
      setReassignPhoneNumberState((prev) => ({ ...prev, [id]: 'failed' }));
      pushDiagnostic('Reassign phone number', err, 'Retry reassignment', 'error');
    }
  }

  async function handleUpdatePhoneNumber(id: string, payload: {
    status?: string;
    service_id?: string;
    aod_ports?: number;
    name?: string;
    ip?: string;
    sip_trunk?: string;
    sip_username?: string;
    sip_password?: string;
  }) {
    setUpdatePhoneNumberState('running');
    try {
      await api.updatePhoneNumber(id, payload);
      await loadPhoneNumbers();
      setUpdatePhoneNumberState('idle');
      showToast('Phone number updated');
    } catch (err) {
      setUpdatePhoneNumberState('failed');
      pushDiagnostic('Update phone number', err, 'Retry save', 'error');
    }
  }

  async function handleDeletePhoneNumber(id: string) {
    try {
      await api.deletePhoneNumber(id);
      await loadPhoneNumbers();
      showToast('Phone number deleted');
    } catch (err) {
      pushDiagnostic('Delete phone number', err, 'Retry delete', 'error');
    }
  }

  async function handleMapNumberToAgent(botId: string, phoneNumber: string | null) {
    setMapAgentState((s) => ({ ...s, [botId]: 'running' }));
    try {
      await api.mapNumberToAgent(botId, phoneNumber);
      await loadNumberMappings();
      setMapAgentState((s) => ({ ...s, [botId]: 'idle' }));
      showToast(phoneNumber ? 'Number mapped to agent' : 'Number cleared');
    } catch (err) {
      setMapAgentState((s) => ({ ...s, [botId]: 'failed' }));
      pushDiagnostic('Map number to agent', err, 'Retry mapping', 'error');
    }
  }

  // ── Library: callbacks ────────────────────────────────────────────────
  async function handleCreatePhrase(payload: Partial<LibraryPhrase>) {
    try {
      await api.createPhrase(payload);
      await loadLibrary();
      showToast('Phrase added');
    } catch (err) {
      pushDiagnostic('Library phrase', err, 'Retry create', 'error');
    }
  }

  async function handleUpdatePhrase(id: string, payload: Partial<LibraryPhrase>) {
    try {
      await api.updatePhrase(id, payload);
      await loadLibrary();
      showToast('Phrase updated');
    } catch (err) {
      pushDiagnostic('Library phrase', err, 'Retry update', 'error');
    }
  }

  async function handleDeletePhrase(id: string) {
    try {
      await api.deletePhrase(id);
      await loadLibrary();
      showToast('Phrase deleted');
    } catch (err) {
      pushDiagnostic('Library phrase', err, 'Retry delete', 'error');
    }
  }

  async function handleUpdateOutcome(key: string, payload: Partial<OutcomeEntry>) {
    try {
      await api.updateOutcome(key, payload);
      await loadLibrary();
      showToast('Outcome updated');
    } catch (err) {
      pushDiagnostic('Outcome catalog', err, 'Retry update', 'error');
    }
  }

  async function handleUpsertLanguageSettings(payload: Partial<LanguageSettings>) {
    try {
      await api.upsertLanguageSettings(payload);
      await loadLibrary();
      showToast('Language settings saved');
    } catch (err) {
      pushDiagnostic('Language settings', err, 'Retry save', 'error');
    }
  }

  async function handleDeleteLanguageSetting(id: string) {
    try {
      await api.deleteLanguageSetting(id);
      await loadLibrary();
      showToast('Language removed');
    } catch (err) {
      pushDiagnostic('Delete language', err, 'Retry delete', 'error');
    }
  }

  // ── Settings: callbacks ────────────────────────────────────────────────
  function handleUpdateRuntime(payload: Partial<RuntimeSettings>) {
    api.updateRuntimeSettings(payload)
      .then((updated) => { setRuntimeSettings(updated); showToast('Runtime settings saved'); })
      .catch((err) => pushDiagnostic('Runtime settings', err, 'Retry save', 'error'));
  }

  async function handleUpdatePricingConfig(payload: PricingConfig): Promise<void> {
    try {
      const updated = await api.updatePricingAdminConfig(payload);
      setPricingConfig(updated);
    } catch (err) {
      pushDiagnostic('Pricing config', err, 'Retry save', 'error');
      throw err;
    }
  }

  async function handleUpdatePlatformSettings(payload: PlatformSettings): Promise<void> {
    try {
      const updated = await api.updatePlatformSettings(payload);
      setPlatformSettings(updated);
    } catch (err) {
      pushDiagnostic('Platform settings', err, 'Retry save', 'error');
      throw err;
    }
  }

  function handleUpdateLangfuse(payload: Partial<LangfuseSettings>) {
    api.updateLangfuseSettings(payload)
      .then((updated) => { setLangfuseSettings(updated); showToast('Observability settings saved'); })
      .catch((err) => pushDiagnostic('Langfuse settings', err, 'Retry save', 'error'));
  }

  // ── Test call: LiveKit wiring ───────────────────────────────────────────
  // The actual room connection (connect, mic publish, track subscribe, disconnect) is
  // owned by <LiveKitRoom> inside components/LiveKitTestSession.tsx — this just requests
  // the room from the backend and reacts to connection lifecycle callbacks from there.
  async function handleStartTestCall() {
    if (!testBot) return;
    setTestError('');
    setTestCloseNote('');
    setTestStatus('Creating room…');
    try {
      const payload = {
        bot_id: testBot._id,
        test_bot_version_id: testForm.test_bot_version_id || undefined,
        campaign_id: testForm.campaign_id || undefined,
        lead_id: testForm.lead_id || undefined,
        call_id: testForm.call_id || undefined,
        mobile: testForm.mobile || undefined,
        srchterm: testForm.srchterm || undefined,
        buyer_name: testForm.buyer_name || undefined,
        city: testForm.city || undefined,
        test_worker_agent_name: testForm.test_worker_agent_name || undefined,
        custom_lead_json: testForm.custom_lead_json || undefined,
      };
      const result = await api.startTestCall(payload);
      setRoomName(result.room_name);
      setTestLivekitUrl(result.livekit_url);
      setTestLivekitToken(result.livekit_token);
      setTestStatus('Connecting to LiveKit…');
    } catch (err) {
      setTestStatus('Failed');
      setTestError(friendlyTestError(err));
    }
  }

  async function handleStopTestCall() {
    const roomToStop = roomName;
    setRoomName('');
    setTestLivekitUrl('');
    setTestLivekitToken('');
    setTestStatus('Idle');
    setRemoteAudioReady(false);
    setMicEnabled(false);
    try {
      if (roomToStop) {
        await api.stopTestCall(roomToStop);
      }
    } catch (err) {
      pushDiagnostic('Stop test call', err, undefined, 'warning');
    } finally {
      setTestCloseNote('Test call ended.');
      loadTranscripts();
    }
  }

  function handleLiveKitConnected() {
    setTestStatus('Waiting for bot to join…');
  }

  function handleLiveKitDisconnected() {
    setTestStatus('Idle');
    setRoomName('');
    setTestLivekitUrl('');
    setTestLivekitToken('');
    setRemoteAudioReady(false);
    setMicEnabled(false);
  }

  function handleLiveKitError(err: Error) {
    setTestStatus('Failed');
    setTestError(friendlyTestError(err));
  }

  // ── Diagnostics bar callbacks ────────────────────────────────────────
  function handleClearDiagnostics(scope?: string) {
    setDiagnostics((prev) => (scope ? prev.filter((d) => d.scope !== scope) : []));
  }
  function handleRetryDiagnostics() {
    loadBots(); loadCampaigns(); loadPhoneNumbers(); loadNumberMappings(); loadTranscripts(); loadSettings(); loadLibrary();
  }
  function handleUseCachedDiagnostics() {
    setDiagnostics([]);
  }

  if (!authed) {
    return <LoginForm onLoggedIn={() => setAuthed(true)} ssoError={ssoError} />;
  }

  return (
    <div className="app-shell">
      <div className="mobile-topbar">
        <button onClick={() => setMobileNavOpen(true)} aria-label="Open navigation">
          <Menu size={18} />
        </button>
        <div className="sidebar-brand" style={{ padding: 0 }}>
          <BotIcon size={18} />
          <span>Voice AI Platform</span>
        </div>
      </div>

      {mobileNavOpen && <div className="sidebar-backdrop" onClick={() => setMobileNavOpen(false)} />}

      <aside className={mobileNavOpen ? 'sidebar open' : 'sidebar'}>
        <div className="sidebar-brand">
          <BotIcon size={20} />
          <span>Voice AI Platform</span>
          <button
            onClick={() => setMobileNavOpen(false)}
            aria-label="Close navigation"
            style={{ marginLeft: 'auto', border: 'none', background: 'none', display: 'none' }}
            className="mobile-nav-close"
          >
            <X size={16} />
          </button>
        </div>
        <SidebarNav>
          {CMD_VIEWS.map((item) => (
            <NavButton
              key={item.view}
              icon={NAV_ICONS[item.view]}
              label={item.label}
              active={view === item.view || (item.view === 'bots' && view === 'builder')}
              onClick={() => {
                setView(item.view);
                if (item.view === 'bots') setWorkspaceMode('list');
                setMobileNavOpen(false);
              }}
            />
          ))}
        </SidebarNav>
        <button className="nav-item shortcuts-btn" onClick={() => setShowShortcuts(true)}>
          <Keyboard size={17} /><span>Shortcuts</span>
        </button>
      </aside>

      <main className="main-content">
        <DiagnosticsBar
          diagnostics={diagnostics}
          onClear={handleClearDiagnostics}
          onRetry={handleRetryDiagnostics}
          onUseCache={handleUseCachedDiagnostics}
        />

        <header className="content-header">
          <div>
            <h1>{titleFor(view)}</h1>
            <p>{subtitleFor(view)}</p>
          </div>
        </header>

        <PageSummary
          view={view}
          bots={bots}
          transcripts={transcripts}
          campaigns={campaigns}
          selectedBot={selectedBot}
          testStatus={testStatus}
          roomName={roomName}
          micEnabled={micEnabled}
          remoteAudioReady={remoteAudioReady}
          loading={loadingBots || loadingCampaigns || loadingTranscripts}
        />

        <ResilientPanel name={titleFor(view) || 'View'} onDiagnostic={(scope, error, action, severity) => pushDiagnostic(scope, error, action, severity)}>
          {view === 'bots' && workspaceMode === 'list' && (
            <BotsView
              bots={bots}
              selectedBot={selectedBot}
              transcripts={transcripts}
              loading={loadingBots}
              onSelect={handleSelectBot}
              onEdit={handleEditBot}
              onDelete={handleDeleteBotRequest}
              onNew={handleNewAgent}
            />
          )}

          {(view === 'builder' || (view === 'bots' && workspaceMode === 'builder')) && (
            <div className="agent-workspace">
              <div className="library-tabs agent-workspace-tabs" role="tablist" aria-label="Agent editor">
                <button
                  type="button"
                  role="tab"
                  aria-selected={builderTab === 'prompt'}
                  className={builderTab === 'prompt' ? 'library-tab active' : 'library-tab'}
                  onClick={() => setBuilderTab('prompt')}
                >
                  <strong>Prompt</strong>
                  <small>Behavior &amp; hyperparameters</small>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={builderTab === 'flow'}
                  className={builderTab === 'flow' ? 'library-tab active' : 'library-tab'}
                  onClick={() => setBuilderTab('flow')}
                >
                  <strong>Flow</strong>
                  <small>Visual conversation graph</small>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={builderTab === 'evals'}
                  className={builderTab === 'evals' ? 'library-tab active' : 'library-tab'}
                  onClick={() => setBuilderTab('evals')}
                >
                  <strong>Evals</strong>
                  <small>Pre-publish simulation</small>
                </button>
              </div>

              {builderTab === 'prompt' && (
                <BuilderView
                  selectedBot={selectedBot}
                  versions={versions}
                  activeVersion={activeVersion}
                  latestDraft={latestDraft}
                  publishedCount={publishedCount}
                  config={parsedConfig}
                  configText={configText}
                  languages={languages}
                  onConfigTextChange={setConfigText}
                  onUpdateConfig={updateConfig}
                  onUpdateLanguage={updateLanguage}
                  onSaveDraft={handleSaveDraft}
                  onPublish={handlePublish}
                  onBack={handleBackFromBuilder}
                  saveState={saveState}
                  publishState={publishState}
                  editingVersionId={editingVersionId}
                  onSelectVersion={handleSelectVersion}
                  onRollback={handleRollback}
                  rollbackState={rollbackState}
                  onShowDiff={handleShowDiff}
                  builderMode={builderMode}
                  onToggleMode={handleToggleBuilderMode}
                  onUpdateVersion={handleUpdateVersion}
                  updateVersionState={updateVersionState}
                  onUnpublish={handleUnpublish}
                  unpublishState={unpublishState}
                  onRename={handleRename}
                  renameState={renameState}
                />
              )}

              {builderTab === 'flow' && (
                <FlowBuilderView
                  selectedBot={selectedBot}
                  editingVersion={versions.find((v) => v._id === editingVersionId)}
                  flow={(parsedConfig.ok ? parsedConfig.value.flow : undefined) || { nodes: [], edges: [] }}
                  onChange={(flow) => updateConfig('flow', flow)}
                  onSave={handleSaveFlow}
                  saveState={saveState === 'running' || updateVersionState === 'running' ? 'running' : saveState === 'failed' || updateVersionState === 'failed' ? 'failed' : 'idle'}
                />
              )}

              {builderTab === 'evals' && selectedBot && (
                <EvalsPanel runs={evalRuns} onRun={handleRunEvals} running={evalsRunning} disabled={!parsedConfig.ok} />
              )}
            </div>
          )}

          {view === 'campaigns' && (
            <CampaignsView
              campaigns={campaigns}
              bots={bots}
              languages={languages}
              outcomes={outcomes}
              loading={loadingCampaigns}
              workspaceMode={campaignWorkspaceMode}
              selectedCampaignKey={selectedCampaignKey}
              onSelectCampaign={handleSelectCampaign}
              onSelectCampaignLeads={handleSelectCampaignLeads}
              onBackToList={handleBackToCampaignList}
              onSaveStrategy={handleSaveStrategy}
              saveState={campaignSaveState}
              onAssignBot={handleAssignBot}
              assignBotState={assignBotState}
              onSetStatus={handleSetCampaignStatus}
              onCreateCampaign={handleCreateCampaign}
              createState={campaignCreateState}
              onDelete={handleDeleteCampaign}
            />
          )}


          {view === 'phone_numbers' && (
            <PhoneNumbersView
              phoneNumbers={phoneNumbers}
              bots={bots}
              loading={loadingPhoneNumbers}
              onCreate={handleCreatePhoneNumber}
              createState={createPhoneNumberState}
              onUpdate={handleUpdatePhoneNumber}
              updateState={updatePhoneNumberState}
              onReassign={handleReassignPhoneNumber}
              reassignState={reassignPhoneNumberState}
              onDelete={handleDeletePhoneNumber}
            />
          )}

          {view === 'number_mapping' && (
            <NumberMappingView
              mappings={numberMappings}
              bots={bots}
              loading={loadingNumberMappings}
              onMap={handleMapNumberToAgent}
              mapState={mapAgentState}
            />
          )}

          {view === 'test' && (
            <TestCallPanel
              bots={bots}
              selectedBot={testBot}
              selectedBotId={testBotId}
              onSelectBot={setTestBotId}
              runtimeSettings={runtimeSettings}
              form={testForm}
              setForm={setTestForm}
              status={testStatus}
              error={testError}
              closeNote={testCloseNote}
              chatMessage={chatMessage}
              setChatMessage={setChatMessage}
              micEnabled={micEnabled}
              roomName={roomName}
              remoteAudioReady={remoteAudioReady}
              livekitUrl={testLivekitUrl}
              livekitToken={testLivekitToken}
              onStart={handleStartTestCall}
              onStop={handleStopTestCall}
              onLiveKitConnected={handleLiveKitConnected}
              onLiveKitDisconnected={handleLiveKitDisconnected}
              onLiveKitError={handleLiveKitError}
              onMicChange={setMicEnabled}
              onAudioReady={setRemoteAudioReady}
            />
          )}

          {view === 'transcripts' && (
            <TranscriptsView
              transcripts={transcripts}
              selectedTranscript={selectedTranscript}
              localRecordingByRoom={localRecordingByRoom}
              callEvents={callEvents}
              bots={bots}
              campaigns={campaigns}
              loading={loadingTranscripts}
              searchText={transcriptSearchText}
              onSearchText={setTranscriptSearchText}
              filters={transcriptFilters}
              onFiltersChange={setTranscriptFilters}
              onSelect={setSelectedTranscriptId}
              onNavigateTest={() => setView('test')}
            />
          )}

          {view === 'analytics' && (
            <AnalyticsView bots={bots} campaigns={campaigns} />
          )}

          {view === 'observability' && (
            <ObservabilityView
              selectedBot={selectedBot}
              transcripts={transcripts}
              langfuseSettings={langfuseSettings}
              onUpdateLangfuse={handleUpdateLangfuse}
            />
          )}

          {view === 'library' && (
            <LibraryView
              phrases={phrases}
              outcomes={outcomes}
              languageSettings={languageSettings}
              analysisPrompts={analysisPrompts}
              onCreate={handleCreatePhrase}
              onUpdate={handleUpdatePhrase}
              onDelete={handleDeletePhrase}
              onUpdateOutcome={handleUpdateOutcome}
              onUpsertLanguageSettings={handleUpsertLanguageSettings}
              onDeleteLanguageSettings={handleDeleteLanguageSetting}
              onUpdateAnalysisPrompt={handleUpdateAnalysisPrompt}
            />
          )}

          {view === 'settings' && (
            <SettingsView
              runtimeSettings={runtimeSettings}
              onUpdateRuntime={handleUpdateRuntime}
              platformSettings={platformSettings}
              onUpdatePlatformSettings={handleUpdatePlatformSettings}
            />
          )}

          {view === 'audit_log' && <AuditLogView />}

          {view === 'admin' && (
            <AdminView config={pricingConfig} onSave={handleUpdatePricingConfig} />
          )}
        </ResilientPanel>
      </main>

      {showCmdK && (
        <CommandPalette
          bots={bots}
          campaigns={campaigns}
          transcripts={transcripts}
          onClose={() => setShowCmdK(false)}
          onNavigate={navigateFromCmdK}
        />
      )}

      {showShortcuts && <ShortcutsModal onClose={() => setShowShortcuts(false)} />}

      {showNewAgent && (
        <NewAgentWizard
          form={newAgentForm}
          onChange={setNewAgentForm}
          languages={languages}
          busy={newAgentBusy}
          onCancel={() => setShowNewAgent(false)}
          onConfirm={confirmNewAgent}
        />
      )}

      {deleteTarget && (
        <DeleteAgentDialog
          bot={deleteTarget}
          campaigns={campaigns}
          transcriptCount={transcripts.filter((t) => t.bot_id === deleteTarget._id).length}
          busy={deleteBusy}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={confirmDeleteBot}
        />
      )}

      {showPublishConfirm && latestDraft && (
        <PublishConfirmModal
          latestDraft={latestDraft}
          activeVersion={activeVersion}
          activeCallCount={0}
          busy={publishState === 'running'}
          onCancel={() => setShowPublishConfirm(false)}
          onConfirm={confirmPublish}
        />
      )}

      {diffPair && (
        <VersionDiffModal
          versionA={diffPair.a}
          versionB={diffPair.b}
          onClose={() => setDiffPair(null)}
        />
      )}

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
