import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AnalyticsView } from './AnalyticsView';
import { api } from '../api';
import type { OutcomeAnalytics, QualityAlert } from '../api';

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return {
    ...actual,
    api: {
      outcomeAnalytics: vi.fn(),
      qualityAlerts: vi.fn(),
    },
  };
});

const mockedApi = vi.mocked(api, true);

function makeAnalytics(overrides: Partial<OutcomeAnalytics> = {}): OutcomeAnalytics {
  return {
    total: 10,
    by_status: { completed: 8, failed: 2 },
    by_outcome: { interested: 5 },
    ended_naturally: 7,
    avg_duration_sec: 42,
    ...overrides,
  };
}

function makeAlert(overrides: Partial<QualityAlert> = {}): QualityAlert {
  return { alert: false, message: '', hours: 1, total_calls: 10, bad_calls: 1, bad_pct: 10, ...overrides };
}

describe('AnalyticsView', () => {
  it('shows an error state with a retry action when loading analytics fails', async () => {
    const user = userEvent.setup();
    mockedApi.outcomeAnalytics.mockRejectedValueOnce(new Error('Network request failed'));
    mockedApi.qualityAlerts.mockRejectedValueOnce(new Error('Network request failed'));

    render(<AnalyticsView bots={[]} campaigns={[]} />);

    await waitFor(() => expect(screen.getByText("Couldn't load analytics")).toBeInTheDocument());
    expect(screen.getByText('Network request failed')).toBeInTheDocument();

    mockedApi.outcomeAnalytics.mockResolvedValueOnce(makeAnalytics());
    mockedApi.qualityAlerts.mockResolvedValueOnce(makeAlert());
    await user.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(screen.queryByText("Couldn't load analytics")).not.toBeInTheDocument());
    expect(screen.getByText('42s')).toBeInTheDocument();
  });

  it('renders analytics data with no error state when the load succeeds', async () => {
    mockedApi.outcomeAnalytics.mockResolvedValueOnce(makeAnalytics());
    mockedApi.qualityAlerts.mockResolvedValueOnce(makeAlert());

    render(<AnalyticsView bots={[]} campaigns={[]} />);

    await waitFor(() => expect(screen.getByText('42s')).toBeInTheDocument());
    expect(screen.queryByText("Couldn't load analytics")).not.toBeInTheDocument();
  });

  it('shows the quality alert status as accessible text, not an emoji-only indicator', async () => {
    mockedApi.outcomeAnalytics.mockResolvedValueOnce(makeAnalytics());
    mockedApi.qualityAlerts.mockResolvedValueOnce(makeAlert({ alert: true }));

    render(<AnalyticsView bots={[]} campaigns={[]} />);

    await waitFor(() => expect(screen.getByText('alert')).toBeInTheDocument());
    expect(screen.queryByText(/🔴|🟢/)).not.toBeInTheDocument();
  });

  it('explains "Ended naturally" via a tooltip instead of leaving it unexplained', async () => {
    const user = userEvent.setup();
    mockedApi.outcomeAnalytics.mockResolvedValueOnce(makeAnalytics());
    mockedApi.qualityAlerts.mockResolvedValueOnce(makeAlert());

    render(<AnalyticsView bots={[]} campaigns={[]} />);
    await waitFor(() => expect(screen.getByText('Ended naturally')).toBeInTheDocument());

    expect(screen.queryByText(/Calls that completed normally/)).not.toBeInTheDocument();
    await user.hover(screen.getByText('Ended naturally').parentElement!.querySelector('svg')!);
    expect(await screen.findByText(/Calls that completed normally/)).toBeInTheDocument();
  });
});
