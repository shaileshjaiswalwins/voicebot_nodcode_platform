import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { AlertsPanel } from './AlertsPanel';
import { api } from '../api';
import type { AlertRule, AlertIncident } from '../types';

vi.mock('../api', () => ({
  api: {
    listAlertRules: vi.fn(),
    listAlertIncidents: vi.fn(),
    createAlertRule: vi.fn(),
    updateAlertRule: vi.fn(),
    deleteAlertRule: vi.fn(),
    toggleAlertRuleEnabled: vi.fn(),
  },
}));

function makeRule(overrides: Partial<AlertRule> = {}): AlertRule {
  return {
    _id: 'rule_1',
    name: 'Call volume dropped',
    metric: 'call_count',
    threshold_type: 'absolute',
    comparator: 'lt',
    threshold_value: 10,
    window: '1h',
    frequency: '5m',
    filters: { bot_ids: [] },
    notify_via: 'in_app',
    enabled: true,
    created_by: 'admin@acmecorp.com',
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

function makeIncident(overrides: Partial<AlertIncident> = {}): AlertIncident {
  return {
    _id: 'incident_1',
    rule_id: 'rule_1',
    rule_name: 'Call volume dropped',
    bot_ids: [],
    metric: 'call_count',
    current_value: 3,
    threshold_value: 10,
    status: 'open',
    triggered_at: new Date().toISOString(),
    resolved_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(api.listAlertRules).mockResolvedValue([]);
  vi.mocked(api.listAlertIncidents).mockResolvedValue([]);
  vi.mocked(api.createAlertRule).mockImplementation(async (payload) => ({
    ...payload,
    _id: 'rule_1',
    threshold_type: 'absolute',
    notify_via: 'in_app',
    created_by: 'admin@acmecorp.com',
    created_at: new Date().toISOString(),
  }));
});

describe('AlertsPanel create-alert dialog', () => {
  it('clears the call_outcome filter when the metric is switched away from task_completion_rate_pct', async () => {
    render(<AlertsPanel bots={[]} />);

    fireEvent.click(await screen.findByRole('button', { name: /create alert/i }));

    const metricSelect = await screen.findByLabelText(/metric/i);
    fireEvent.change(metricSelect, { target: { value: 'task_completion_rate_pct' } });

    const outcomeInput = await screen.findByPlaceholderText(/leave blank for all outcomes/i);
    fireEvent.change(outcomeInput, { target: { value: 'Approved' } });
    expect(outcomeInput).toHaveValue('Approved');

    // Switch to a metric that doesn't support the call_outcome filter — the field disappears...
    fireEvent.change(metricSelect, { target: { value: 'call_count' } });
    expect(screen.queryByPlaceholderText(/leave blank for all outcomes/i)).not.toBeInTheDocument();

    // ...and the stale value must not be submitted with the created rule.
    fireEvent.change(await screen.findByPlaceholderText(/call volume dropped/i), { target: { value: 'My rule' } });
    const submitButton = screen
      .getAllByRole('button', { name: /create alert/i })
      .find((b) => b.closest('.modal-footer'))!;
    await waitFor(() => expect(submitButton).not.toBeDisabled());
    fireEvent.click(submitButton);

    await waitFor(() => expect(api.createAlertRule).toHaveBeenCalled());
    const payload = vi.mocked(api.createAlertRule).mock.calls[0][0];
    expect(payload.filters.call_outcome).toBeUndefined();
  });

  it('warns that the call_outcome filter is unreachable for bots using Post-Call Analysis fields', async () => {
    render(<AlertsPanel bots={[]} />);

    fireEvent.click(await screen.findByRole('button', { name: /create alert/i }));

    const metricSelect = await screen.findByLabelText(/metric/i);
    fireEvent.change(metricSelect, { target: { value: 'task_completion_rate_pct' } });

    await screen.findByPlaceholderText(/leave blank for all outcomes/i);
    expect(screen.getByText(/records no call_outcome at all/i)).toBeInTheDocument();
  });
});

describe('AlertsPanel rules list', () => {
  it('renders the fetched rules by name', async () => {
    vi.mocked(api.listAlertRules).mockResolvedValue([
      makeRule({ _id: 'rule_1', name: 'Call volume dropped' }),
      makeRule({ _id: 'rule_2', name: 'Latency spike' }),
      makeRule({ _id: 'rule_3', name: 'Error rate high' }),
    ]);

    render(<AlertsPanel bots={[]} />);

    expect(await screen.findByText('Call volume dropped')).toBeInTheDocument();
    expect(screen.getByText('Latency spike')).toBeInTheDocument();
    expect(screen.getByText('Error rate high')).toBeInTheDocument();
  });
});

describe('AlertsPanel history tab', () => {
  it('fetches and renders incidents when switching to the History sub-tab', async () => {
    vi.mocked(api.listAlertIncidents).mockResolvedValue([
      makeIncident({
        _id: 'incident_1',
        rule_name: 'Call volume dropped',
        metric: 'call_count',
        threshold_value: 10,
        current_value: 3,
      }),
    ]);

    render(<AlertsPanel bots={[]} />);
    await waitFor(() => expect(api.listAlertIncidents).toHaveBeenCalled());

    fireEvent.click(await screen.findByRole('button', { name: /history/i }));

    expect(await screen.findByText('Call volume dropped')).toBeInTheDocument();
    expect(screen.getByText(/call_count vs 10/)).toBeInTheDocument();
    expect(screen.getByText(/10 → 3/)).toBeInTheDocument();
  });
});

describe('AlertsPanel create round-trip', () => {
  it('submits the expected payload and adds the new rule to the list', async () => {
    render(<AlertsPanel bots={[]} />);

    fireEvent.click(await screen.findByRole('button', { name: /create alert/i }));
    fireEvent.change(await screen.findByPlaceholderText(/call volume dropped/i), { target: { value: 'New rule' } });

    const submitButton = screen
      .getAllByRole('button', { name: /create alert/i })
      .find((b) => b.closest('.modal-footer'))!;
    await waitFor(() => expect(submitButton).not.toBeDisabled());
    fireEvent.click(submitButton);

    await waitFor(() => expect(api.createAlertRule).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'New rule',
        metric: 'call_count',
        comparator: 'lt',
        threshold_value: 0,
        window: '1h',
        frequency: '5m',
        filters: { bot_ids: [] },
        enabled: true,
      }),
    ));

    expect(await screen.findByText('New rule')).toBeInTheDocument();
  });
});

describe('AlertsPanel edit round-trip', () => {
  it('submits updateAlertRule with the rule id and the changed field', async () => {
    vi.mocked(api.listAlertRules).mockResolvedValue([makeRule({ _id: 'rule_1', name: 'Call volume dropped' })]);
    vi.mocked(api.updateAlertRule).mockImplementation(async (id, payload) => ({
      ...makeRule({ _id: id }),
      ...payload,
    }));

    render(<AlertsPanel bots={[]} />);

    fireEvent.click(await screen.findByText('Call volume dropped'));

    const nameInput = await screen.findByPlaceholderText(/call volume dropped/i);
    fireEvent.change(nameInput, { target: { value: 'Renamed rule' } });

    const submitButton = screen
      .getAllByRole('button', { name: /save changes/i })
      .find((b) => b.closest('.modal-footer'))!;
    fireEvent.click(submitButton);

    await waitFor(() => expect(api.updateAlertRule).toHaveBeenCalledWith(
      'rule_1',
      expect.objectContaining({ name: 'Renamed rule' }),
    ));

    expect(await screen.findByText('Renamed rule')).toBeInTheDocument();
  });
});

describe('AlertsPanel delete round-trip', () => {
  it('calls deleteAlertRule with the rule id and removes it from the list on success', async () => {
    vi.mocked(api.listAlertRules).mockResolvedValue([makeRule({ _id: 'rule_1', name: 'Call volume dropped' })]);
    vi.mocked(api.deleteAlertRule).mockResolvedValue({ ok: true });
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    const { container } = render(<AlertsPanel bots={[]} />);

    expect(await screen.findByText('Call volume dropped')).toBeInTheDocument();
    const deleteButton = container.querySelector('.danger-button') as HTMLButtonElement;
    fireEvent.click(deleteButton);

    await waitFor(() => expect(api.deleteAlertRule).toHaveBeenCalledWith('rule_1'));
    await waitFor(() => expect(screen.queryByText('Call volume dropped')).not.toBeInTheDocument());
  });

  it('does not remove the rule from the list before the delete request resolves', async () => {
    vi.mocked(api.listAlertRules).mockResolvedValue([makeRule({ _id: 'rule_1', name: 'Call volume dropped' })]);
    let resolveDelete: (() => void) | undefined;
    vi.mocked(api.deleteAlertRule).mockImplementation(
      () => new Promise((resolve) => { resolveDelete = () => resolve({ ok: true }); }),
    );
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    const { container } = render(<AlertsPanel bots={[]} />);
    expect(await screen.findByText('Call volume dropped')).toBeInTheDocument();

    const deleteButton = container.querySelector('.danger-button') as HTMLButtonElement;
    fireEvent.click(deleteButton);

    await waitFor(() => expect(api.deleteAlertRule).toHaveBeenCalled());
    // Still present — the list update is deferred until the request resolves.
    expect(screen.getByText('Call volume dropped')).toBeInTheDocument();

    resolveDelete?.();
    await waitFor(() => expect(screen.queryByText('Call volume dropped')).not.toBeInTheDocument());
  });

  it('surfaces the error banner and keeps the rule in the list when delete fails', async () => {
    vi.mocked(api.listAlertRules).mockResolvedValue([makeRule({ _id: 'rule_1', name: 'Call volume dropped' })]);
    vi.mocked(api.deleteAlertRule).mockRejectedValue(new Error('Could not delete this alert.'));
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    const { container } = render(<AlertsPanel bots={[]} />);
    expect(await screen.findByText('Call volume dropped')).toBeInTheDocument();

    const deleteButton = container.querySelector('.danger-button') as HTMLButtonElement;
    fireEvent.click(deleteButton);

    expect(await screen.findByText('Could not delete this alert.')).toBeInTheDocument();
    expect(screen.getByText('Call volume dropped')).toBeInTheDocument();
  });
});

describe('AlertsPanel toggle-enabled round-trip', () => {
  it('calls updateAlertRule with the flipped enabled value', async () => {
    vi.mocked(api.listAlertRules).mockResolvedValue([makeRule({ _id: 'rule_1', name: 'Call volume dropped', enabled: true })]);
    vi.mocked(api.updateAlertRule).mockImplementation(async (id, payload) => ({ ...makeRule({ _id: id }), ...payload }));

    render(<AlertsPanel bots={[]} />);
    const checkbox = await screen.findByRole('checkbox', { name: /disable rule/i });
    fireEvent.click(checkbox);

    await waitFor(() => expect(api.updateAlertRule).toHaveBeenCalledWith(
      'rule_1',
      expect.objectContaining({ enabled: false }),
    ));
  });

  it('does not optimistically flip the checkbox before the request resolves, and shows an error on failure', async () => {
    vi.mocked(api.listAlertRules).mockResolvedValue([makeRule({ _id: 'rule_1', name: 'Call volume dropped', enabled: true })]);
    vi.mocked(api.updateAlertRule).mockRejectedValue(new Error('Could not update this alert.'));

    render(<AlertsPanel bots={[]} />);
    const checkbox = await screen.findByRole('checkbox', { name: /disable rule/i }) as HTMLInputElement;
    fireEvent.click(checkbox);

    expect(await screen.findByText('Could not update this alert.')).toBeInTheDocument();
    expect(checkbox.checked).toBe(true);
  });
});

describe('AlertsPanel list/error states', () => {
  it('shows the error banner when listAlertRules rejects', async () => {
    vi.mocked(api.listAlertRules).mockRejectedValue(new Error('Could not load alerts.'));

    render(<AlertsPanel bots={[]} />);

    expect(await screen.findByText('Could not load alerts.')).toBeInTheDocument();
  });

  it('shows the error banner when createAlertRule rejects, inside the dialog', async () => {
    vi.mocked(api.createAlertRule).mockRejectedValue(new Error('Could not save this alert.'));

    render(<AlertsPanel bots={[]} />);
    fireEvent.click(await screen.findByRole('button', { name: /create alert/i }));
    fireEvent.change(await screen.findByPlaceholderText(/call volume dropped/i), { target: { value: 'New rule' } });

    const submitButton = screen
      .getAllByRole('button', { name: /create alert/i })
      .find((b) => b.closest('.modal-footer'))!;
    await waitFor(() => expect(submitButton).not.toBeDisabled());
    fireEvent.click(submitButton);

    expect(await screen.findByText('Could not save this alert.')).toBeInTheDocument();
  });
});

describe('AlertsPanel window→frequency auto-snap', () => {
  it('snaps an incompatible frequency to the first compatible one when the window changes', async () => {
    render(<AlertsPanel bots={[]} />);
    fireEvent.click(await screen.findByRole('button', { name: /create alert/i }));

    const frequencySelect = await screen.findByLabelText(/check every/i) as HTMLSelectElement;
    const windowSelect = await screen.findByLabelText(/for the last/i) as HTMLSelectElement;

    // Default is window='1h', frequency='5m' (compatible). Pick '1h' frequency, still
    // compatible with '1h' window, then switch window to '5m' — '1h' isn't in
    // WINDOW_FREQUENCY_COMPAT['5m'] (['1m','5m']), so it should snap to '1m'.
    fireEvent.change(frequencySelect, { target: { value: '1h' } });
    expect(frequencySelect.value).toBe('1h');

    fireEvent.change(windowSelect, { target: { value: '5m' } });

    expect(windowSelect.value).toBe('5m');
    expect(frequencySelect.value).toBe('1m');
  });

  it('keeps the current frequency when it is still compatible with the new window', async () => {
    render(<AlertsPanel bots={[]} />);
    fireEvent.click(await screen.findByRole('button', { name: /create alert/i }));

    const frequencySelect = await screen.findByLabelText(/check every/i) as HTMLSelectElement;
    const windowSelect = await screen.findByLabelText(/for the last/i) as HTMLSelectElement;

    // Default frequency is '5m'; switching window to '30m' keeps '5m' since
    // WINDOW_FREQUENCY_COMPAT['30m'] = ['5m', '30m'].
    fireEvent.change(windowSelect, { target: { value: '30m' } });

    expect(windowSelect.value).toBe('30m');
    expect(frequencySelect.value).toBe('5m');
  });
});
