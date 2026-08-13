import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { AlertsPanel } from './AlertsPanel';
import { api } from '../api';

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

beforeEach(() => {
  vi.mocked(api.listAlertRules).mockResolvedValue([]);
  vi.mocked(api.listAlertIncidents).mockResolvedValue([]);
  vi.mocked(api.createAlertRule).mockImplementation(async (payload) => ({
    ...payload,
    id: 'rule_1',
    threshold_type: 'absolute',
    notify_via: 'in_app',
    created_by: 'admin@justdial.com',
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
});
