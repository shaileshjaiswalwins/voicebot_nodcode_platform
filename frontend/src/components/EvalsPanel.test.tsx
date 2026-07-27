import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EvalsPanel } from './EvalsPanel';
import type { EvalRun } from '../api';

function makeRun(overrides: Partial<EvalRun> = {}): EvalRun {
  return {
    _id: 'run-1',
    bot_id: 'bot-1',
    version_id: 'v1',
    created_at: new Date('2024-01-01T00:00:00Z').toISOString(),
    total: 2,
    passed: 1,
    failed: 1,
    results: [
      { scenario: 'Interested buyer', passed: true, transcript: [{ role: 'caller', text: 'Hi' }], missing_required_phrases: [], forbidden_phrases_found: [] },
      { scenario: 'Not interested caller', passed: false, transcript: [{ role: 'bot', text: 'Sorry to hear' }], missing_required_phrases: ['thank you'], forbidden_phrases_found: ['spam'] },
    ],
    ...overrides,
  };
}

describe('EvalsPanel', () => {
  it('shows the empty-run message when there are no runs and none is in progress', () => {
    render(<EvalsPanel runs={[]} onRun={vi.fn()} running={false} />);
    expect(screen.getByText('No eval runs yet for this draft.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run default evals' })).toBeInTheDocument();
  });

  it('calls onRun with undefined scenarios (default evals) when no custom scenarios are added', async () => {
    const user = userEvent.setup();
    const onRun = vi.fn();
    render(<EvalsPanel runs={[]} onRun={onRun} running={false} />);
    await user.click(screen.getByRole('button', { name: 'Run default evals' }));
    expect(onRun).toHaveBeenCalledWith(undefined);
  });

  it('toggles the scenario editor and runs with valid custom scenarios only', async () => {
    const user = userEvent.setup();
    const onRun = vi.fn();
    render(<EvalsPanel runs={[]} onRun={onRun} running={false} />);

    await user.click(screen.getByRole('button', { name: 'Customize scenarios' }));
    expect(screen.getByText('Hide scenarios')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /add custom scenario/i }));
    await user.type(screen.getByPlaceholderText(/angry repeat caller/i), 'VIP caller');
    await user.type(screen.getByPlaceholderText(/you are a caller who/i), 'You are a VIP customer.');

    const runButton = screen.getByRole('button', { name: 'Run 1 custom scenario(s)' });
    await user.click(runButton);

    expect(onRun).toHaveBeenCalledWith([
      expect.objectContaining({ name: 'VIP caller', caller_persona: 'You are a VIP customer.' }),
    ]);
  });

  it('ignores blank custom scenarios and falls back to default evals label/behavior', async () => {
    const user = userEvent.setup();
    const onRun = vi.fn();
    render(<EvalsPanel runs={[]} onRun={onRun} running={false} />);

    await user.click(screen.getByRole('button', { name: 'Customize scenarios' }));
    await user.click(screen.getByRole('button', { name: /add custom scenario/i }));
    // Leave name/persona blank — should not count as a valid custom scenario.
    expect(screen.getByRole('button', { name: 'Run default evals' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Run default evals' }));
    expect(onRun).toHaveBeenCalledWith(undefined);
  });

  it('disables and relabels the run button while running, and disables when disabled prop is set', () => {
    const { rerender } = render(<EvalsPanel runs={[]} onRun={vi.fn()} running={true} />);
    const runningButton = screen.getByRole('button', { name: 'Running simulation…' });
    expect(runningButton).toBeDisabled();

    rerender(<EvalsPanel runs={[]} onRun={vi.fn()} running={false} disabled={true} />);
    expect(screen.getByRole('button', { name: 'Run default evals' })).toBeDisabled();
  });

  it('renders the latest run summary with pass/fail counts and per-scenario detail, including missing/forbidden phrases', async () => {
    const user = userEvent.setup();
    const run = makeRun();
    render(<EvalsPanel runs={[run]} onRun={vi.fn()} running={false} />);

    expect(screen.getByText('1/2 scenarios passed')).toBeInTheDocument();

    // Expand the failing scenario to reveal detail text (rendered inside a <details>).
    const failingSummary = screen.getByText('Not interested caller');
    await user.click(failingSummary);

    expect(screen.getByText(/Missing required phrases: thank you/)).toBeInTheDocument();
    expect(screen.getByText(/Forbidden phrases found: spam/)).toBeInTheDocument();
  });

  it('lets a custom scenario configure max_turns, defaulting to 4', async () => {
    const user = userEvent.setup();
    const onRun = vi.fn();
    render(<EvalsPanel runs={[]} onRun={onRun} running={false} />);

    await user.click(screen.getByRole('button', { name: 'Customize scenarios' }));
    await user.click(screen.getByRole('button', { name: /add custom scenario/i }));
    await user.type(screen.getByPlaceholderText(/angry repeat caller/i), 'VIP caller');
    await user.type(screen.getByPlaceholderText(/you are a caller who/i), 'You are a VIP customer.');

    const maxTurnsInput = screen.getByLabelText(/max turns/i);
    expect(maxTurnsInput).toHaveValue(4);
    fireEvent.change(maxTurnsInput, { target: { value: '7' } });

    await user.click(screen.getByRole('button', { name: 'Run 1 custom scenario(s)' }));

    expect(onRun).toHaveBeenCalledWith([
      expect.objectContaining({ name: 'VIP caller', max_turns: 7 }),
    ]);
  });

  it('shows a run history list beyond just the latest run, and lets you view an older run', async () => {
    const user = userEvent.setup();
    const older = makeRun({ _id: 'run-0', created_at: new Date('2023-12-01T00:00:00Z').toISOString(), passed: 2, failed: 0, total: 2 });
    const latest = makeRun({ _id: 'run-1' });
    render(<EvalsPanel runs={[latest, older]} onRun={vi.fn()} running={false} />);

    // History list shows both runs, not just the latest.
    const historyEntries = screen.getAllByText(/scenarios passed/);
    expect(historyEntries.length).toBeGreaterThanOrEqual(2);

    // Selecting the older run switches the detail view to it.
    const olderEntry = screen.getByText('2/2 scenarios passed');
    await user.click(olderEntry);
    expect(screen.getAllByText('Interested buyer').length).toBeGreaterThan(0);
  });
});
