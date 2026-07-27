import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { CopyableId } from './CopyableId';
import { FEEDBACK_TIMEOUT_MS } from '../constants/ui';

describe('CopyableId', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    });
  });

  it('shows a check icon after copying and reverts after the shared feedback timeout', async () => {
    vi.useFakeTimers();
    render(<CopyableId value="abc123" />);

    fireEvent.click(screen.getByText('abc123'));
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('.copyable-id-icon svg.lucide-check')).toBeTruthy();

    await vi.advanceTimersByTimeAsync(FEEDBACK_TIMEOUT_MS - 100);
    expect(document.querySelector('.copyable-id-icon svg.lucide-check')).toBeTruthy();

    await vi.advanceTimersByTimeAsync(200);
    expect(document.querySelector('.copyable-id-icon svg.lucide-check')).toBeFalsy();
    vi.useRealTimers();
  });

  it('renders a dash placeholder when there is no value', () => {
    render(<CopyableId value="" />);
    expect(screen.getByText('-')).toBeInTheDocument();
  });
});
