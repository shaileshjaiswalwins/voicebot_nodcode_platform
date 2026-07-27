import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfirmDialog } from './ConfirmDialog';

describe('ConfirmDialog', () => {
  it('renders title and description', () => {
    render(
      <ConfirmDialog
        title="Pause campaign?"
        description="New calls will stop dialing until you activate it again."
        confirmLabel="Pause"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />
    );
    expect(screen.getByRole('heading', { name: 'Pause campaign?' })).toBeInTheDocument();
    expect(screen.getByText('New calls will stop dialing until you activate it again.')).toBeInTheDocument();
  });

  it('fires onConfirm when the confirm button is clicked', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        title="Delete node?"
        description="This removes the node and any edges connected to it."
        confirmLabel="Delete"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />
    );
    await user.click(screen.getByRole('button', { name: 'Delete' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('fires onCancel when the cancel button is clicked', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        title="Delete phrase?"
        description="Calls after the next refresh stop detecting this line."
        confirmLabel="Delete"
        onCancel={onCancel}
        onConfirm={vi.fn()}
      />
    );
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('disables both buttons and shows busy label while busy', () => {
    render(
      <ConfirmDialog
        title="Pause campaign?"
        description="..."
        confirmLabel="Pause"
        busyLabel="Pausing…"
        busy
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />
    );
    expect(screen.getByRole('button', { name: 'Pausing…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  });

  it('renders as a danger dialog when tone is danger', () => {
    render(
      <ConfirmDialog
        title="Delete node?"
        description="..."
        confirmLabel="Delete"
        tone="danger"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />
    );
    expect(screen.getByRole('dialog')).toHaveClass('critical');
  });
});
