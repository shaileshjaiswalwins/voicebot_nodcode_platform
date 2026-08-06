import React, { useState } from 'react';
import { Link2, Phone } from 'lucide-react';
import type { Bot as BotType, NumberMapping, PhoneNumber } from '../api';
import { PhoneNumbersView, type CreatePayload, type UpdatePayload } from './PhoneNumbersView';
import { NumberMappingView } from './NumberMappingView';

type NumbersTab = 'numbers' | 'mapping';

/** Merges the two number-management screens (SIP/trunk CRUD and number-to-agent
 * assignment) into one nav item with two tabs — PMs experience these as one concept
 * ("which number rings which bot"), and NumberMappingView already cross-linked to
 * PhoneNumbersView (and vice versa) before this merge because you often need both in
 * the same task. */
export function NumbersView({
  phoneNumbers,
  bots,
  loadingPhoneNumbers,
  onCreate,
  createState,
  onUpdate,
  updateState,
  onReassign,
  reassignState,
  onDelete,
  mappings,
  loadingMappings,
  onMap,
  mapState,
}: {
  phoneNumbers: PhoneNumber[];
  bots: BotType[];
  loadingPhoneNumbers?: boolean;
  onCreate: (payload: CreatePayload) => Promise<void>;
  createState?: 'idle' | 'running' | 'failed';
  onUpdate: (id: string, payload: UpdatePayload) => Promise<void>;
  updateState?: 'idle' | 'running' | 'failed';
  onReassign: (id: string, botId: string) => void;
  reassignState?: Record<string, 'idle' | 'running' | 'failed'>;
  onDelete: (id: string) => void;
  mappings: NumberMapping[];
  loadingMappings?: boolean;
  onMap: (botId: string, phoneNumber: string | null) => void;
  mapState?: Record<string, 'idle' | 'running' | 'failed'>;
}) {
  const [tab, setTab] = useState<NumbersTab>('numbers');

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div className="cf-tabbar" role="tablist" style={{ display: 'flex', gap: '0.25rem', borderBottom: '1px solid var(--border)', marginBottom: '0.25rem' }}>
        {([
          { id: 'numbers' as const, label: 'Numbers', icon: <Phone size={15} /> },
          { id: 'mapping' as const, label: 'Mapping', icon: <Link2 size={15} /> },
        ]).map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? 'cf-tab active' : 'cf-tab'}
            onClick={() => setTab(t.id)}
            style={{
              display: 'flex', alignItems: 'center', gap: '0.4rem',
              padding: '0.45rem 0.85rem', border: 'none', background: 'none', cursor: 'pointer',
              fontWeight: tab === t.id ? 700 : 500,
              borderBottom: tab === t.id ? '2px solid var(--primary, #116db6)' : '2px solid transparent',
              color: tab === t.id ? 'var(--primary, #116db6)' : 'var(--muted)',
            }}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {tab === 'numbers' && (
        <PhoneNumbersView
          phoneNumbers={phoneNumbers}
          bots={bots}
          loading={loadingPhoneNumbers}
          onCreate={onCreate}
          createState={createState}
          onUpdate={onUpdate}
          updateState={updateState}
          onReassign={onReassign}
          reassignState={reassignState}
          onDelete={onDelete}
          onGoToNumberMapping={() => setTab('mapping')}
        />
      )}
      {tab === 'mapping' && (
        <NumberMappingView
          mappings={mappings}
          bots={bots}
          loading={loadingMappings}
          onMap={onMap}
          mapState={mapState}
          onGoToPhoneNumbers={() => setTab('numbers')}
        />
      )}
    </section>
  );
}
