import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';
import { api } from './api';
import type { Bot } from './api';

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api');
  return {
    ...actual,
    api: {
      login: vi.fn(),
      logout: vi.fn(),
      bots: vi.fn(),
      createBot: vi.fn(),
      bot: vi.fn(),
      saveDraft: vi.fn(),
      updateVersion: vi.fn(),
      unpublish: vi.fn(),
      renameBot: vi.fn(),
      publish: vi.fn(),
      rollback: vi.fn(),
      deleteBot: vi.fn(),
      runEvals: vi.fn(),
      listEvals: vi.fn(),
      platformSettings: vi.fn(),
      updatePlatformSettings: vi.fn(),
      runtimeSettings: vi.fn(),
      updateRuntimeSettings: vi.fn(),
      langfuseSettings: vi.fn(),
      updateLangfuseSettings: vi.fn(),
      campaigns: vi.fn(),
      saveCampaignStrategy: vi.fn(),
      assignCampaignBot: vi.fn(),
      setCampaignStatus: vi.fn(),
      phoneNumbers: vi.fn(),
      createPhoneNumber: vi.fn(),
      reassignPhoneNumber: vi.fn(),
      deletePhoneNumber: vi.fn(),
      phrases: vi.fn(),
      createPhrase: vi.fn(),
      updatePhrase: vi.fn(),
      deletePhrase: vi.fn(),
      outcomes: vi.fn(),
      updateOutcome: vi.fn(),
      languageSettings: vi.fn(),
      upsertLanguageSettings: vi.fn(),
      transcripts: vi.fn(),
      callEvents: vi.fn(),
      testRecordingLookup: vi.fn(),
      exportCsvUrl: vi.fn(),
      outcomeAnalytics: vi.fn(),
      qualityAlerts: vi.fn(),
      startTestCall: vi.fn(),
      stopTestCall: vi.fn(),
      getPricingAdminConfig: vi.fn().mockResolvedValue({
        stt: [], llm: [], tts: [], telephony: [],
      }),
      updatePricingAdminConfig: vi.fn(),
      pricingMatrix: vi.fn(),
      pricingTiers: vi.fn(),
      pricingBudgetRoute: vi.fn(),
    },
  };
});

// livekit-client isn't relevant to these journeys and jsdom lacks WebRTC APIs.
vi.mock('livekit-client', () => ({
  Room: vi.fn(() => ({
    on: vi.fn(),
    connect: vi.fn(),
    disconnect: vi.fn(),
    localParticipant: { publishTrack: vi.fn(), setMicrophoneEnabled: vi.fn(), publishData: vi.fn() },
  })),
  RoomEvent: { TrackSubscribed: 'trackSubscribed', Disconnected: 'disconnected' },
  createLocalTracks: vi.fn().mockResolvedValue([]),
}));

const mockedApi = vi.mocked(api, true);

function makeBot(overrides: Partial<Bot> = {}): Bot {
  return {
    _id: 'bot-1',
    name: 'Sales Bot',
    description: 'Outbound sales',
    assistant_id: 'asst-1',
    status: 'active',
    updated_at: new Date().toISOString(),
    active_version_id: 'v1',
    ...overrides,
  };
}

function mockAuthedDataLoads() {
  mockedApi.bots.mockResolvedValue([]);
  mockedApi.campaigns.mockResolvedValue([]);
  mockedApi.phoneNumbers.mockResolvedValue([]);
  mockedApi.transcripts.mockResolvedValue([]);
  mockedApi.platformSettings.mockResolvedValue({
    active_environment: 'dev',
    dev: {},
    prod: {},
  });
  mockedApi.runtimeSettings.mockResolvedValue({});
  mockedApi.langfuseSettings.mockResolvedValue({});
  mockedApi.phrases.mockResolvedValue([]);
  mockedApi.outcomes.mockResolvedValue([]);
  mockedApi.languageSettings.mockResolvedValue([]);
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  window.history.pushState({}, '', '/');
});

describe('Login journey', () => {
  it('renders the login form when not authenticated', () => {
    render(<App />);
    expect(screen.getByRole('heading', { name: /justdial voice ai platform/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
  });

  it('logs in with valid credentials and renders the authenticated shell', async () => {
    const user = userEvent.setup();
    mockedApi.login.mockResolvedValue({ token: 'tok-123', email: 'user@justdial.com' });
    mockAuthedDataLoads();

    render(<App />);

    await user.type(screen.getByLabelText(/email/i), 'user@justdial.com');
    await user.type(screen.getByLabelText(/password/i), 'correct-password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getAllByText('Voice AI Platform').length).toBeGreaterThan(0);
    });
    // Sidebar nav items rendered — confirms authenticated shell, not login screen.
    expect(screen.getByRole('button', { name: /agents/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /shortcuts/i })).toBeInTheDocument();
    expect(mockedApi.login).toHaveBeenCalledWith('user@justdial.com', 'correct-password');
  });

  it('shows a specific human-readable error on invalid credentials, not a raw error dump', async () => {
    const user = userEvent.setup();
    mockedApi.login.mockRejectedValue(new Error('Invalid email or password'));

    render(<App />);

    await user.type(screen.getByLabelText(/email/i), 'user@justdial.com');
    await user.type(screen.getByLabelText(/password/i), 'wrong-password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    const notice = await screen.findByText('Invalid email or password');
    expect(notice).toBeInTheDocument();
    // Should not have proceeded past the login screen.
    expect(screen.queryAllByText('Voice AI Platform').length).toBe(0);
    // Should not dump a stack trace / object representation.
    expect(screen.queryByText(/at Object\.|\[object Object\]/)).not.toBeInTheDocument();
  });

  it('disables the submit button and shows a loading label while the login request is in flight', async () => {
    const user = userEvent.setup();
    let resolveLogin: (value: { token: string; email: string }) => void = () => {};
    mockedApi.login.mockImplementation(
      () => new Promise((resolve) => { resolveLogin = resolve; })
    );
    mockAuthedDataLoads();

    render(<App />);

    await user.type(screen.getByLabelText(/email/i), 'user@justdial.com');
    await user.type(screen.getByLabelText(/password/i), 'correct-password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    const pendingButton = await screen.findByRole('button', { name: /signing in/i });
    expect(pendingButton).toBeDisabled();

    resolveLogin({ token: 'tok-123', email: 'user@justdial.com' });

    await waitFor(() => {
      expect(screen.getAllByText('Voice AI Platform').length).toBeGreaterThan(0);
    });
  });
});

describe('Empty states', () => {
  it('renders the EmptyState component with its real heading/description/action when there are zero bots', async () => {
    const user = userEvent.setup();
    mockedApi.login.mockResolvedValue({ token: 'tok-123', email: 'user@justdial.com' });
    mockAuthedDataLoads(); // bots() resolves []

    render(<App />);
    await user.type(screen.getByLabelText(/email/i), 'user@justdial.com');
    await user.type(screen.getByLabelText(/password/i), 'correct-password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText('No agents yet')).toBeInTheDocument();
    expect(screen.getByText(/create your first voice agent/i)).toBeInTheDocument();
    const createButton = screen.getByRole('button', { name: /create first agent/i });
    expect(createButton).toBeInTheDocument();

    await user.click(createButton);
    // Opens the new-agent wizard modal (the EmptyState action wired to onNew).
    expect(await screen.findByRole('dialog', { name: /new voice agent/i })).toBeInTheDocument();
  });
});

describe('Error handling surfaced via DiagnosticsBar', () => {
  it('shows "Dashboard diagnostics clear" when nothing has failed', async () => {
    const user = userEvent.setup();
    mockedApi.login.mockResolvedValue({ token: 'tok-123', email: 'user@justdial.com' });
    mockAuthedDataLoads();

    render(<App />);
    await user.type(screen.getByLabelText(/email/i), 'user@justdial.com');
    await user.type(screen.getByLabelText(/password/i), 'correct-password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText('Dashboard diagnostics clear')).toBeInTheDocument();
  });

  it('surfaces a specific human-readable message in the DiagnosticsBar when loading campaigns fails, without crashing', async () => {
    const user = userEvent.setup();
    mockedApi.login.mockResolvedValue({ token: 'tok-123', email: 'user@justdial.com' });
    mockAuthedDataLoads();
    mockedApi.campaigns.mockReset();
    mockedApi.campaigns.mockRejectedValue(new Error('Failed to fetch'));

    render(<App />);
    await user.type(screen.getByLabelText(/email/i), 'user@justdial.com');
    await user.type(screen.getByLabelText(/password/i), 'correct-password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    // friendlyApiError() rewrites "Failed to fetch" into a specific instruction message.
    const diagnostic = await screen.findByText(/Cannot reach the FastAPI backend\. Start it with \.\/start_api\.sh/i);
    expect(diagnostic).toBeInTheDocument();
    expect(screen.getByText(/^Campaigns:/)).toBeInTheDocument();

    // App shell still renders normally (no crash / blank screen).
    expect(screen.getAllByText('Voice AI Platform').length).toBeGreaterThan(0);

    // Dismiss action works.
    await user.click(screen.getByRole('button', { name: /dismiss/i }));
    await waitFor(() => {
      expect(screen.getByText('Dashboard diagnostics clear')).toBeInTheDocument();
    });
  });

  it('logs the user out (returns to login) when bots() rejects with a 401 ApiError', async () => {
    const user = userEvent.setup();
    mockedApi.login.mockResolvedValue({ token: 'tok-123', email: 'user@justdial.com' });
    mockAuthedDataLoads();
    const { ApiError } = await vi.importActual<typeof import('./api')>('./api');
    mockedApi.bots.mockReset();
    mockedApi.bots.mockRejectedValue(new ApiError(401, 'Unauthorized'));

    render(<App />);
    await user.type(screen.getByLabelText(/email/i), 'user@justdial.com');
    await user.type(screen.getByLabelText(/password/i), 'correct-password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /justdial voice ai platform/i })).toBeInTheDocument();
    });
  });
});

describe('Bot select -> builder -> EvalsPanel microinteractions', () => {
  async function loginAndOpenBuilder(user: ReturnType<typeof userEvent.setup>, bot: Bot) {
    mockedApi.login.mockResolvedValue({ token: 'tok-123', email: 'user@justdial.com' });
    mockAuthedDataLoads();
    mockedApi.bots.mockResolvedValue([bot]);
    mockedApi.bot.mockResolvedValue({
      bot,
      versions: [
        { _id: 'v1', bot_id: bot._id, version: 1, state: 'draft', config: { system_prompt: 'hi' }, created_at: new Date().toISOString() },
      ],
    });
    mockedApi.listEvals.mockResolvedValue([]);

    render(<App />);
    await user.type(screen.getByLabelText(/email/i), 'user@justdial.com');
    await user.type(screen.getByLabelText(/password/i), 'correct-password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    const editButtons = await screen.findAllByRole('button', { name: /^edit$/i });
    await user.click(editButtons[0]);

    // Prompt/Flow/Evals are now tabs within one agent workspace (consolidated from
    // separate sidebar destinations), so wait for the tab bar rather than the
    // Evals panel, which only mounts once its tab is selected.
    await screen.findByRole('tab', { name: /prompt/i });
  }

  async function openEvalsTab(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('tab', { name: /evals/i }));
    await screen.findByText('Pre-publish evals');
  }

  it('runs default evals and shows a running/disabled state until the promise resolves', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    await loginAndOpenBuilder(user, bot);
    await openEvalsTab(user);

    let resolveRun: (value: any) => void = () => {};
    mockedApi.runEvals.mockImplementation(() => new Promise((resolve) => { resolveRun = resolve; }));

    const runButton = screen.getByRole('button', { name: /run default evals/i });
    await user.click(runButton);

    const runningButton = await screen.findByRole('button', { name: /running simulation/i });
    expect(runningButton).toBeDisabled();

    resolveRun({
      _id: 'run-1', bot_id: bot._id, version_id: 'v1', created_at: new Date().toISOString(),
      results: [], total: 2, passed: 2, failed: 0,
    });

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /run default evals/i })).not.toBeDisabled();
    });
    expect(mockedApi.runEvals).toHaveBeenCalledWith(bot._id, 'v1', undefined);
    expect(await screen.findByText('2/2 scenarios passed')).toBeInTheDocument();
  });

  it('runs with custom scenarios when the scenario editor has valid entries', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    await loginAndOpenBuilder(user, bot);
    await openEvalsTab(user);

    mockedApi.runEvals.mockResolvedValue({
      _id: 'run-2', bot_id: bot._id, version_id: 'v1', created_at: new Date().toISOString(),
      results: [], total: 1, passed: 1, failed: 0,
    });

    await user.click(screen.getByRole('button', { name: /customize scenarios/i }));
    await user.click(screen.getByRole('button', { name: /add custom scenario/i }));
    await user.type(screen.getByPlaceholderText(/angry repeat caller/i), 'Angry repeat caller');
    await user.type(screen.getByPlaceholderText(/you are a caller who/i), 'You are frustrated and repeat yourself.');

    const runButton = screen.getByRole('button', { name: /run 1 custom scenario/i });
    await user.click(runButton);

    await waitFor(() => {
      expect(mockedApi.runEvals).toHaveBeenCalledWith(
        bot._id,
        'v1',
        [expect.objectContaining({ name: 'Angry repeat caller', caller_persona: 'You are frustrated and repeat yourself.' })]
      );
    });
  });

  it('surfaces a diagnostic and does not crash when runEvals rejects', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    await loginAndOpenBuilder(user, bot);
    await openEvalsTab(user);

    mockedApi.runEvals.mockRejectedValue(new Error('Simulation backend unavailable'));

    await user.click(screen.getByRole('button', { name: /run default evals/i }));

    expect(await screen.findByText(/Run evals: Simulation backend unavailable/)).toBeInTheDocument();
    // Button returns to its idle, clickable state (not stuck disabled).
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /run default evals/i })).not.toBeDisabled();
    });
  });

  it('disables save-draft button while saving and shows the loading label', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    await loginAndOpenBuilder(user, bot);

    let resolveSave: (value: any) => void = () => {};
    mockedApi.saveDraft.mockImplementation(() => new Promise((resolve) => { resolveSave = resolve; }));

    const saveButton = screen.getByRole('button', { name: /new version/i });
    await user.click(saveButton);

    const savingButton = await screen.findByRole('button', { name: /saving/i });
    expect(savingButton).toBeDisabled();

    resolveSave({ draft_version_id: 'v2' });

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /new version/i })).not.toBeDisabled();
    });
  });

  it('shows a success toast after saving a draft, instead of silent success', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    await loginAndOpenBuilder(user, bot);

    mockedApi.saveDraft.mockResolvedValue({ draft_version_id: 'v2' });

    await user.click(screen.getByRole('button', { name: /new version/i }));

    expect(await screen.findByText('Draft saved')).toBeInTheDocument();
  });

  it('shows an inline JSON error and disables save when the Developer JSON is invalid', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    await loginAndOpenBuilder(user, bot);

    await user.click(screen.getByRole('button', { name: /advanced/i }));
    // The Developer JSON editor now lives under the settings "Advanced" tab.
    await user.click(screen.getByRole('tab', { name: 'Advanced' }));

    const editor = await waitFor(() => {
      const el = document.querySelector('.json-editor');
      expect(el).toBeTruthy();
      return el as HTMLTextAreaElement;
    });

    fireEvent.change(editor, { target: { value: '{ not valid json' } });

    expect(await screen.findByText(/Invalid JSON — fix before saving/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /new version/i })).toBeDisabled();
  });
});
