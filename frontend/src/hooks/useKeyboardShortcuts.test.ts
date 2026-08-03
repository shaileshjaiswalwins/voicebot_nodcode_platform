import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useKeyboardShortcuts } from './useKeyboardShortcuts';

function fireKey(key: string, opts: Partial<KeyboardEventInit> = {}, target?: EventTarget) {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...opts });
  if (target) Object.defineProperty(event, 'target', { value: target, configurable: true });
  window.dispatchEvent(event);
}

describe('useKeyboardShortcuts', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('navigates on a bare mapped key press', () => {
    const navigate = vi.fn();
    renderHook(() => useKeyboardShortcuts(navigate, vi.fn(), vi.fn()));
    fireKey('b');
    expect(navigate).toHaveBeenCalledWith('bots');
  });

  it('does nothing for an unmapped key', () => {
    const navigate = vi.fn();
    renderHook(() => useKeyboardShortcuts(navigate, vi.fn(), vi.fn()));
    fireKey('z');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('calls toggleShortcuts on "?"', () => {
    const toggle = vi.fn();
    renderHook(() => useKeyboardShortcuts(vi.fn(), toggle, vi.fn()));
    fireKey('?');
    expect(toggle).toHaveBeenCalled();
  });

  it('calls closeModal on "Escape"', () => {
    const close = vi.fn();
    renderHook(() => useKeyboardShortcuts(vi.fn(), vi.fn(), close));
    fireKey('Escape');
    expect(close).toHaveBeenCalled();
  });

  it('ignores navigation shortcuts when a modifier key is held', () => {
    const navigate = vi.fn();
    renderHook(() => useKeyboardShortcuts(navigate, vi.fn(), vi.fn()));
    fireKey('b', { metaKey: true });
    fireKey('b', { ctrlKey: true });
    fireKey('b', { altKey: true });
    expect(navigate).not.toHaveBeenCalled();
  });

  it('is case-insensitive for the shortcut key', () => {
    const navigate = vi.fn();
    renderHook(() => useKeyboardShortcuts(navigate, vi.fn(), vi.fn()));
    fireKey('B');
    expect(navigate).toHaveBeenCalledWith('bots');
  });

  it('does not navigate when the event target is an interactive element (INPUT)', () => {
    const input = document.createElement('input');
    document.body.appendChild(input);
    const navigate = vi.fn();
    renderHook(() => useKeyboardShortcuts(navigate, vi.fn(), vi.fn()));
    fireKey('b', {}, input);
    expect(navigate).not.toHaveBeenCalled();
  });

  it('does not navigate when the event target is BUTTON even if focus moved there mid-edit', () => {
    const button = document.createElement('button');
    document.body.appendChild(button);
    const navigate = vi.fn();
    renderHook(() => useKeyboardShortcuts(navigate, vi.fn(), vi.fn()));
    fireKey('b', {}, button);
    expect(navigate).not.toHaveBeenCalled();
  });

  it('does not navigate when the currently focused element is interactive, even if the event target is not', () => {
    const textarea = document.createElement('textarea');
    document.body.appendChild(textarea);
    textarea.focus();
    const navigate = vi.fn();
    renderHook(() => useKeyboardShortcuts(navigate, vi.fn(), vi.fn()));
    fireKey('b', {}, document.body);
    expect(navigate).not.toHaveBeenCalled();
  });

  it('does not navigate when the target is contentEditable', () => {
    const div = document.createElement('div');
    div.contentEditable = 'true';
    document.body.appendChild(div);
    const navigate = vi.fn();
    renderHook(() => useKeyboardShortcuts(navigate, vi.fn(), vi.fn()));
    fireKey('b', {}, div);
    expect(navigate).not.toHaveBeenCalled();
  });

  it('always uses the latest callback references without re-registering the listener', () => {
    const navigate1 = vi.fn();
    const navigate2 = vi.fn();
    const { rerender } = renderHook(
      ({ nav }) => useKeyboardShortcuts(nav, vi.fn(), vi.fn()),
      { initialProps: { nav: navigate1 } },
    );
    rerender({ nav: navigate2 });
    fireKey('b');
    expect(navigate1).not.toHaveBeenCalled();
    expect(navigate2).toHaveBeenCalledWith('bots');
  });

  it('removes the listener on unmount', () => {
    const navigate = vi.fn();
    const { unmount } = renderHook(() => useKeyboardShortcuts(navigate, vi.fn(), vi.fn()));
    unmount();
    fireKey('b');
    expect(navigate).not.toHaveBeenCalled();
  });
});
