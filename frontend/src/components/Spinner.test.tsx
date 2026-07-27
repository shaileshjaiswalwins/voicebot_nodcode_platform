import React from 'react';
import { render, screen } from '@testing-library/react';
import { Spinner } from './Spinner';

describe('Spinner', () => {
  it('renders with an accessible label', () => {
    render(<Spinner label="Saving" />);
    expect(screen.getByRole('status', { name: 'Saving' })).toBeInTheDocument();
  });

  it('defaults to a generic "Loading" label when none is given', () => {
    render(<Spinner />);
    expect(screen.getByRole('status', { name: 'Loading' })).toBeInTheDocument();
  });
});
