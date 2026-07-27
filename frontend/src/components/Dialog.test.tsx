import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Dialog } from './Dialog';

describe('Dialog', () => {
  it('renders title, icon, children, and footer', () => {
    render(
      <Dialog title="Version diff" icon={<span data-testid="icon" />} onClose={vi.fn()} footer={<button>Close</button>}>
        <p>Body content</p>
      </Dialog>
    );
    expect(screen.getByRole('heading', { name: 'Version diff' })).toBeInTheDocument();
    expect(screen.getByTestId('icon')).toBeInTheDocument();
    expect(screen.getByText('Body content')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
  });

  it('has dialog role and aria-modal', () => {
    render(<Dialog title="Test" onClose={vi.fn()}>content</Dialog>);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
  });

  it('calls onClose when the Escape key is pressed', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Dialog title="Test" onClose={onClose}>content</Dialog>);
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when the backdrop is clicked but not when the panel is clicked', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Dialog title="Test" onClose={onClose}>content</Dialog>);
    await user.click(screen.getByText('content'));
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole('presentation'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('applies the critical class when tone is danger', () => {
    render(<Dialog title="Danger" onClose={vi.fn()} tone="danger">content</Dialog>);
    expect(screen.getByRole('dialog')).toHaveClass('critical');
  });

  it('does not call onClose on Escape or backdrop click when closeOnBackdrop/onEscape are disabled', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Dialog title="Busy" onClose={onClose} closeOnBackdrop={false} closeOnEscape={false}>
        content
      </Dialog>
    );
    await user.click(screen.getByRole('presentation'));
    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
  });
});
