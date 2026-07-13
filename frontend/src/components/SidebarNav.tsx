import React, { useEffect, useRef } from 'react';

// Wraps the sidebar <nav> and drives the --proximity CSS variable on each
// child based on how close the pointer is. Each item independently scales
// and shifts — items far from the cursor stay still, nearby ones lift.
export function SidebarNav({ children }: { children: React.ReactNode }) {
  const navRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;

    // Radius (px) within which items respond. Beyond this they get proximity=0.
    const RADIUS = 100;

    function handleMove(e: PointerEvent) {
      const items = nav!.querySelectorAll<HTMLElement>('.nav-item');
      items.forEach(el => {
        const rect = el.getBoundingClientRect();
        const centerY = rect.top + rect.height / 2;
        const dist = Math.abs(e.clientY - centerY);
        const proximity = Math.max(0, 1 - dist / RADIUS);
        el.style.setProperty('--proximity', proximity.toFixed(3));
      });
    }

    function handleLeave() {
      nav!.querySelectorAll<HTMLElement>('.nav-item').forEach(el => {
        el.style.setProperty('--proximity', '0');
      });
    }

    nav.addEventListener('pointermove', handleMove);
    nav.addEventListener('pointerleave', handleLeave);
    return () => {
      nav.removeEventListener('pointermove', handleMove);
      nav.removeEventListener('pointerleave', handleLeave);
    };
  }, []);

  return (
    <nav className="sidebar-nav" ref={navRef}>
      {children}
    </nav>
  );
}
