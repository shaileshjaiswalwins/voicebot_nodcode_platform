import { useEffect, useRef } from 'react';
import type { View } from '../types';
import { SHORTCUT_MAP } from '../constants/ui';

export function useKeyboardShortcuts(
  navigate: (v: View) => void,
  toggleShortcuts: () => void,
  closeModal: () => void
) {
  // Store callbacks in refs so the event listener is registered once and always
  // calls the latest version — avoids re-registering on every render.
  const navigateRef = useRef(navigate);
  const toggleRef = useRef(toggleShortcuts);
  const closeRef = useRef(closeModal);
  navigateRef.current = navigate;
  toggleRef.current = toggleShortcuts;
  closeRef.current = closeModal;

  useEffect(() => {
    // A bare-key nav shortcut must never fire while the user is interacting with a control —
    // a stray key (e.g. 'c') would navigate away mid-edit and lose in-progress work. Guard
    // both the event target AND the actually-focused element, and include BUTTON/A/OPTION
    // (e.g. right after clicking "Add function"/"New key value pair", focus is on the button,
    // so typing the field value would otherwise hit the button and trigger navigation).
    const INTERACTIVE = ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A', 'OPTION'];
    const isInteractive = (el: HTMLElement | null) =>
      !!el && (INTERACTIVE.includes(el.tagName) || el.isContentEditable);

    function handleKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const active = document.activeElement as HTMLElement | null;
      if (isInteractive(target) || isInteractive(active)) return;

      if (e.key === 'Escape') { closeRef.current(); return; }
      if (e.key === '?') { toggleRef.current(); return; }
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const key = e.key.toLowerCase();
      const match = SHORTCUT_MAP.find(s => s.key === key);
      if (match) { e.preventDefault(); navigateRef.current(match.view); }
    }

    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, []); // registered once, callbacks always current via refs
}
