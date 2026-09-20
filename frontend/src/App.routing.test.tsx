import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
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
    description: 'Outbound sales agent',
    assistant_id: 'asst-1',
    status: 'active',
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

function mockAuthedDataLoads() {
  mockedApi.campaigns.mockResolvedValue([]);
  mockedApi.transcripts.mockResolvedValue([]);
  mockedApi.platformSettings.mockResolvedValue({ active_environment: 'dev', dev: {}, prod: {} });
  mockedApi.runtimeSettings.mockResolvedValue({});
  mockedApi.langfuseSettings.mockResolvedValue({});
  mockedApi.phrases.mockResolvedValue([]);
  mockedApi.outcomes.mockResolvedValue([]);
  mockedApi.languageSettings.mockResolvedValue([]);
  mockedApi.exportTranscriptsCsv.mockResolvedValue(new Blob(['']));
}

async function login(user: ReturnType<typeof userEvent.setup>) {
  mockedApi.login.mockResolvedValue({ token: 'tok-123', email: 'user@voicedesk.com' });
  mockAuthedDataLoads();
  mockedApi.bots.mockResolvedValue([makeBot()]);

  render(<App />);
  await user.type(screen.getByLabelText(/email/i), 'user@voicedesk.com');
  await user.type(screen.getByLabelText(/password/i), 'correct-password');
  await user.click(screen.getByRole('button', { name: /sign in/i }));
  await screen.findByText('Sales Bot');
}

beforeEach(() => {
  window.history.pushState({}, '', '/');
});

describe('App routing', () => {
  it('lands on /agents by default and shows a document title for it', async () => {
    const user = userEvent.setup();
    await login(user);
    await waitFor(() => expect(window.location.pathname).toBe('/agents'));
    expect(document.title).toMatch(/Agents/);
  });

  it('updates the URL when navigating to a different top-level view via the sidebar', async () => {
    const user = userEvent.setup();
    await login(user);

    await user.click(screen.getByRole('button', { name: /^campaigns$/i }));
    await waitFor(() => expect(window.location.pathname).toBe('/campaigns'));
    expect(document.title).toMatch(/Campaigns/);

    await user.click(screen.getByRole('button', { name: /analytics/i }));
    await waitFor(() => expect(window.location.pathname).toBe('/analytics'));
  });

  it('navigates back to the previous view when the browser back button is used', async () => {
    const user = userEvent.setup();
    await login(user);

    await user.click(screen.getByRole('button', { name: /^campaigns$/i }));
    await waitFor(() => expect(window.location.pathname).toBe('/campaigns'));

    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe('/agents'));
    await waitFor(() => expect(screen.getByText('Sales Bot')).toBeInTheDocument());
  });

  it('puts a bot id in the URL when opening the builder for that agent', async () => {
    const user = userEvent.setup();
    mockedApi.bot.mockResolvedValue({
      bot: makeBot(),
      versions: [{ _id: 'v1', bot_id: 'bot-1', version: 1, state: 'draft', config: { system_prompt: 'hi' }, created_at: new Date().toISOString() }],
    });
    mockedApi.listEvals.mockResolvedValue([]);
    await login(user);

    // Edit/Delete now live behind a single per-row "⋮" menu (RowActionsMenu).
    await user.click(screen.getByRole('button', { name: /row actions/i }));
    await user.click(await screen.findByRole('menuitem', { name: /^edit$/i }));
    await waitFor(() => expect(window.location.pathname).toBe('/agents/bot-1'));
  });

  it('renders the correct view when navigating directly to a URL (deep link / refresh)', async () => {
    window.history.pushState({}, '', '/settings');
    const user = userEvent.setup();
    mockedApi.login.mockResolvedValue({ token: 'tok-123', email: 'user@voicedesk.com' });
    mockAuthedDataLoads();
    mockedApi.bots.mockResolvedValue([makeBot()]);

    render(<App />);
    await user.type(screen.getByLabelText(/email/i), 'user@voicedesk.com');
    await user.type(screen.getByLabelText(/password/i), 'correct-password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => expect(screen.getByText('Runtime settings')).toBeInTheDocument());
    expect(window.location.pathname).toBe('/settings');
  });
});
