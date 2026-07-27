import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ErrorState } from './ErrorState';

describe('ErrorState', () => {
  it('renders a heading and the error message', () => {
    render(<ErrorState heading="Couldn't load analytics" description="Network request failed." />);
    expect(screen.getByText("Couldn't load analytics")).toBeInTheDocument();
    expect(screen.getByText('Network request failed.')).toBeInTheDocument();
  });

  it('has an alert role so screen readers announce it', () => {
    render(<ErrorState heading="Failed" description="Something broke." />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('does not render a retry button when onRetry is not provided', () => {
    render(<ErrorState heading="Failed" description="Something broke." />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('renders and fires a retry button when onRetry is provided', async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(<ErrorState heading="Failed to load" description="Try again." onRetry={onRetry} />);
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
