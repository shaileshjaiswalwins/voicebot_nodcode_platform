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
        loading={false}
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
        loading={true}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
      />
    );
    expect(screen.queryByText('No agents yet')).not.toBeInTheDocument();
    expect(container.querySelectorAll('.skeleton-row').length).toBe(4);
  });

  it('renders bot rows and fires onEdit on row click, and onEdit / onDelete from the row menu', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    const onEdit = vi.fn();
    const onDelete = vi.fn();

    render(
      <BotsView
        bots={[bot]}
        loading={false}
        onEdit={onEdit}
        onDelete={onDelete}
        onNew={vi.fn()}
      />
    );

    expect(screen.getByText('Sales Bot')).toBeInTheDocument();

    // Clicking the row itself opens the agent for editing — there's no separate
    // "select" step now that the side profile panel is gone.
    await user.click(screen.getByText('Sales Bot'));
    expect(onEdit).toHaveBeenCalledWith('bot-1');
    onEdit.mockClear();

    // Edit/Delete also live behind a single per-row "⋮" menu.
    await user.click(screen.getByRole('button', { name: /row actions/i }));
    await user.click(screen.getByRole('menuitem', { name: /edit/i }));
    expect(onEdit).toHaveBeenCalledWith('bot-1');

    await user.click(screen.getByRole('button', { name: /row actions/i }));
    await user.click(screen.getByRole('menuitem', { name: /delete/i }));
    expect(onDelete).toHaveBeenCalledWith(bot);
  });

  it('shows the server-aggregated call count in the table', () => {
    // call_count is now a server-computed field (GET /api/bots aggregates the full
    // tbl_ai_vb_call_transcripts collection) rather than a client-side filter over
    // whatever page of transcripts the Transcripts view happened to have loaded — that
    // client-side count was silently wrong for any bot with more calls than the fetch limit.
    const bot = makeBot({ call_count: 7 });
    render(
      <BotsView
        bots={[bot]}
        loading={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
      />
    );
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('shows 0 calls when call_count is absent', () => {
    const bot = makeBot();
    render(
      <BotsView
        bots={[bot]}
        loading={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
      />
    );
    expect(screen.getByText('0')).toBeInTheDocument();
  });

  it('offers "Duplicate" from the row menu and calls onDuplicate with the bot', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    const onDuplicate = vi.fn();
    render(
      <BotsView
        bots={[bot]}
        loading={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
        onDuplicate={onDuplicate}
      />
    );
    await user.click(screen.getByRole('button', { name: /row actions/i }));
    await user.click(screen.getByRole('menuitem', { name: /duplicate/i }));
    expect(onDuplicate).toHaveBeenCalledWith(bot);
  });

  it('shows a persistent "New agent" button in the panel header once agents already exist', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    const onNew = vi.fn();
    render(
      <BotsView
        bots={[bot]}
        loading={false}
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
