import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BrowserRouter, useLocation, useNavigate } from 'react-router-dom';
import {
  Activity, BarChart2, BookOpen, Bot as BotIcon, ChevronLeft, ChevronRight, ClipboardList, FileText, GitBranch,
  IndianRupee, Keyboard, LayoutDashboard, Link2, LogOut, Megaphone, Menu, Phone, PhoneCall, Settings as SettingsIcon, X
} from 'lucide-react';

import { api, ApiError, getToken, setToken, API_BASE } from './api';
import type {
  AnalysisPromptEntry, AnalysisPromptKey, Bot, BotVersion, Campaign, CallEvent, CurrentUser, EvalRun, LanguageOption,
  LanguageSettings, LibraryPhrase, NumberMapping, OutcomeEntry, PhoneNumber, PhoneNumberEnvironment,
  PlatformSettings, RuntimeSettings, Transcript, TestRecordingLookup, DialingStrategy, PricingConfig
} from './api';
import type { AgentWorkspaceMode, BuilderMode, CmdKExtra, Diagnostic, RuntimeConfig, TestCallStatus, TestForm, View } from './types';

import { CMD_VIEWS, SHORTCUT_MAP, defaultConfig } from './constants/ui';
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts';
import { parseConfig } from './utils/config';
import { ensureFunctionIds } from './utils/customFunctions';
import { shortId } from './utils/formatting';
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
import { Breadcrumbs } from './components/Breadcrumbs';
import type { Crumb } from './components/Breadcrumbs';
import { PublishConfirmModal } from './components/PublishConfirmModal';
import { VersionDiffModal } from './components/VersionDiffModal';

import { BotsView, NewAgentWizard, DeleteAgentDialog } from './views/BotsView';
import { CreateAgentPicker } from './components/CreateAgentPicker';
import { CreateWithAI } from './components/CreateWithAI';
import { TemplateGallery } from './components/TemplateGallery';
import type { AgentTemplate } from './constants/agentTemplates';
import { BuilderView } from './views/BuilderView';
import { autoLayout as autoLayoutWorkflow } from './views/WorkflowBuilderView';
import { CampaignsView } from './views/CampaignsView';
import { NumbersView } from './views/NumbersView';
import { LibraryView } from './views/LibraryView';
import { TranscriptsView } from './views/TranscriptsView';
import { AnalyticsView } from './views/AnalyticsView';
import { DashboardView } from './views/DashboardView';
import { TestCallPanel, titleFor } from './views/TestCallPanel';
import { TestLLMPanel } from './views/TestLLMPanel';
import { TestInputsModal } from './components/TestInputsModal';
import type { DynamicVariables, FunctionMocks } from './components/TestInputsModal';
import { SettingsView } from './views/SettingsView';
import { Confetti } from './components/Confetti';
import { OnboardingTour } from './components/OnboardingTour';

// Real WebRTC test-call connection is owned by <LiveKitRoom> inside
// components/LiveKitTestSession.tsx, not managed manually here.

type AsyncState = 'idle' | 'running' | 'failed';

const NAV_ICONS: Record<View, React.ReactNode> = {
  dashboard: <LayoutDashboard size={17} />,
  bots: <BotIcon size={17} />,
  builder: <BotIcon size={17} />,
  flow: <GitBranch size={17} />,
  campaigns: <Megaphone size={17} />,
  phone_numbers: <Phone size={17} />,
  number_mapping: <Link2 size={17} />,
  test: <PhoneCall size={17} />,
  transcripts: <FileText size={17} />,
  analytics: <BarChart2 size={17} />,
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
  pre_call_params: {},
  dynamic_variables: {},
};

// Keys must match bot.py's LANG_CONFIGS ("hi"/"en") — add an entry here only once
// bot.py has a matching LANG_CONFIGS entry, otherwise it saves to Mongo and falls
// back silently at call time.
const DEFAULT_LANGUAGES: LanguageOption[] = [
  { id: 'hi', label: 'Hindi' },
  { id: 'en', label: 'English' },
];

// ---------------------------------------------------------------------------
// Login gate
// ---------------------------------------------------------------------------

function LoginForm({ onLoggedIn, ssoError }: { onLoggedIn: () => void; ssoError?: string }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(ssoError || '');
  const [mode, setMode] = useState<'login' | 'signup'>('login');

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      if (mode === 'signup') {
        await api.signup(email.trim(), password);
      } else {
        await api.login(email.trim(), password);
      }
      onLoggedIn();
    } catch (err) {
      setError(friendlyApiError(err));
    } finally {
      setBusy(false);
    }
  }

  function toggleMode() {
    setMode((prev) => (prev === 'login' ? 'signup' : 'login'));
    setError('');
  }

  return (
    <div className="login-screen" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
      <form onSubmit={submit} className="panel" style={{ maxWidth: 360, width: '100%', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div>
          <h2>JustDial Voice AI Platform</h2>
          <p>
            {mode === 'signup'
              ? 'Create an account to manage voice agents, campaigns and transcripts.'
              : 'Sign in to manage voice agents, campaigns and transcripts.'}
          </p>
        </div>
        {error && <div className="notice error">{error}</div>}
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={mode === 'signup' ? 8 : undefined}
            required
          />
        </label>
        <button className="primary" type="submit" disabled={busy}>
          {busy ? (mode === 'signup' ? 'Creating account…' : 'Signing in…') : mode === 'signup' ? 'Create account' : 'Sign in'}
        </button>
        <button type="button" className="link-button" onClick={toggleMode}>
          {mode === 'signup' ? 'Already have an account? Sign in' : "Don't have an account? Sign up"}
        </button>
        {mode === 'login' && (
          <>
            <div className="sso-divider"><span>or</span></div>
            <a className="sso-button" href={`${API_BASE}/api/auth/sso/login`}>
              Sign in with Justdial SSO
            </a>
          </>
        )}
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
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);

  useEffect(() => {
    if (!authed) {
      setCurrentUser(null);
      return;
    }
    api.me().then(setCurrentUser).catch(() => setCurrentUser(null));
  }, [authed]);

  function handleLogout() {
    api.logout();
    setAuthed(false);
  }

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
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(
    () => localStorage.getItem('sidebarCollapsed') === '1'
  );
  function toggleSidebarCollapsed() {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem('sidebarCollapsed', next ? '1' : '0');
      return next;
    });
  }
  function handleGoHome() {
    setView('bots');
    setWorkspaceMode('list');
    setMobileNavOpen(false);
  }

  // ── Diagnostics ─────────────────────────────────────────────────────
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([]);
  const DIAGNOSTIC_AUTO_DISMISS_MS = 6000;
  function pushDiagnostic(scope: string, error: unknown, action?: string, severity: Diagnostic['severity'] = 'error') {
    const diagnostic = buildDiagnostic(scope, error, action, severity);
    setDiagnostics((prev) => [diagnostic, ...prev].slice(0, 20));
    // Transient hiccups (warning/info — a slow endpoint, a background refresh) shouldn't sit
    // on screen forever like a real errors do; auto-clear them the way toasts normally behave.
    // Errors stay until the user dismisses or retries, since those usually need action.
    if (severity !== 'error') {
      setTimeout(() => {
        setDiagnostics((prev) => prev.filter((d) => d.id !== diagnostic.id));
      }, DIAGNOSTIC_AUTO_DISMISS_MS);
    }
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
  const [pricingConfig, setPricingConfig] = useState<PricingConfig | null>(null);
  const [phrases, setPhrases] = useState<LibraryPhrase[]>([]);
  const [outcomes, setOutcomes] = useState<OutcomeEntry[]>([]);
  const [languageSettings, setLanguageSettings] = useState<LanguageSettings[]>([]);
  const [analysisPrompts, setAnalysisPrompts] = useState<AnalysisPromptEntry[]>([]);

  const [loadingBots, setLoadingBots] = useState(false);
  const [loadingCampaigns, setLoadingCampaigns] = useState(false);
  const [loadingTranscripts, setLoadingTranscripts] = useState(false);
  const [loadingMoreTranscripts, setLoadingMoreTranscripts] = useState(false);
  const [hasMoreTranscripts, setHasMoreTranscripts] = useState(false);
  const [selectedTranscriptDetail, setSelectedTranscriptDetail] = useState<Transcript | undefined>(undefined);
  const [loadingPhoneNumbers, setLoadingPhoneNumbers] = useState(false);
  const [loadingNumberMappings, setLoadingNumberMappings] = useState(false);
  const [loadingLibrary, setLoadingLibrary] = useState(false);
  const [mapAgentState, setMapAgentState] = useState<Record<string, AsyncState>>({});

  // Built-in hi/en (bot.py's LANG_CONFIGS) plus any PM-added entries from Library →
  // Language Settings — merged by id so a PM addition (e.g. Punjabi) shows up without
  // dropping the defaults, and without needing a code deploy.
  const languages: LanguageOption[] = [
    ...DEFAULT_LANGUAGES,
    ...languageSettings
      .filter((l) => !DEFAULT_LANGUAGES.some((d) => d.id === l.id))
      // bot.py's LANG_CONFIGS only has "hi"/"en" runtime configs (prompts, STT/TTS codes) —
      // a PM-added language beyond that saves fine but the bot speaks Hindi on calls until
      // an engineer adds a matching LANG_CONFIGS entry. Flag that in the label so it's not
      // a silent trap.
      .map((l) => ({ id: l.id, label: `${l.name} (not live on calls yet)` })),
  ];

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
  const evalsAbortRef = useRef<AbortController | null>(null);
  const [showPublishConfirm, setShowPublishConfirm] = useState(false);
  const [diffPair, setDiffPair] = useState<{ a: BotVersion; b: BotVersion } | null>(null);

  // Create Agent is a small flow, not one modal: picker (AI / template / scratch) first,
  // then whichever path the user picked. Replaces the old behavior of jumping straight into
  // a blank form, which gave a non-technical user nothing to work from.
  const [createAgentStep, setCreateAgentStep] = useState<'closed' | 'picker' | 'ai' | 'workflow_ai' | 'template' | 'scratch'>('closed');
  const [aiCreateBusy, setAiCreateBusy] = useState(false);
  const [aiCreateError, setAiCreateError] = useState('');
  const [workflowAiCreateBusy, setWorkflowAiCreateBusy] = useState(false);
  const [workflowAiCreateError, setWorkflowAiCreateError] = useState('');
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

  // ── Test Audio / Test LLM panel — now rendered inside BuilderView's Test Agent drawer
  // (see testPanelSlot below) rather than a permanently docked column. ──
  const [testPanelMode, setTestPanelMode] = useState<'audio' | 'llm'>('audio');
  const [testInputsOpen, setTestInputsOpen] = useState(false);
  const [dynamicVariables, setDynamicVariables] = useState<DynamicVariables>({});
  const [functionMocks, setFunctionMocks] = useState<FunctionMocks>({});
  const testInputsKey = selectedBotId ? `test-inputs:${selectedBotId}` : '';
  useEffect(() => {
    if (!testInputsKey) { setDynamicVariables({}); setFunctionMocks({}); return; }
    try {
      const saved = JSON.parse(localStorage.getItem(testInputsKey) || '{}');
      setDynamicVariables(saved.dynamicVariables || {});
      setFunctionMocks(saved.functionMocks || {});
    } catch {
      setDynamicVariables({});
      setFunctionMocks({});
    }
  }, [testInputsKey]);
  function saveTestInputs(nextVariables: DynamicVariables, nextMocks: FunctionMocks) {
    setDynamicVariables(nextVariables);
    setFunctionMocks(nextMocks);
    if (testInputsKey) {
      localStorage.setItem(testInputsKey, JSON.stringify({ dynamicVariables: nextVariables, functionMocks: nextMocks }));
    }
  }

  // ── Campaigns ───────────────────────────────────────────────────────
  const [selectedCampaignKey, setSelectedCampaignKey] = useState<string>('');
  const [campaignWorkspaceMode, setCampaignWorkspaceMode] = useState<'list' | 'strategy' | 'leads'>('list');
  const [campaignSaveState, setCampaignSaveState] = useState<AsyncState>('idle');
  const [assignBotState, setAssignBotState] = useState<Record<string, AsyncState>>({});

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
  const [transcriptSource, setTranscriptSource] = useState<'' | 'web_test' | 'batch'>('');
  const [callEvents, setCallEvents] = useState<CallEvent[]>([]);
  const [localRecordingByRoom, setLocalRecordingByRoom] = useState<Record<string, TestRecordingLookup>>({});

  // ── URL routing sync ────────────────────────────────────────────────
  // Single effect handles both directions to avoid ordering races between separate effects:
  // on first login/refresh the URL wins (may be a deep link like /settings); after that,
  // external URL changes (back/forward) update state, and state changes push new URLs.
  const initializedFromUrlRef = useRef(false);
  // React StrictMode double-invokes effects on mount (dev only). The first invocation
  // calls applyRouteToState() and schedules setView(...) — an async update that hasn't
  // rendered yet — then the second invocation runs with the SAME stale `view` closure.
  // Without this, that second pass computes statePath from the old view and pushes it
  // over the just-adopted deep link (e.g. a hard reload on /analytics bounces to /agents).
  // pendingRouteRef tracks the route we just told React to adopt so this effect can use
  // it instead of the not-yet-rendered `view`/selection state, until a real render lands.
  const pendingRouteRef = useRef<ReturnType<typeof parsePath> | null>(null);

  useEffect(() => {
    if (!authed) return;

    if (pendingRouteRef.current && pendingRouteRef.current.view === view) {
      pendingRouteRef.current = null;
    }
    const pending = pendingRouteRef.current;
    const effectiveView = pending?.view ?? view;
    const effectiveBotId = pending?.view === 'bots' ? pending.botId : selectedBotId;
    const effectiveCampaignKey = pending?.view === 'campaigns' ? pending.campaignKey : selectedCampaignKey;
    const effectiveTranscriptId = pending?.view === 'transcripts' ? pending.transcriptId : selectedTranscriptId;

    const statePath =
      effectiveView === 'bots' || effectiveView === 'builder' ? buildPath({ view: 'bots', botId: workspaceMode === 'builder' ? effectiveBotId : '' })
      : effectiveView === 'campaigns' ? buildPath({ view: 'campaigns', campaignKey: campaignWorkspaceMode === 'strategy' ? effectiveCampaignKey : '' })
      : effectiveView === 'transcripts' ? buildPath({ view: 'transcripts', transcriptId: effectiveTranscriptId })
      : buildPath({ view: effectiveView } as Parameters<typeof buildPath>[0]);

    function applyRouteToState(pathname: string) {
      const route = parsePath(pathname);
      pendingRouteRef.current = route;
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
    document.title = `${titleFor(effectiveView === 'builder' ? 'bots' : effectiveView)} — Voice AI Platform`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed, view, selectedBotId, workspaceMode, selectedCampaignKey, campaignWorkspaceMode, selectedTranscriptId, location.pathname, navigate]);

  // ── Test call (LiveKit) ─────────────────────────────────────────────
  const [testBotId, setTestBotId] = useState<string>('');
  const [testForm, setTestForm] = useState<TestForm>(EMPTY_TEST_FORM);
  const [testStatus, setTestStatus] = useState<TestCallStatus>('Idle');
  const [testError, setTestError] = useState('');
  const [testCloseNote, setTestCloseNote] = useState('');
  const [micEnabled, setMicEnabled] = useState(false);
  const [roomName, setRoomName] = useState('');
  const [remoteAudioReady, setRemoteAudioReady] = useState(false);
  const [showFirstCallConfetti, setShowFirstCallConfetti] = useState(false);
  // Set only when createAgentAndEnter() creates someone's very first-ever agent (any of the
  // three Create Agent paths — AI / template / scratch, they all funnel through it). Confetti
  // fires only when THIS specific bot's test call succeeds — not any test call, and not just
  // because the account currently has zero agents (that broke as soon as the agent existed).
  const [firstAgentPendingTestId, setFirstAgentPendingTestId] = useState('');

  const handleAudioReady = (ready: boolean) => {
    setRemoteAudioReady(ready);
    if (ready) {
      // Bot's audio is flowing — advance past "Waiting for bot to join…", which
      // otherwise never changes on its own and ends up shown alongside the real,
      // live agent state (e.g. "Speaking · Waiting for bot to join…").
      setTestStatus((prev) => (prev === 'Waiting for bot to join…' ? 'In call' : prev));
    }
    if (ready && testBotId && testBotId === firstAgentPendingTestId) {
      setFirstAgentPendingTestId('');
      setShowFirstCallConfetti(true);
    }
  };
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

  // The test rail lives inside the agent workspace, so it must always target the agent
  // currently being edited — otherwise "Talk to it" would dispatch whichever bot the
  // standalone /test screen happened to leave in testBotId.
  useEffect(() => {
    if (workspaceMode === 'builder' && selectedBotId && selectedBotId !== testBotId) {
      setTestBotId(selectedBotId);
    }
  }, [workspaceMode, selectedBotId, testBotId]);

  // /test no longer has its own screen — testing an agent happens in that agent's
  // workspace. Old bookmarks and the Cmd+K history still resolve, so send them into the
  // workspace of the last-tested agent rather than 404-ing or showing an empty page.
  useEffect(() => {
    if (view !== 'test') return;
    const target = testBotId || bots[0]?._id || '';
    if (target) handleEditBot(target);
    setView('bots');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, testBotId, bots]);

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

  const TRANSCRIPTS_PAGE_SIZE = 50;

  const loadTranscripts = useCallback(async () => {
    setLoadingTranscripts(true);
    try {
      const page = await api.transcripts({ text: transcriptSearchText || undefined, source: transcriptSource || undefined, limit: TRANSCRIPTS_PAGE_SIZE });
      setTranscripts(page);
      setHasMoreTranscripts(page.length === TRANSCRIPTS_PAGE_SIZE);
    } catch (err) {
      pushDiagnostic('Transcripts', err, 'Retry loading transcripts', 'warning');
    } finally {
      setLoadingTranscripts(false);
    }
  }, [transcriptSearchText, transcriptSource]);

  const loadMoreTranscripts = useCallback(async () => {
    if (loadingMoreTranscripts || !hasMoreTranscripts || transcripts.length === 0) return;
    const oldest = transcripts[transcripts.length - 1]?.created_at;
    if (!oldest) return;
    setLoadingMoreTranscripts(true);
    try {
      const page = await api.transcripts({
        text: transcriptSearchText || undefined,
        source: transcriptSource || undefined,
        before: oldest,
        limit: TRANSCRIPTS_PAGE_SIZE,
      });
      setTranscripts((prev) => [...prev, ...page]);
      setHasMoreTranscripts(page.length === TRANSCRIPTS_PAGE_SIZE);
    } catch (err) {
      pushDiagnostic('Transcripts', err, 'Retry loading more transcripts', 'warning');
    } finally {
      setLoadingMoreTranscripts(false);
    }
  }, [loadingMoreTranscripts, hasMoreTranscripts, transcripts, transcriptSearchText, transcriptSource]);

  const loadSettings = useCallback(async () => {
    try {
      const [platform, runtime] = await Promise.all([
        api.platformSettings(), api.runtimeSettings()
      ]);
      setPlatformSettings(platform);
      setRuntimeSettings(runtime);
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
    setLoadingLibrary(true);
    try {
      const [p, o, l, a] = await Promise.all([
        api.phrases(), api.outcomes(), api.languageSettings(), api.analysisPrompts()
      ]);
      setPhrases(p); setOutcomes(o); setLanguageSettings(l); setAnalysisPrompts(a);
    } catch (err) {
      pushDiagnostic('Library', err, 'Retry loading library', 'warning');
    } finally {
      setLoadingLibrary(false);
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
    loadLibrary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed]);

  // Admin-only settings/pricing config — the backend 403s a non-admin, so wait for
  // currentUser to resolve rather than firing (and getting a diagnostic warning) for
  // every regular user on every login.
  useEffect(() => {
    if (!authed || currentUser?.role !== 'admin') return;
    loadSettings();
    loadPricingConfig();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed, currentUser]);

  // Reload transcripts on search change (debounced)
  useEffect(() => {
    if (!authed) return;
    const id = window.setTimeout(() => { loadTranscripts(); }, 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transcriptSearchText, transcriptSource, authed]);

  // Normalize function ids before a config lands in editor state — legacy configs can carry
  // blank/duplicate ids that make the Functions tab merge edits across unrelated functions.
  function normalizedConfigText(config: RuntimeConfig): string {
    const withIds = config.functions ? { ...config, functions: ensureFunctionIds(config.functions) } : config;
    return JSON.stringify(withIds, null, 2);
  }

  // Load bot versions when a bot is selected / edited. `preferredVersionId`, when given
  // and still present in the refreshed list, wins over the draft/active/first heuristic —
  // a bot can have several draft versions at once (each "New version" click forks another
  // one rather than replacing the current draft), so after e.g. updating v3 in place, the
  // heuristic could easily land on a DIFFERENT draft (v4, v1, whichever the fetch returns
  // first) and silently swap the editor to show its config instead — reading exactly like
  // "the field I just added vanished", when really the save succeeded but the UI moved on.
  const loadBotVersions = useCallback(async (botId: string, preferredVersionId?: string) => {
    try {
      const bundle = await api.bot(botId);
      setVersions(bundle.versions);
      const preferred = preferredVersionId && bundle.versions.find((v) => v._id === preferredVersionId);
      const active = bundle.versions.find((v) => v._id === bundle.bot.active_version_id);
      const draft = bundle.versions.find((v) => v.state === 'draft');
      const initial = preferred || draft || active || bundle.versions[0];
      if (initial) {
        setEditingVersionId(initial._id);
        setConfigText(normalizedConfigText(initial.config));
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
    const controller = new AbortController();
    evalsAbortRef.current = controller;
    setEvalsRunning(true);
    try {
      const run = await api.runEvals(selectedBot._id, editingVersionId || undefined, scenarios, controller.signal);
      setEvalRuns((prev) => [run, ...prev]);
    } catch (err) {
      // A user-initiated Stop click also lands here (the aborted fetch rejects) — skip the
      // diagnostic toast for that case since it's an expected outcome, not a failure.
      if (!controller.signal.aborted) {
        pushDiagnostic('Run evals', err, 'Retry evals', 'error');
      }
    } finally {
      evalsAbortRef.current = null;
      setEvalsRunning(false);
    }
  }

  // "Stop" in the evals panel: only cancels the frontend's wait — backend/evals.py has no
  // cancellation hook, so the simulation keeps running server-side to completion regardless.
  function handleStopEvals() {
    evalsAbortRef.current?.abort();
  }

  // Load call events when a transcript is selected
  useEffect(() => {
    if (!selectedTranscriptId) { setCallEvents([]); return; }
    api.callEvents(selectedTranscriptId).then(setCallEvents).catch((err) => {
      setCallEvents([]);
      pushDiagnostic('Call events', err, undefined, 'warning');
    });
  }, [selectedTranscriptId]);

  // The list endpoint omits transcript/muted_transcript to keep list payload light —
  // fetch the full document here once a row is selected, same pattern as callEvents above.
  useEffect(() => {
    if (!selectedTranscriptId) { setSelectedTranscriptDetail(undefined); return; }
    let cancelled = false;
    api.transcriptDetail(selectedTranscriptId).then((detail) => {
      if (!cancelled) setSelectedTranscriptDetail(detail);
    }).catch((err) => {
      if (!cancelled) {
        setSelectedTranscriptDetail(undefined);
        pushDiagnostic('Transcript detail', err, undefined, 'warning');
      }
    });
    return () => { cancelled = true; };
  }, [selectedTranscriptId]);

  // Prefer the full fetched detail; fall back to the list row (missing transcript/
  // analysis) so the header/status still show instantly while the detail loads.
  const selectedTranscript = selectedTranscriptDetail?._id === selectedTranscriptId
    ? selectedTranscriptDetail
    : transcripts.find((t) => t._id === selectedTranscriptId);

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

  function handleEditBot(botId: string) {
    setSelectedBotId(botId);
    setWorkspaceMode('builder');
    setBuilderTab('prompt');
    loadBotVersions(botId);
  }

  function handleBackFromBuilder() {
    setWorkspaceMode('list');
  }

  const EMPTY_NEW_AGENT_FORM = {
    name: '', description: '', agent_name: '', organization_name: '',
    persona_gender: 'female' as const, language: 'hindi',
    initial_message: defaultConfig.initial_message || '',
  };

  // Deliberately does NOT reset the form: closing the wizard with Escape used to be
  // indistinguishable from discarding it, so an accidental keypress lost every field.
  // The draft survives until the agent is created or Cancel is pressed.
  function handleNewAgent() {
    setCreateAgentStep('picker');
  }

  // Shared by all three creation paths (scratch/AI/template) — lands the user *inside* the
  // agent they just created rather than back on the list, so there's no extra hunt-and-click
  // before they can start testing it.
  async function createAgentAndEnter(name: string, description: string, config: RuntimeConfig) {
    // Capture "this account had zero agents right before this create" BEFORE loadBots()
    // refreshes `bots` and makes that check useless — this is what makes the resulting
    // agent eligible for the first-test-call confetti, not `bots.length` at call time.
    const isFirstEverAgent = bots.length === 0;
    const bot = await api.createBot({ name, description, config });
    // Clear the scratch draft only once the agent actually exists — the wizard no longer
    // resets on close, so this (and an explicit Cancel) are the only paths that discard it.
    setNewAgentForm(EMPTY_NEW_AGENT_FORM);
    setCreateAgentStep('closed');
    await loadBots();
    if (isFirstEverAgent) setFirstAgentPendingTestId(bot._id);
    handleEditBot(bot._id);
    return bot;
  }

  function discardNewAgent() {
    setNewAgentForm(EMPTY_NEW_AGENT_FORM);
    setCreateAgentStep('picker');
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
      await createAgentAndEnter(newAgentForm.name, newAgentForm.description, config);
    } catch (err) {
      pushDiagnostic('New agent', err, 'Retry creating agent', 'error');
    } finally {
      setNewAgentBusy(false);
    }
  }

  async function handleCreateWithAI(description: string) {
    setAiCreateBusy(true);
    setAiCreateError('');
    try {
      const generated = await api.generateAgent(description);
      const config: RuntimeConfig = {
        ...defaultConfig,
        agent_name: generated.agent_name || defaultConfig.agent_name,
        persona_gender: (generated.persona_gender as RuntimeConfig['persona_gender']) || defaultConfig.persona_gender,
        stt_language: generated.stt_language || defaultConfig.stt_language,
        tts_language: generated.tts_language || defaultConfig.tts_language,
        initial_message: generated.initial_message || defaultConfig.initial_message,
        call_end_text: generated.call_end_text || defaultConfig.call_end_text,
        system_prompt: generated.system_prompt || defaultConfig.system_prompt,
        interruption_sensitivity:
          (generated.interruption_sensitivity as RuntimeConfig['interruption_sensitivity']) ||
          defaultConfig.interruption_sensitivity,
      };
      // Bot name: fall back to the persona name (or a generic label) since "Create with AI"
      // never asks for one separately — asking would defeat the point of a one-box flow.
      const name = generated.agent_name ? `${generated.agent_name} — AI-generated` : 'New AI-generated agent';
      await createAgentAndEnter(name, generated.description || description.slice(0, 200), config);
    } catch (err) {
      setAiCreateError(err instanceof Error ? err.message : 'Agent generation failed.');
    } finally {
      setAiCreateBusy(false);
    }
  }

  async function handleCreateWorkflowWithAI(description: string) {
    setWorkflowAiCreateBusy(true);
    setWorkflowAiCreateError('');
    try {
      const generated = await api.generateWorkflow(description);
      const config: RuntimeConfig = {
        ...defaultConfig,
        agent_name: generated.agent_name || defaultConfig.agent_name,
        persona_gender: (generated.persona_gender as RuntimeConfig['persona_gender']) || defaultConfig.persona_gender,
        stt_language: generated.stt_language || defaultConfig.stt_language,
        tts_language: generated.tts_language || defaultConfig.tts_language,
        interruption_sensitivity:
          (generated.interruption_sensitivity as RuntimeConfig['interruption_sensitivity']) ||
          defaultConfig.interruption_sensitivity,
        bot_type: 'workflow',
        // Re-layout with the builder's own algorithm (same one "Auto Arrange" uses) rather
        // than trusting whatever positions the backend guessed — keeps exactly one source of
        // truth for node placement instead of two layout implementations drifting apart.
        workflow: autoLayoutWorkflow(generated.workflow),
        global_prompt: generated.global_prompt || '',
        // Placeholder URLs (backend PLACEHOLDER_URL) travel through unchanged — the builder
        // (WorkflowGraphNode / CustomFunctionsEditor) flags them with a red "needs attention"
        // badge so the user notices and fills them in before the bot goes live.
        functions: generated.functions.length ? generated.functions : defaultConfig.functions,
        function_calling: generated.functions.length > 0,
      };
      const name = generated.agent_name ? `${generated.agent_name} — AI-generated workflow` : 'New AI-generated workflow';
      await createAgentAndEnter(name, generated.description || description.slice(0, 200), config);
    } catch (err) {
      setWorkflowAiCreateError(err instanceof Error ? err.message : 'Workflow generation failed.');
    } finally {
      setWorkflowAiCreateBusy(false);
    }
  }

  async function handleUseTemplate(template: AgentTemplate) {
    setNewAgentBusy(true);
    try {
      const config: RuntimeConfig = {
        ...defaultConfig,
        agent_name: template.agent_name,
        initial_message: template.initial_message,
        system_prompt: template.system_prompt,
      };
      await createAgentAndEnter(template.name, template.description, config);
    } catch (err) {
      pushDiagnostic('New agent', err, 'Retry creating agent', 'error');
    } finally {
      setNewAgentBusy(false);
    }
  }

  const [duplicatingBotId, setDuplicatingBotId] = useState<string>('');

  async function handleDuplicateBot(bot: Bot) {
    setDuplicatingBotId(bot._id);
    try {
      // Clone whatever config is actually running: the active published version if there
      // is one, else the latest draft. Falls back to defaultConfig only if the bot
      // somehow has neither (shouldn't happen, but a duplicate should never 400).
      const { bot: fullBot, versions } = await api.bot(bot._id);
      const sourceVersion =
        versions.find((v) => v._id === fullBot.active_version_id) ||
        versions.find((v) => v.state === 'draft') ||
        versions[0];
      const config = (sourceVersion?.config as RuntimeConfig) || defaultConfig;
      const created = await api.createBot({
        name: `${bot.name} (copy)`,
        description: bot.description,
        config,
      });
      await loadBots();
      showToast(`Duplicated as "${created.name}"`);
      handleEditBot(created._id);
    } catch (err) {
      pushDiagnostic('Duplicate agent', err, 'Retry duplicating agent', 'error');
    } finally {
      setDuplicatingBotId('');
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
    // Auto-fill sensible STT/TTS language defaults so a PM picking a language gets a
    // coherent pipeline without separately configuring 3 dropdowns. Only fills in blanks —
    // never clobbers an stt_language/tts_language the user already set explicitly.
    const STT_TTS_DEFAULTS: Record<string, { stt: string; tts: string }> = {
      hi: { stt: 'hi-IN', tts: 'hi-IN' },
      en: { stt: 'en-US', tts: 'en-IN' },
    };
    const defaults = STT_TTS_DEFAULTS[value];
    setConfigText((prev) => {
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(prev);
      } catch {
        return prev;
      }
      const next: Record<string, unknown> = { ...parsed, language: value };
      // Only auto-fill if the current value is blank OR still equals the default we
      // auto-filled for the PREVIOUS language (so a real user override always sticks,
      // but switching hi -> en -> hi doesn't leave stt/tts stuck on a stale language).
      const prevLang = typeof parsed.language === 'string' ? parsed.language : '';
      const prevDefaults = STT_TTS_DEFAULTS[prevLang];
      if (defaults) {
        const sttWasAuto = !next.stt_language || (prevDefaults && next.stt_language === prevDefaults.stt);
        const ttsWasAuto = !next.tts_language || (prevDefaults && next.tts_language === prevDefaults.tts);
        if (sttWasAuto) next.stt_language = defaults.stt;
        if (ttsWasAuto) next.tts_language = defaults.tts;
      }
      return JSON.stringify(next, null, 2);
    });
  }

  async function handleSaveDraft() {
    if (!selectedBot || !parsedConfig.ok) return;
    setSaveState('running');
    try {
      const result = await api.saveDraft(selectedBot._id, parsedConfig.value);
      await loadBots();
      await loadBotVersions(selectedBot._id, result.draft_version_id);
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
      const result = await api.publish(selectedBot._id);
      await loadBots();
      await loadBotVersions(selectedBot._id, result.active_version_id);
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
      await loadBotVersions(selectedBot._id, result.draft_version_id);
      setRollbackState((prev) => ({ ...prev, [key]: 'idle' }));
      showToast('Rolled back to a new draft');
    } catch (err) {
      setRollbackState((prev) => ({ ...prev, [key]: 'failed' }));
      pushDiagnostic('Rollback', err, 'Retry rollback', 'error');
    }
  }

  function handleSelectVersion(v: BotVersion) {
    setEditingVersionId(v._id);
    setConfigText(normalizedConfigText(v.config));
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
      await loadBotVersions(selectedBot._id, versionId);
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
      // loadBotVersions already lands on result.draft_version_id (and its matching
      // configText) via `preferredVersionId` — no separate setEditingVersionId needed,
      // which previously left editingVersionId and configText pointing at different
      // versions whenever another draft also existed.
      await loadBotVersions(selectedBot._id, result.draft_version_id);
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

  async function handleBatchCallCreated() {
    await loadCampaigns();
    showToast('Batch call created');
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
        pre_call_params: Object.keys(testForm.pre_call_params || {}).length ? testForm.pre_call_params : undefined,
        dynamic_variables: Object.keys(testForm.dynamic_variables || {}).length ? testForm.dynamic_variables : undefined,
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
    loadBots(); loadCampaigns(); loadPhoneNumbers(); loadNumberMappings(); loadTranscripts(); loadLibrary();
    if (currentUser?.role === 'admin') { loadSettings(); loadPricingConfig(); }
  }
  function handleUseCachedDiagnostics() {
    setDiagnostics([]);
  }

  if (!authed) {
    return <LoginForm onLoggedIn={() => setAuthed(true)} ssoError={ssoError} />;
  }

  const breadcrumbs: Crumb[] = (() => {
    if (view === 'bots' || view === 'builder') {
      const crumbs: Crumb[] = [{ label: 'Agents', onClick: () => { setView('bots'); setWorkspaceMode('list'); } }];
      if (workspaceMode === 'builder' && selectedBot) crumbs.push({ label: selectedBot.name });
      return crumbs;
    }
    if (view === 'campaigns') {
      const crumbs: Crumb[] = [{ label: 'Campaigns', onClick: () => { setCampaignWorkspaceMode('list'); setSelectedCampaignKey(''); } }];
      if (campaignWorkspaceMode !== 'list' && selectedCampaignKey) crumbs.push({ label: selectedCampaignKey });
      return crumbs;
    }
    if (view === 'transcripts') {
      const crumbs: Crumb[] = [{ label: 'Transcripts', onClick: () => setSelectedTranscriptId('') }];
      if (selectedTranscript) crumbs.push({ label: shortId(selectedTranscript._id) });
      return crumbs;
    }
    return [{ label: titleFor(view) }];
  })();

  // Rendered inside BuilderView's "Test Agent" drawer (testPanelSlot prop) — state stays
  // owned here since it's already threaded through handleStartTestCall/handleStopTestCall
  // and the LiveKit connection callbacks below; only the JSX placement moved.
  const testPanelSlot = (
    <>
      <div className="test-rail-tabs">
        <div className="mode-toggle" role="tablist">
          <button role="tab" aria-selected={testPanelMode === 'audio'} className={testPanelMode === 'audio' ? 'mode-btn active' : 'mode-btn'} onClick={() => setTestPanelMode('audio')}>
            Test Audio
          </button>
          <button role="tab" aria-selected={testPanelMode === 'llm'} className={testPanelMode === 'llm' ? 'mode-btn active' : 'mode-btn'} onClick={() => setTestPanelMode('llm')}>
            Test LLM
          </button>
        </div>
        <button title="Test Inputs" onClick={() => setTestInputsOpen(true)}>{'{ }'}</button>
      </div>

      {testPanelMode === 'audio' && (
        <TestCallPanel
          variant="rail"
          bots={bots}
          selectedBot={selectedBot}
          selectedBotId={selectedBotId}
          onSelectBot={setTestBotId}
          runtimeSettings={runtimeSettings}
          functions={parsedConfig.ok ? (parsedConfig.value.functions || []) : []}
          dynamicVariables={parsedConfig.ok ? (parsedConfig.value.dynamic_variables || []) : []}
          form={testForm}
          setForm={setTestForm}
          status={testStatus}
          error={testError}
          closeNote={testCloseNote}
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
          onAudioReady={handleAudioReady}
          onTriage={
            selectedBot?._id
              ? (rn, status, error, closeNote) => api.triageTestCall(selectedBot!._id, rn, status, error, closeNote)
              : undefined
          }
        />
      )}

      {testPanelMode === 'llm' && selectedBot && (
        <TestLLMPanel
          botId={selectedBot._id}
          systemPrompt={parsedConfig.ok ? String(parsedConfig.value.system_prompt || '') : ''}
          dynamicVariables={dynamicVariables}
          functionMocks={functionMocks}
        />
      )}

    </>
  );

  // Rendered at app root (not inside testPanelSlot) so closing the Test Agent sidebar —
  // which unmounts testPanelSlot — doesn't discard an open Test Inputs modal's edits.
  const testInputsModal = testInputsOpen && (
    <TestInputsModal
      open={testInputsOpen}
      onClose={() => setTestInputsOpen(false)}
      functions={parsedConfig.ok ? (parsedConfig.value.functions || []) : []}
      dynamicVariables={dynamicVariables}
      onChangeDynamicVariables={(v) => saveTestInputs(v, functionMocks)}
      functionMocks={functionMocks}
      onChangeFunctionMocks={(v) => saveTestInputs(dynamicVariables, v)}
      preCallParams={testForm.pre_call_params}
      onChangePreCallParams={(v) => setTestForm((f) => ({ ...f, pre_call_params: v }))}
    />
  );

  return (
    <div className="app-shell">
      {testInputsModal}
      <Confetti fire={showFirstCallConfetti} onDone={() => setShowFirstCallConfetti(false)} />
      <OnboardingTour run={!loadingBots && bots.length === 0 && view === 'bots'} />
      <div className="mobile-topbar">
        <button onClick={() => setMobileNavOpen(true)} aria-label="Open navigation">
          <Menu size={18} />
        </button>
        <button className="sidebar-brand sidebar-brand-btn" style={{ padding: 0 }} onClick={handleGoHome} aria-label="Go to home">
          <img src="/justdial-logo.png" alt="Justdial" className="sidebar-logo sidebar-logo-full" />
          <span className="sidebar-brand-subtitle">Voice AI Platform</span>
        </button>
      </div>

      {mobileNavOpen && <div className="sidebar-backdrop" onClick={() => setMobileNavOpen(false)} />}

      <aside className={mobileNavOpen ? 'sidebar open' : sidebarCollapsed ? 'sidebar collapsed' : 'sidebar'}>
        <div className="sidebar-brand">
          <div className="sidebar-brand-text">
            <button className="sidebar-brand-btn" onClick={handleGoHome} aria-label="Go to home" title="Home">
              <img src="/justdial-logo.png" alt="Justdial" className="sidebar-logo sidebar-logo-full" />
              <img src="/favicon.jpeg" alt="Justdial" className="sidebar-logo sidebar-logo-mono" />
            </button>
            <span className="sidebar-brand-subtitle">Voice AI Platform</span>
          </div>
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
              tourId={item.view === 'bots' ? 'onboarding-nav-agents' : item.view === 'dashboard' ? 'onboarding-nav-dashboard' : undefined}
              active={view === item.view || (item.view === 'bots' && view === 'builder')}
              onClick={() => {
                setView(item.view);
                if (item.view === 'bots') setWorkspaceMode('list');
                setMobileNavOpen(false);
              }}
            />
          ))}
        </SidebarNav>
        <button className="nav-item shortcuts-btn" onClick={() => setShowShortcuts(true)} title="Shortcuts">
          <Keyboard size={17} /><span>Shortcuts</span>
        </button>
        {currentUser && !sidebarCollapsed && (
          <div className="sidebar-current-user" title={currentUser.email}>
            {currentUser.email}
          </div>
        )}
        <button className="nav-item logout-btn" onClick={handleLogout} title="Sign out">
          <LogOut size={17} /><span>Sign out</span>
        </button>
        <button
          className="nav-item sidebar-collapse-btn"
          onClick={toggleSidebarCollapsed}
          aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {sidebarCollapsed ? <ChevronRight size={17} /> : <><ChevronLeft size={17} /><span>Collapse</span></>}
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
          <Breadcrumbs crumbs={breadcrumbs} />
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
              loading={loadingBots}
              onEdit={handleEditBot}
              onDelete={handleDeleteBotRequest}
              onNew={handleNewAgent}
              onDuplicate={handleDuplicateBot}
              duplicatingBotId={duplicatingBotId}
              onBotRestored={loadBots}
            />
          )}

          {(view === 'builder' || (view === 'bots' && workspaceMode === 'builder')) && (
            <div className="agent-workspace">
              {builderTab === 'prompt' && (
                <BuilderView
                  testPanelSlot={testPanelSlot}
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
                  evalRuns={evalRuns}
                  onRunEvals={handleRunEvals}
                  evalsRunning={evalsRunning}
                  onStopEvals={handleStopEvals}
                />
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
              onBatchCallCreated={handleBatchCallCreated}
              onDelete={handleDeleteCampaign}
            />
          )}

          {(view === 'phone_numbers' || view === 'number_mapping') && (
            <NumbersView
              phoneNumbers={phoneNumbers}
              bots={bots}
              loadingPhoneNumbers={loadingPhoneNumbers}
              onCreate={handleCreatePhoneNumber}
              createState={createPhoneNumberState}
              onUpdate={handleUpdatePhoneNumber}
              updateState={updatePhoneNumberState}
              onReassign={handleReassignPhoneNumber}
              reassignState={reassignPhoneNumberState}
              onDelete={handleDeletePhoneNumber}
              mappings={numberMappings}
              loadingMappings={loadingNumberMappings}
              onMap={handleMapNumberToAgent}
              mapState={mapAgentState}
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
              source={transcriptSource}
              onSourceChange={setTranscriptSource}
              onSelect={setSelectedTranscriptId}
              onNavigateTest={() => setView('test')}
              hasMore={hasMoreTranscripts}
              loadingMore={loadingMoreTranscripts}
              onLoadMore={loadMoreTranscripts}
            />
          )}

          {view === 'dashboard' && <DashboardView onGoToAgents={handleGoHome} />}

          {view === 'analytics' && (
            <AnalyticsView bots={bots} campaigns={campaigns} onGoToAgents={handleGoHome} />
          )}

          {view === 'library' && (
            <LibraryView
              phrases={phrases}
              outcomes={outcomes}
              languageSettings={languageSettings}
              analysisPrompts={analysisPrompts}
              loading={loadingLibrary}
              onCreate={handleCreatePhrase}
              onUpdate={handleUpdatePhrase}
              onDelete={handleDeletePhrase}
              onUpdateOutcome={handleUpdateOutcome}
              onUpsertLanguageSettings={handleUpsertLanguageSettings}
              onDeleteLanguageSettings={handleDeleteLanguageSetting}
              onUpdateAnalysisPrompt={handleUpdateAnalysisPrompt}
            />
          )}

          {(view === 'settings' || view === 'audit_log' || view === 'admin') && (
            <SettingsView
              runtimeSettings={runtimeSettings}
              onUpdateRuntime={handleUpdateRuntime}
              platformSettings={platformSettings}
              onUpdatePlatformSettings={handleUpdatePlatformSettings}
              pricingConfig={pricingConfig}
              onUpdatePricingConfig={handleUpdatePricingConfig}
              initialTab={view === 'audit_log' ? 'audit_log' : view === 'admin' ? 'admin' : undefined}
              isAdmin={currentUser?.role === 'admin'}
              currentUserEmail={currentUser?.email}
              bots={bots}
            />
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

      {createAgentStep === 'picker' && (
        <CreateAgentPicker
          onClose={() => setCreateAgentStep('closed')}
          onSelectAI={() => setCreateAgentStep('ai')}
          onSelectWorkflowAI={() => setCreateAgentStep('workflow_ai')}
          onSelectTemplate={() => setCreateAgentStep('template')}
          // No reset here: re-entering "from scratch" after an accidental Escape must restore
          // the draft, not blank it. The form is cleared on successful create and on Cancel.
          onSelectScratch={() => setCreateAgentStep('scratch')}
        />
      )}

      {createAgentStep === 'ai' && (
        <CreateWithAI
          onBack={() => setCreateAgentStep('picker')}
          onContinue={handleCreateWithAI}
          busy={aiCreateBusy}
          error={aiCreateError}
        />
      )}

      {createAgentStep === 'workflow_ai' && (
        <CreateWithAI
          title="Create a workflow with AI"
          label="Describe the workflow bot you want to build"
          placeholder='e.g. "Call the customer, ask for their order ID, look up the order status via our API, and if it is delayed offer to transfer them to a human agent — otherwise read out the expected delivery date and end the call."'
          hint="We'll build the full node graph — conversation steps, condition branches, and API-call nodes — for you to review in the builder. Any endpoint we can't know is left as a placeholder, flagged in red."
          continueLabel="Generate workflow"
          generatingLabel="Building workflow…"
          onBack={() => setCreateAgentStep('picker')}
          onContinue={handleCreateWorkflowWithAI}
          busy={workflowAiCreateBusy}
          error={workflowAiCreateError}
        />
      )}

      {createAgentStep === 'template' && (
        <TemplateGallery
          onBack={() => setCreateAgentStep('picker')}
          onUseTemplate={handleUseTemplate}
          busy={newAgentBusy}
        />
      )}

      {createAgentStep === 'scratch' && (
        <NewAgentWizard
          form={newAgentForm}
          onChange={setNewAgentForm}
          languages={languages}
          busy={newAgentBusy}
          // onCancel = Escape/close: step back to the picker but keep the draft.
          // onDiscard = the Cancel button: an explicit throw-away, so clear it.
          onCancel={() => setCreateAgentStep('picker')}
          onDiscard={discardNewAgent}
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
          onViewFullDiff={activeVersion ? () => setDiffPair({ a: activeVersion, b: latestDraft }) : undefined}
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
