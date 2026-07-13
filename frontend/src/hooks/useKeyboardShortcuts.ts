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
    function handleKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (target.isContentEditable) return;

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
