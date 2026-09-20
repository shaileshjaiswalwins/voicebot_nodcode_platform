import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';
import { api } from './api';
import type { Bot, BotVersion } from './api';

// Mirrors App.test.tsx's mock surface — the api module is fully stubbed so these journeys
// exercise routing/workspace wiring, not network behaviour.
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
      transcriptDetail: vi.fn(),
      callEvents: vi.fn(),
      testRecordingLookup: vi.fn(),
      exportTranscriptsCsv: vi.fn(),
      outcomeAnalytics: vi.fn(),
      qualityAlerts: vi.fn(),
      startTestCall: vi.fn(),
      stopTestCall: vi.fn(),
      getPricingAdminConfig: vi.fn().mockResolvedValue({ stt: [], llm: [], tts: [], telephony: [] }),
      updatePricingAdminConfig: vi.fn(),
      pricingMatrix: vi.fn(),
      pricingTiers: vi.fn(),
      pricingBudgetRoute: vi.fn(),
    },
  };
});

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
    active_version_id: 'ver-1',
    ...overrides,
  };
}

function makeVersion(overrides: Partial<BotVersion> = {}): BotVersion {
  return {
    _id: 'ver-1',
    bot_id: 'bot-1',
    version: 1,
    state: 'published',
    config: {},
    created_at: new Date().toISOString(),
    ...overrides,
  } as BotVersion;
}

function mockAuthedDataLoads(bots: Bot[] = []) {
  mockedApi.bots.mockResolvedValue(bots);
  mockedApi.campaigns.mockResolvedValue([]);
  mockedApi.phoneNumbers.mockResolvedValue([]);
  mockedApi.transcripts.mockResolvedValue([]);
  mockedApi.platformSettings.mockResolvedValue({ active_environment: 'dev', dev: {}, prod: {} });
  mockedApi.runtimeSettings.mockResolvedValue({});
  mockedApi.langfuseSettings.mockResolvedValue({});
  mockedApi.phrases.mockResolvedValue([]);
  mockedApi.outcomes.mockResolvedValue([]);
  mockedApi.languageSettings.mockResolvedValue([]);
  mockedApi.listEvals.mockResolvedValue([]);
}

async function signIn(user: ReturnType<typeof userEvent.setup>) {
  mockedApi.login.mockResolvedValue({ token: 'tok-123', email: 'user@acmecorp.com' });
  render(<App />);
  await user.type(screen.getByLabelText(/email/i), 'user@acmecorp.com');
  await user.type(screen.getByLabelText(/password/i), 'correct-password');
  await user.click(screen.getByRole('button', { name: /sign in/i }));
  await waitFor(() => expect(screen.getAllByText('Voice AI Platform').length).toBeGreaterThan(0));
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  window.history.pushState({}, '', '/');
});

describe('Agent workspace', () => {
  it('drops the user straight into the builder after creating an agent, instead of back on the list', async () => {
    const user = userEvent.setup();
    const created = makeBot({ _id: 'bot-new', name: 'Riya — HR Screening' });
    mockAuthedDataLoads([]);
    mockedApi.createBot.mockResolvedValue(created);
    mockedApi.bot.mockResolvedValue({ bot: created, versions: [makeVersion({ bot_id: 'bot-new' })] });

    await signIn(user);

    // Empty state offers the same entry point as the header button.
    await user.click(await screen.findByRole('button', { name: /create first agent/i }));

    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByPlaceholderText(/Acme Outbound/i), 'Riya — HR Screening');
    await user.type(within(dialog).getByPlaceholderText(/Tarun, Priya, Aman/i), 'Riya');

    // The list re-fetch after creation must include the new agent, or selecting it is a no-op.
    mockedApi.bots.mockResolvedValue([created]);
    await user.click(within(dialog).getByRole('button', { name: /create agent/i }));

    // The builder — not the list — is what renders next.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /back to agents/i })).toBeInTheDocument();
    });
    expect(screen.getByRole('tablist', { name: /agent editor/i })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/agents/bot-new');
  });

  it('docks the test rail inside the workspace so the prompt and "Talk to it" are on screen together', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    mockAuthedDataLoads([bot]);
    mockedApi.bot.mockResolvedValue({ bot, versions: [makeVersion()] });

    await signIn(user);
    await user.click(await screen.findByText('Sales Bot'));

    const rail = await screen.findByRole('complementary', { name: /test call/i });
    expect(within(rail).getByRole('button', { name: /talk to it/i })).toBeInTheDocument();
    // The rail scopes itself to the agent being edited — no agent picker to re-select.
    expect(within(rail).queryByLabelText(/^agent$/i)).not.toBeInTheDocument();
    // Builder is still mounted alongside it.
    expect(screen.getByRole('tablist', { name: /agent editor/i })).toBeInTheDocument();
  });

  it('no longer advertises Test Call as a destination in the sidebar', async () => {
    const user = userEvent.setup();
    mockAuthedDataLoads([makeBot()]);
    await signIn(user);

    const sidebar = screen.getByRole('navigation');
    expect(within(sidebar).queryByRole('button', { name: /test call/i })).not.toBeInTheDocument();
    expect(within(sidebar).getByRole('button', { name: /agents/i })).toBeInTheDocument();
  });

  it('redirects the retired /test URL into the agent workspace rather than rendering nothing', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    mockAuthedDataLoads([bot]);
    mockedApi.bot.mockResolvedValue({ bot, versions: [makeVersion()] });
    window.history.pushState({}, '', '/test');

    await signIn(user);

    await waitFor(() => expect(window.location.pathname).toBe('/agents/bot-1'));
    expect(await screen.findByRole('complementary', { name: /test call/i })).toBeInTheDocument();
  });
});
