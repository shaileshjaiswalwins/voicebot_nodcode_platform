import React from 'react';
import { Keyboard } from 'lucide-react';
import { SHORTCUT_MAP } from '../constants/ui';

export function ShortcutsModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 420 }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <Keyboard size={18} />
          <h2>Keyboard shortcuts</h2>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div style={{ padding: '16px 20px 20px' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {SHORTCUT_MAP.map(s => (
                <tr key={s.key} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 0', width: 80 }}><kbd className="kbd">{s.key.toUpperCase()}</kbd></td>
                  <td style={{ padding: '8px 0', color: 'var(--text-2)' }}>{s.label}</td>
                </tr>
              ))}
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '8px 0' }}><kbd className="kbd">Esc</kbd></td>
                <td style={{ padding: '8px 0', color: 'var(--text-2)' }}>Close modal</td>
              </tr>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '8px 0' }}><kbd className="kbd">⌘S</kbd></td>
                <td style={{ padding: '8px 0', color: 'var(--text-2)' }}>Save draft (in builder)</td>
              </tr>
              <tr>
                <td style={{ padding: '8px 0' }}><kbd className="kbd">?</kbd></td>
                <td style={{ padding: '8px 0', color: 'var(--text-2)' }}>Toggle this help</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
