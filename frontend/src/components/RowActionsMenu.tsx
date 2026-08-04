import React, { useEffect, useRef, useState } from 'react';
import { MoreVertical } from 'lucide-react';

export type RowAction = {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
};

/** A single "⋮" trigger per row that opens a small menu of actions — replaces a row of
 * always-visible buttons (Edit/Delete, ...) so a table with several per-row actions doesn't
 * turn into a wall of buttons. Closes on outside click, Escape, or scroll.
 *
 * The dropdown is positioned `fixed` from the trigger's own bounding rect (computed on open)
 * rather than `absolute` within the row — most tables in this app live in a `.table-scroll`
 * container (`overflow-x: auto`, which computes overflow-y to auto too), and an absolutely
 * positioned dropdown anchored inside that scroll box gets silently clipped whenever the row
 * sits inside a horizontally-scrolled/short container, even though it's still in the DOM. */
export function RowActionsMenu({ actions }: { actions: RowAction[] }) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<{ top: number; right: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (
        triggerRef.current && !triggerRef.current.contains(e.target as Node) &&
        dropdownRef.current && !dropdownRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    // Any ancestor scroll (table-scroll, page scroll) invalidates the anchored position —
    // simplest correct behavior is to just close, same as clicking outside.
    function onScroll() {
      setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('scroll', onScroll, true);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('scroll', onScroll, true);
    };
  }, [open]);

  function toggle() {
    if (!open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setCoords({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
    }
    setOpen((v) => !v);
  }

  return (
    <div className="row-actions-menu" onClick={(e) => e.stopPropagation()}>
      <button
        ref={triggerRef}
        className="row-actions-trigger"
        aria-label="Row actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggle}
      >
        <MoreVertical size={15} />
      </button>
      {open && coords && (
        <div
          className="row-actions-dropdown"
          role="menu"
          ref={dropdownRef}
          style={{ position: 'fixed', top: coords.top, right: coords.right }}
        >
          {actions.map((a) => (
            <button
              key={a.label}
              role="menuitem"
              className={a.danger ? 'row-actions-item danger' : 'row-actions-item'}
              onClick={() => {
                setOpen(false);
                a.onClick();
              }}
            >
              {a.icon} {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
