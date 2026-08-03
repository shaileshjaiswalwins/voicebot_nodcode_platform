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
    // el.isContentEditable is unreliable under jsdom (the test environment) even when the
    // contenteditable attribute is set correctly, so check the attribute directly too —
    // matches real-browser behavior either way and also covers a contenteditable ancestor
    // (e.g. a rich-text child span), not just the exact target element.
    const isEditableEl = (el: HTMLElement) =>
      el.isContentEditable || el.getAttribute('contenteditable') === 'true' || (el as HTMLElement).contentEditable === 'true';
    const isInteractive = (el: HTMLElement | null) =>
      !!el && el instanceof Element && (
        INTERACTIVE.includes(el.tagName) ||
        isEditableEl(el) ||
        el.closest('[contenteditable="true"]') !== null
      );

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
