import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useToasts } from './useToasts';

describe('useToasts', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('adds a toast with an incrementing id and default "success" tone', () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.showToast('first');
    });
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0]).toMatchObject({ id: 'toast-1', message: 'first', tone: 'success' });

    act(() => {
      result.current.showToast('second');
    });
    expect(result.current.toasts[1].id).toBe('toast-2');
  });

  it('respects an explicit tone', () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.showToast('oops', 'error');
    });
    expect(result.current.toasts[0].tone).toBe('error');
  });

  it('dismissToast removes a toast by id', () => {
    const { result } = renderHook(() => useToasts());
    let id = '';
    act(() => {
      id = result.current.showToast('msg', 'info', 0);
    });
    act(() => {
      result.current.dismissToast(id);
    });
    expect(result.current.toasts).toHaveLength(0);
  });

  it('auto-dismisses after the default duration', () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.showToast('auto');
    });
    expect(result.current.toasts).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(result.current.toasts).toHaveLength(0);
  });

  it('durationMs=0 makes the toast sticky (never auto-dismisses)', () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.showToast('sticky', 'info', 0);
    });
    act(() => {
      vi.advanceTimersByTime(1_000_000);
    });
    expect(result.current.toasts).toHaveLength(1);
  });
});
