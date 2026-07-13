import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Tooltip } from './Tooltip';

describe('Tooltip', () => {
  it('does not show the tooltip text until hovered', () => {
    render(
      <Tooltip label="Delete this phrase permanently">
        <button>Delete</button>
      </Tooltip>
    );
    expect(screen.queryByText('Delete this phrase permanently')).not.toBeInTheDocument();
  });

  it('shows the tooltip text on hover and hides it on unhover', async () => {
    const user = userEvent.setup();
    render(
      <Tooltip label="Delete this phrase permanently">
        <button>Delete</button>
      </Tooltip>
    );
    await user.hover(screen.getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('Delete this phrase permanently')).toBeInTheDocument();

    await user.unhover(screen.getByRole('button', { name: 'Delete' }));
    expect(screen.queryByText('Delete this phrase permanently')).not.toBeInTheDocument();
  });

  it('shows the tooltip on keyboard focus for accessibility', async () => {
    const user = userEvent.setup();
    render(
      <Tooltip label="Delete this phrase permanently">
        <button>Delete</button>
      </Tooltip>
    );
    await user.tab();
    expect(await screen.findByText('Delete this phrase permanently')).toBeInTheDocument();
  });
});
