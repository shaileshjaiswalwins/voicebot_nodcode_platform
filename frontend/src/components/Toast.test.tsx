import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderHook, act } from '@testing-library/react';
import { ToastContainer } from './Toast';
import { useToasts } from '../hooks/useToasts';

describe('useToasts + ToastContainer', () => {
  it('shows a toast when showToast is called and it disappears after the timeout', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.showToast('Phrase deleted');
    });
    expect(result.current.toasts).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(result.current.toasts).toHaveLength(0);
    vi.useRealTimers();
  });

  it('renders toast messages and respects tone classes', () => {
    const toasts = [
      { id: '1', message: 'Campaign paused', tone: 'success' as const },
      { id: '2', message: 'Could not save', tone: 'error' as const },
    ];
    render(<ToastContainer toasts={toasts} onDismiss={vi.fn()} />);
    expect(screen.getByText('Campaign paused')).toBeInTheDocument();
    expect(screen.getByText('Could not save')).toBeInTheDocument();
  });

  it('dismisses a toast when its close button is clicked', async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    render(<ToastContainer toasts={[{ id: '1', message: 'Saved', tone: 'success' }]} onDismiss={onDismiss} />);
    await user.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalledWith('1');
  });

  it('announces toasts to screen readers via a status role', () => {
    render(<ToastContainer toasts={[{ id: '1', message: 'Saved', tone: 'success' }]} onDismiss={vi.fn()} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
});
