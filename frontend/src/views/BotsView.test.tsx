import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BotsView } from './BotsView';
import type { Bot } from '../api';

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

describe('BotsView', () => {
  it('renders the EmptyState with a working "Create first agent" action when there are zero bots', async () => {
    const user = userEvent.setup();
    const onNew = vi.fn();
    render(
      <BotsView
        bots={[]}
        transcripts={[]}
        loading={false}
        onSelect={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onNew={onNew}
      />
    );

    expect(screen.getByText('No agents yet')).toBeInTheDocument();
    const button = screen.getByRole('button', { name: 'Create first agent' });
    await user.click(button);
    expect(onNew).toHaveBeenCalledTimes(1);
  });

  it('renders skeleton rows while loading with no bots yet, not the empty state', () => {
    const { container } = render(
      <BotsView
        bots={[]}
        transcripts={[]}
        loading={true}
        onSelect={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
      />
    );
    expect(screen.queryByText('No agents yet')).not.toBeInTheDocument();
    expect(container.querySelectorAll('.skeleton-row').length).toBe(4);
  });

  it('renders bot rows and fires onSelect / onEdit / onDelete', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    const onSelect = vi.fn();
    const onEdit = vi.fn();
    const onDelete = vi.fn();

    render(
      <BotsView
        bots={[bot]}
        transcripts={[]}
        loading={false}
        onSelect={onSelect}
        onEdit={onEdit}
        onDelete={onDelete}
        onNew={vi.fn()}
      />
    );

    expect(screen.getByText('Sales Bot')).toBeInTheDocument();

    await user.click(screen.getByText('Sales Bot'));
    expect(onSelect).toHaveBeenCalledWith('bot-1');

    // Edit/Delete now live behind a single per-row "⋮" menu rather than as always-visible
    // buttons — open it before each action is clickable.
    await user.click(screen.getByRole('button', { name: /row actions/i }));
    await user.click(screen.getByRole('menuitem', { name: /edit/i }));
    expect(onEdit).toHaveBeenCalledWith('bot-1');

    await user.click(screen.getByRole('button', { name: /row actions/i }));
    await user.click(screen.getByRole('menuitem', { name: /delete/i }));
    expect(onDelete).toHaveBeenCalledWith(bot);
  });

  it('shows selected bot details in the right-hand profile panel, including call count', () => {
    const bot = makeBot();
    render(
      <BotsView
        bots={[bot]}
        selectedBot={bot}
        transcripts={[
          { _id: 't1', bot_id: 'bot-1', created_at: new Date().toISOString() },
          { _id: 't2', bot_id: 'other-bot', created_at: new Date().toISOString() },
        ]}
        loading={false}
        onSelect={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
      />
    );
    // Only the one transcript belonging to bot-1 should be counted — both in the table's own
    // Calls column and the right-hand profile panel's "Calls stored" detail.
    expect(screen.getAllByText('1').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /edit agent/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /delete agent/i })).toBeInTheDocument();
  });

  it('does not show the fake hardcoded "Orchestration" field or "local-dev" owner fallback', () => {
    const bot = makeBot({ owner: undefined });
    render(
      <BotsView
        bots={[bot]}
        selectedBot={bot}
        transcripts={[]}
        loading={false}
        onSelect={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
      />
    );
    expect(screen.queryByText('Orchestration')).not.toBeInTheDocument();
    expect(screen.queryByText('prompt_settings')).not.toBeInTheDocument();
    expect(screen.queryByText('local-dev')).not.toBeInTheDocument();
    expect(screen.getByText('Unknown')).toBeInTheDocument();
  });

  it('shows the real owner value when the bot has one', () => {
    const bot = makeBot({ owner: 'admin@justdial.com' });
    render(
      <BotsView
        bots={[bot]}
        selectedBot={bot}
        transcripts={[]}
        loading={false}
        onSelect={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
      />
    );
    expect(screen.getByText('admin@justdial.com')).toBeInTheDocument();
  });

  it('shows a persistent "New agent" button in the panel header once agents already exist', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    const onNew = vi.fn();
    render(
      <BotsView
        bots={[bot]}
        transcripts={[]}
        loading={false}
        onSelect={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onNew={onNew}
      />
    );
    const button = screen.getByRole('button', { name: /new agent/i });
    await user.click(button);
    expect(onNew).toHaveBeenCalledTimes(1);
  });
});
