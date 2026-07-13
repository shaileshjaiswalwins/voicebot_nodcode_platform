import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DeleteAgentDialog } from './BotsView';
import type { Bot } from '../api';

function makeBot(overrides: Partial<Bot> = {}): Bot {
  return {
    _id: 'bot-1',
    name: 'Sales Bot',
    description: '',
    assistant_id: 'asst-1',
    status: 'active',
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

describe('DeleteAgentDialog (migrated onto shared Dialog)', () => {
  it('requires typing the exact bot name before the delete button is enabled', async () => {
    const user = userEvent.setup();
    const bot = makeBot();
    render(<DeleteAgentDialog bot={bot} campaigns={[]} transcriptCount={0} busy={false} onCancel={vi.fn()} onConfirm={vi.fn()} />);

    const deleteButton = screen.getByRole('button', { name: /delete agent/i });
    expect(deleteButton).toBeDisabled();

    await user.type(screen.getByPlaceholderText('Sales Bot'), 'Sales Bot');
    expect(deleteButton).not.toBeDisabled();
  });

  it('closes when Escape is pressed (inherited from the shared Dialog primitive)', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<DeleteAgentDialog bot={makeBot()} campaigns={[]} transcriptCount={0} busy={false} onCancel={onCancel} onConfirm={vi.fn()} />);
    await user.keyboard('{Escape}');
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('does not close on Escape while a delete is already in progress', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<DeleteAgentDialog bot={makeBot()} campaigns={[]} transcriptCount={0} busy onCancel={onCancel} onConfirm={vi.fn()} />);
    await user.keyboard('{Escape}');
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('shows blast-radius warnings for assigned campaigns and orphaned transcripts', () => {
    const bot = makeBot();
    render(
      <DeleteAgentDialog
        bot={bot}
        campaigns={[{ _id: 'c1', campaign_key: 'ck', name: 'Summer Sale', bot_id: 'bot-1' } as any]}
        transcriptCount={3}
        busy={false}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />
    );
    expect(screen.getByText(/Summer Sale/)).toBeInTheDocument();
    expect(screen.getByText(/3 transcripts/)).toBeInTheDocument();
  });
});
