import React, { useEffect, useState } from 'react';
import { Plus, Save, Trash2 } from 'lucide-react';
import type { PricingConfig, PricingModelEntry } from '../api';
import { FEEDBACK_TIMEOUT_MS } from '../constants/ui';

function slugify(label: string): string {
  return label.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

function emptyEntry(company = ''): PricingModelEntry {
  return { key: '', label: '', cost_inr_per_min: 0, company };
}

const UNGROUPED = 'Other';

/** One editable row-list for a pricing category (STT/LLM/TTS/Telephony), grouped by
 * `company` so it matches the two-level Company → Model picker in the Agent Builder's
 * LLM tab. `withEstimates` shows the extra latency/token fields — only meaningful for LLM
 * entries, which drive the per-bot Agent details card's Latency/Tokens rows (see
 * utils/agentCost.ts). */
function PricingCategoryEditor({
  title, hint, entries, onChange, withEstimates,
}: {
  title: string;
  hint: string;
  entries: PricingModelEntry[];
  onChange: (next: PricingModelEntry[]) => void;
  withEstimates?: boolean;
}) {
  function updateRow(index: number, patch: Partial<PricingModelEntry>) {
    const next = entries.slice();
    const row = { ...next[index], ...patch };
    if (patch.label !== undefined) row.key = slugify(patch.label);
    next[index] = row;
    onChange(next);
  }

  function addRow() {
    onChange([...entries, emptyEntry()]);
  }

  function removeRow(index: number) {
    onChange(entries.filter((_, i) => i !== index));
  }

  const knownCompanies = Array.from(new Set(entries.map((e) => e.company).filter((c): c is string => Boolean(c)))).sort();
  const groups = new Map<string, number[]>();
  entries.forEach((e, i) => {
    const company = e.company || UNGROUPED;
    if (!groups.has(company)) groups.set(company, []);
    groups.get(company)!.push(i);
  });
  const groupNames = Array.from(groups.keys()).sort((a, b) => (a === UNGROUPED ? 1 : b === UNGROUPED ? -1 : a.localeCompare(b)));

  return (
    <div className="panel compact">
      <div className="panel-header">
        <div>
          <h2>{title}</h2>
          <p>{hint}</p>
        </div>
        <button onClick={addRow}><Plus size={14} /> Add {title}</button>
      </div>
      {entries.length === 0 && <p className="muted">No entries yet.</p>}
      {groupNames.map((company) => (
        <div className="pricing-company-group" key={company}>
          <h3 className="pricing-company-heading">{company}</h3>
          <div className="pricing-row-list">
            {groups.get(company)!.map((i) => {
              const entry = entries[i];
              return (
          <div className="pricing-row" key={i}>
            <label className="pricing-row-field">
              Company
              <input
                list="pricing-company-options"
                value={entry.company || ''}
                placeholder="e.g. Google"
                onChange={(e) => updateRow(i, { company: e.target.value })}
              />
            </label>
            <input
              className="pricing-row-label"
              value={entry.label}
              placeholder="Display name (e.g. gpt-5.1)"
              onChange={(e) => updateRow(i, { label: e.target.value })}
            />
            <label className="pricing-row-field">
              ₹/min
              <input
                type="number"
                step="0.01"
                min={0}
                value={entry.cost_inr_per_min}
                onChange={(e) => updateRow(i, { cost_inr_per_min: Number(e.target.value) })}
              />
            </label>
            {withEstimates && (
              <>
                <label className="pricing-row-field">
                  Latency min (ms)
                  <input
                    type="number"
                    min={0}
                    value={entry.latency_ms_min ?? ''}
                    onChange={(e) => updateRow(i, { latency_ms_min: e.target.value === '' ? undefined : Number(e.target.value) })}
                  />
                </label>
                <label className="pricing-row-field">
                  Latency max (ms)
                  <input
                    type="number"
                    min={0}
                    value={entry.latency_ms_max ?? ''}
                    onChange={(e) => updateRow(i, { latency_ms_max: e.target.value === '' ? undefined : Number(e.target.value) })}
                  />
                </label>
                <label className="pricing-row-field">
                  Tokens min
                  <input
                    type="number"
                    min={0}
                    value={entry.tokens_min ?? ''}
                    onChange={(e) => updateRow(i, { tokens_min: e.target.value === '' ? undefined : Number(e.target.value) })}
                  />
                </label>
                <label className="pricing-row-field">
                  Tokens max
                  <input
                    type="number"
                    min={0}
                    value={entry.tokens_max ?? ''}
                    onChange={(e) => updateRow(i, { tokens_max: e.target.value === '' ? undefined : Number(e.target.value) })}
                  />
                </label>
              </>
            )}
            <button className="fallback-button" title="Remove" onClick={() => removeRow(i)}>
              <Trash2 size={14} />
            </button>
          </div>
              );
            })}
          </div>
        </div>
      ))}
      <datalist id="pricing-company-options">
        {Array.from(new Set([...knownCompanies, 'Google', 'OpenAI', 'Anthropic', 'Sarvam', 'Deepgram', 'ElevenLabs', 'Cartesia', 'Plivo', 'Platform'])).map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
    </div>
  );
}

export function AdminView({
  config,
  onSave,
}: {
  config: PricingConfig | null;
  onSave: (config: PricingConfig) => Promise<void>;
}) {
  const [draft, setDraft] = useState<PricingConfig | null>(config);
  const [saveState, setSaveState] = useState<'idle' | 'running' | 'saved' | 'failed'>('idle');

  useEffect(() => {
    setDraft(config);
  }, [config]);

  async function handleSave() {
    if (!draft) return;
    setSaveState('running');
    try {
      await onSave(draft);
      setSaveState('saved');
      setTimeout(() => setSaveState('idle'), FEEDBACK_TIMEOUT_MS);
    } catch {
      setSaveState('failed');
    }
  }

  if (!draft) {
    return <section className="content-grid"><p>Loading pricing configuration…</p></section>;
  }

  return (
    <section className="content-grid">
      <div className="strategy-topbar">
        <div>
          <h2>Admin — Model pricing (₹/min)</h2>
          <p>Prices set here drive the campaign budget widget and every bot's Agent details cost card.</p>
        </div>
        <button
          className={saveState === 'failed' ? 'fallback-button' : 'primary'}
          disabled={saveState === 'running'}
          onClick={handleSave}
        >
          <Save size={14} />
          {saveState === 'running' ? 'Saving…' : saveState === 'saved' ? 'Saved' : saveState === 'failed' ? 'Retry save' : 'Save changes'}
        </button>
      </div>

      <PricingCategoryEditor
        title="LLM"
        hint="Drives the per-bot Cost/Latency/Tokens estimate and the campaign budget router."
        entries={draft.llm}
        onChange={(llm) => setDraft({ ...draft, llm })}
        withEstimates
      />
      <PricingCategoryEditor
        title="TTS"
        hint="Used in the per-bot cost card and the campaign budget widget."
        entries={draft.tts}
        onChange={(tts) => setDraft({ ...draft, tts })}
      />
      <PricingCategoryEditor
        title="STT"
        hint="Used in the per-bot cost card and the campaign budget widget's model stack comparison."
        entries={draft.stt}
        onChange={(stt) => setDraft({ ...draft, stt })}
      />
      <PricingCategoryEditor
        title="Telephony"
        hint="Used in the per-bot cost card and the campaign budget widget's model stack comparison."
        entries={draft.telephony}
        onChange={(telephony) => setDraft({ ...draft, telephony })}
      />
    </section>
  );
}
