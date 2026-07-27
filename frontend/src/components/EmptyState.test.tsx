import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EmptyState } from './EmptyState';

describe('EmptyState', () => {
  it('renders icon, heading, and description', () => {
    render(
      <EmptyState
        icon={<span data-testid="icon">*</span>}
        heading="No campaigns yet"
        description="Create a campaign to start dialing leads."
      />
    );
    expect(screen.getByTestId('icon')).toBeInTheDocument();
    expect(screen.getByText('No campaigns yet')).toBeInTheDocument();
    expect(screen.getByText('Create a campaign to start dialing leads.')).toBeInTheDocument();
  });

  it('does not render an action button when no action is provided', () => {
    render(<EmptyState icon={<span />} heading="Empty" description="Nothing here." />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('renders and fires the action button when provided', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <EmptyState
        icon={<span />}
        heading="No agents yet"
        description="Create your first voice agent."
        action={{ label: 'Create first agent', onClick }}
      />
    );
    const button = screen.getByRole('button', { name: 'Create first agent' });
    await user.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
