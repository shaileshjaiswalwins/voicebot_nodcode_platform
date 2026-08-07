import React, { useState, useEffect } from 'react';
import { BookOpen, Pencil, Plus, Save } from 'lucide-react';
import type { AnalysisPromptEntry, AnalysisPromptKey, LanguageSettings, LanguageOption, LibraryPhrase, OutcomeEntry, PhraseCategory } from '../api';
import { TimeAgo } from '../components/TimeAgo';
import { EmptyState } from '../components/EmptyState';
import { RowActions } from '../components/RowActions';
import { Spinner } from '../components/Spinner';
import { PHRASE_CATEGORY_META } from '../constants/phrases';

type LibrarySection = PhraseCategory | 'outcomes' | 'languages' | 'analysis_prompts';

const ANALYSIS_PROMPT_META: Record<AnalysisPromptKey, { label: string; description: string }> = {
  call_analysis: {
    label: 'Call analysis',
    description: 'Runs once per finished call against the saved transcript to classify the outcome and extract qualification answers. Unrelated to the live system_prompt — this never speaks to the caller, it only reads the transcript afterward.',
  },
  b2b_score: {
    label: 'B2B lead score',
    description: 'A separate post-call pass that scores B2B lead quality (deal value, intent, urgency) from the same transcript.',
  },
};

export function PhraseEditor({
  meta, rows, editingId,
  editingText, editingLanguage, editingNotes,
  draftText, draftLanguage, draftNotes,
  setDraftText, setDraftLanguage, setDraftNotes,
  setEditingText, setEditingLanguage, setEditingNotes,
  onStartEdit, onCancelEdit, onSubmitEdit, onSubmitNew, onDeleteRow
}: {
  meta: { id: PhraseCategory; label: string; description: string };
  rows: LibraryPhrase[];
  editingId: string;
  editingText: string;
  editingLanguage: string;
  editingNotes: string;
  draftText: string;
  draftLanguage: string;
  draftNotes: string;
  setDraftText: (v: string) => void;
  setDraftLanguage: (v: string) => void;
  setDraftNotes: (v: string) => void;
  setEditingText: (v: string) => void;
  setEditingLanguage: (v: string) => void;
  setEditingNotes: (v: string) => void;
  onStartEdit: (row: LibraryPhrase) => void;
  onCancelEdit: () => void;
  onSubmitEdit: () => void;
  onSubmitNew: () => void;
  onDeleteRow: (row: LibraryPhrase) => Promise<void> | void;
}) {
  return (
    <>
      <div className="callout">
        <BookOpen size={18} />
        <div>
          <strong>{meta.label}</strong>
          <p>{meta.description}</p>
          <p className="muted">Edits go live within a minute. Currently-running calls keep using their original list.</p>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div>
            <h2>Add a new phrase</h2>
            <p>Type the words exactly as you'd expect them to appear in a transcript. Matching is case-insensitive substring.</p>
          </div>
          <Plus size={18} />
        </div>
        <div className="form-grid">
          <label className="full">
            Phrase text
            <input
              value={draftText}
              onChange={(event) => setDraftText(event.target.value)}
              placeholder='e.g. "please record your message after the tone"'
            />
          </label>
          <label>
            Language tag (optional)
            <input
              value={draftLanguage}
              onChange={(event) => setDraftLanguage(event.target.value)}
              placeholder="en, hi, gu, ta..."
            />
          </label>
          <label>
            Notes (optional)
            <input
              value={draftNotes}
              onChange={(event) => setDraftNotes(event.target.value)}
              placeholder="Where you saw this, or why you added it"
            />
          </label>
        </div>
        <div className="button-row">
          <button className="primary" disabled={!draftText.trim()} onClick={onSubmitNew}>
            <Plus size={16} /> Add to {meta.label}
          </button>
        </div>
      </div>

      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Saved {meta.label.toLowerCase()}</h2>
            <p>{rows.length} entries. Click a row to edit. Deleted entries stop matching new calls within a minute.</p>
          </div>
        </div>
        <div className="table-scroll"><table>
          <thead>
            <tr><th>Phrase</th><th>Language</th><th>Notes</th><th>Updated</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              editingId === row._id ? (
                <tr key={row._id} className="selected-row">
                  <td><input value={editingText} onChange={(event) => setEditingText(event.target.value)} /></td>
                  <td><input value={editingLanguage} onChange={(event) => setEditingLanguage(event.target.value)} style={{ width: 80 }} /></td>
                  <td><input value={editingNotes} onChange={(event) => setEditingNotes(event.target.value)} /></td>
                  <td><TimeAgo value={row.updated_at} /></td>
                  <td>
                    <div className="button-row">
                      <button className="primary" onClick={onSubmitEdit}><Save size={14} /> Save</button>
                      <button onClick={onCancelEdit}>Cancel</button>
                    </div>
                  </td>
                </tr>
              ) : (
                <tr key={row._id}>
                  <td><strong>{row.text}</strong>{row.created_by && <small>added by {row.created_by}</small>}</td>
                  <td><code>{row.language || '-'}</code></td>
                  <td><small>{row.notes || '-'}</small></td>
                  <td><TimeAgo value={row.updated_at} /></td>
                  <td>
                    <RowActions
                      item={row}
                      onEdit={onStartEdit}
                      editTitle="Edit"
                      onDelete={onDeleteRow}
                      deleteTitle="Delete phrase?"
                      deleteDescription={(target) => `"${target.text}" — calls starting after the next refresh will stop detecting this line. This can't be undone.`}
                    />
                  </td>
                </tr>
              )
            ))}
            {!rows.length && (
              <tr><td colSpan={5}>
                <EmptyState
                  icon={<BookOpen size={32} />}
                  heading="No phrases yet"
                  description="No phrases yet for this category. Add the first one above."
                />
              </td></tr>
            )}
          </tbody>
        </table></div>
      </div>
    </>
  );
}

export function OutcomeCatalog({
  outcomes,
  onUpdate
}: {
  outcomes: OutcomeEntry[];
  onUpdate: (key: string, payload: Partial<OutcomeEntry>) => Promise<void> | void;
}) {
  const [editingKey, setEditingKey] = useState('');
  const [draftLabel, setDraftLabel] = useState('');
  const [draftDescription, setDraftDescription] = useState('');

  function startEditOutcome(row: OutcomeEntry) {
    setEditingKey(row.key);
    setDraftLabel(row.display_label || row.key);
    setDraftDescription(row.description || '');
  }

  function cancelOutcomeEdit() {
    setEditingKey('');
    setDraftLabel('');
    setDraftDescription('');
  }

  async function submitOutcomeEdit() {
    if (!editingKey || !draftDescription.trim()) return;
    await onUpdate(editingKey, {
      display_label: draftLabel.trim() || editingKey,
      description: draftDescription.trim()
    });
    cancelOutcomeEdit();
  }

  return (
    <>
      <div className="callout">
        <BookOpen size={18} />
        <div>
          <strong>Call outcomes</strong>
          <p>These are the categories the AI uses to tag every call (Approved, Not Interested, Wrong Number, etc.). The key is fixed because downstream systems use it, but you can edit the description that teaches the AI when to pick each one.</p>
          <p className="muted">Edits take effect on calls analyzed after a 60-second cache refresh.</p>
        </div>
      </div>
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>{outcomes.length} outcome categories</h2>
            <p>The longer and clearer the description, the better the AI gets at picking the right label.</p>
          </div>
        </div>
        <div className="table-scroll"><table>
          <thead>
            <tr>
              <th style={{ width: 220 }}>Key</th>
              <th style={{ width: 180 }}>Display label</th>
              <th>Description (sent to the AI)</th>
              <th>Updated</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {outcomes.map((row) => (
              editingKey === row.key ? (
                <tr key={row.key} className="selected-row">
                  <td><code>{row.key}</code></td>
                  <td><input value={draftLabel} onChange={(e) => setDraftLabel(e.target.value)} /></td>
                  <td><textarea rows={4} value={draftDescription} onChange={(e) => setDraftDescription(e.target.value)} /></td>
                  <td><TimeAgo value={row.updated_at} /></td>
                  <td>
                    <div className="button-row">
                      <button className="primary" onClick={submitOutcomeEdit}><Save size={14} /> Save</button>
                      <button onClick={cancelOutcomeEdit}>Cancel</button>
                    </div>
                  </td>
                </tr>
              ) : (
                <tr key={row.key}>
                  <td><code>{row.key}</code></td>
                  <td><strong>{row.display_label || row.key}</strong></td>
                  <td><small>{row.description}</small></td>
                  <td><TimeAgo value={row.updated_at} /></td>
                  <td><button onClick={() => startEditOutcome(row)}><Pencil size={13} /> Edit</button></td>
                </tr>
              )
            ))}
            {!outcomes.length && <tr><td colSpan={5}>Outcome catalog is empty. The backend seeds defaults on next startup.</td></tr>}
          </tbody>
        </table></div>
      </div>
    </>
  );
}

export function LanguageSettingsCatalog({
  settings,
  onUpsert,
  onDelete
}: {
  settings: LanguageSettings[];
  onUpsert: (payload: Partial<LanguageSettings>) => Promise<void> | void;
  onDelete?: (id: string) => Promise<void> | void;
}) {
  const [activeId, setActiveId] = useState<string>(settings[0]?.id || '');
  const current = settings.find((item) => item.id === activeId) || settings[0];
  const [draft, setDraft] = useState<LanguageSettings>({
    id: current?.id || '',
    name: current?.name || '',
    timeout_message: current?.timeout_message || '',
    inactivity_nudge: current?.inactivity_nudge || '',
    lang_notes: current?.lang_notes || ''
  });
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!creating) {
      setDraft({
        id: current?.id || '',
        name: current?.name || '',
        timeout_message: current?.timeout_message || '',
        inactivity_nudge: current?.inactivity_nudge || '',
        lang_notes: current?.lang_notes || ''
      });
    }
  }, [current?.id, current?.name, current?.timeout_message, current?.inactivity_nudge, current?.lang_notes, creating]);

  function startNew() {
    setCreating(true);
    setActiveId('');
    setDraft({
      id: '',
      name: '',
      timeout_message: '',
      inactivity_nudge: '',
      lang_notes: ''
    });
  }

  function cancelNew() {
    setCreating(false);
    if (settings[0]) setActiveId(settings[0].id);
  }

  async function save() {
    if (!draft.id.trim() || !draft.name.trim()) return;
    await onUpsert(draft);
    setCreating(false);
    setActiveId(draft.id);
  }

  return (
    <>
      <div className="callout">
        <BookOpen size={18} />
        <div>
          <strong>Per-language settings</strong>
          <p>Edit the messages and style notes the bot uses when speaking each language. The "timeout message" is what the bot says when the 5-minute call timer fires. The "style notes" go into the system prompt to set tone and word choice.</p>
          <p className="muted">Changes apply to new calls within ~60s. Live calls finish on the snapshot they started with.</p>
        </div>
      </div>

      <div className="content-grid two-col">
        <div className="table-panel">
          <div className="panel-header">
            <div>
              <h2>Languages</h2>
              <p>{settings.length} configured. Pick one to edit or add a new language.</p>
            </div>
            <button onClick={startNew}><Plus size={16} /> New language</button>
          </div>
          <div className="table-scroll"><table>
            <thead>
              <tr><th>ID</th><th>Display name</th><th>Updated</th></tr>
            </thead>
            <tbody>
              {settings.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => { setCreating(false); setActiveId(row.id); }}
                  className={!creating && row.id === activeId ? 'selected-row' : ''}
                >
                  <td><code>{row.id}</code></td>
                  <td><strong>{row.name}</strong></td>
                  <td><TimeAgo value={row.updated_at} /></td>
                </tr>
              ))}
              {!settings.length && (
                <tr><td colSpan={3}>
                  <EmptyState
                    icon={<BookOpen size={32} />}
                    heading="No languages yet"
                    description={'Click "New language" to add the first.'}
                  />
                </td></tr>
              )}
            </tbody>
          </table></div>
        </div>
        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>{creating ? 'New language' : (current ? `Editing ${current.name}` : 'Select a language')}</h2>
              <p>The ID is used as a key in Mongo. Keep it lowercase and stable (e.g. "hindi", "tamil"). The display name is what the dashboard shows.</p>
            </div>
            {!creating && current && onDelete && (
              <RowActions
                item={current}
                onDelete={async (target) => {
                  await onDelete(target.id);
                  if (target.id === activeId) setActiveId(settings.find((s) => s.id !== target.id)?.id || '');
                }}
                deleteTitle="Delete language?"
                deleteDescription={(target) => `"${target.name}" will be removed. Bots or campaigns still referencing this language id will fall back to defaults.`}
              />
            )}
          </div>
          <div className="form-section">
            <div className="form-grid">
              <label>
                ID
                <input
                  value={draft.id}
                  onChange={(e) => setDraft({ ...draft, id: e.target.value })}
                  disabled={!creating}
                  placeholder="lowercase, no spaces"
                />
              </label>
              <label>
                Display name
                <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
              </label>
            </div>
            <label className="full">
              Timeout message (what the bot says at the 5-min hard timeout)
              <textarea
                rows={3}
                value={draft.timeout_message || ''}
                onChange={(e) => setDraft({ ...draft, timeout_message: e.target.value })}
              />
            </label>
            <label className="full">
              Inactivity nudge (what the bot says when the caller goes silent)
              <textarea
                rows={2}
                value={draft.inactivity_nudge || ''}
                onChange={(e) => setDraft({ ...draft, inactivity_nudge: e.target.value })}
              />
            </label>
            <label className="full">
              Language style notes (added to the system prompt; describes tone, fillers, and example phrasing)
              <textarea
                rows={10}
                value={draft.lang_notes || ''}
                onChange={(e) => setDraft({ ...draft, lang_notes: e.target.value })}
              />
            </label>
          </div>
          <div className="button-row">
            <button
              className="primary"
              onClick={save}
              disabled={!draft.id.trim() || !draft.name.trim()}
            >
              <Save size={16} /> Save
            </button>            {creating && <button onClick={cancelNew}>Cancel</button>}
          </div>
        </div>
      </div>

    </>
  );
}

export function AnalysisPromptCatalog({
  prompts,
  onUpdate
}: {
  prompts: AnalysisPromptEntry[];
  onUpdate: (key: AnalysisPromptKey, promptTemplate: string) => Promise<void> | void;
}) {
  const keys = Object.keys(ANALYSIS_PROMPT_META) as AnalysisPromptKey[];
  const [activeKey, setActiveKey] = useState<AnalysisPromptKey>(keys[0]);
  const current = prompts.find((p) => p.key === activeKey);
  const [draft, setDraft] = useState(current?.prompt_template || '');
  const [saveState, setSaveState] = useState<'idle' | 'running' | 'failed'>('idle');
  const dirty = draft !== (current?.prompt_template || '');

  useEffect(() => {
    setDraft(current?.prompt_template || '');
    setSaveState('idle');
  }, [activeKey, current?.prompt_template]);

  async function save() {
    setSaveState('running');
    try {
      await onUpdate(activeKey, draft);
      setSaveState('idle');
    } catch {
      setSaveState('failed');
    }
  }

  return (
    <>
      <div className="callout">
        <BookOpen size={18} />
        <div>
          <strong>Post-call analysis prompts</strong>
          <p>These prompts run AFTER a call ends, against the saved transcript — separate from the live system_prompt configured per-bot. Keep any doubled curly braces (<code>{'{{'}</code> / <code>{'}}'}</code>) exactly as they are — they represent literal JSON examples in the prompt, not something to edit. Single-brace placeholders like <code>{'{lines}'}</code> are filled in automatically at call time and must not be deleted.</p>
          <p className="muted">Edits go live within a minute. Calls already mid-flight finish analysis on the prompt version they started with.</p>
        </div>
      </div>

      <div className="library-tabs" style={{ marginBottom: '0.75rem' }}>
        {keys.map((key) => (
          <button
            key={key}
            className={key === activeKey ? 'library-tab active' : 'library-tab'}
            onClick={() => setActiveKey(key)}
          >
            <strong>{ANALYSIS_PROMPT_META[key].label}</strong>
          </button>
        ))}
      </div>

      <div className="panel">
        <div className="panel-header">
          <div>
            <h2>{ANALYSIS_PROMPT_META[activeKey].label}</h2>
            <p>{ANALYSIS_PROMPT_META[activeKey].description}</p>
          </div>
        </div>
        <label className="full">
          Prompt template
          <textarea
            className="json-editor"
            rows={24}
            spellCheck={false}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        </label>
        {current?.updated_at && (
          <p className="muted">Last updated <TimeAgo value={current.updated_at} /> {current.updated_by ? `by ${current.updated_by}` : ''}</p>
        )}
        <div className="button-row">
          <button
            className={saveState === 'failed' ? 'fallback-button' : 'primary'}
            onClick={save}
            disabled={saveState === 'running' || !dirty || !draft.trim()}
          >
            <Save size={16} />
            {saveState === 'running' ? 'Saving...' : saveState === 'failed' ? 'Retry save' : dirty ? 'Save prompt' : 'Saved'}
          </button>
        </div>
      </div>
    </>
  );
}

export function LibraryView({
  phrases,
  outcomes,
  languageSettings,
  analysisPrompts,
  loading,
  onCreate,
  onUpdate,
  onDelete,
  onUpdateOutcome,
  onUpsertLanguageSettings,
  onDeleteLanguageSettings,
  onUpdateAnalysisPrompt
}: {
  phrases: LibraryPhrase[];
  outcomes: OutcomeEntry[];
  languageSettings: LanguageSettings[];
  analysisPrompts: AnalysisPromptEntry[];
  /** True while the initial library fetch (phrases/outcomes/language settings/analysis
   * prompts) is in flight. Without this, a first-ever load renders every tab as "0 items"
   * indistinguishable from a genuinely empty library. */
  loading?: boolean;
  onCreate: (payload: Partial<LibraryPhrase>) => Promise<void> | void;
  onUpdate: (id: string, payload: Partial<LibraryPhrase>) => Promise<void> | void;
  onDelete: (id: string) => Promise<void> | void;
  onUpdateOutcome: (key: string, payload: Partial<OutcomeEntry>) => Promise<void> | void;
  onUpsertLanguageSettings: (payload: Partial<LanguageSettings>) => Promise<void> | void;
  onDeleteLanguageSettings?: (id: string) => Promise<void> | void;
  onUpdateAnalysisPrompt: (key: AnalysisPromptKey, promptTemplate: string) => Promise<void> | void;
}) {
  const [activeSection, setActiveSection] = useState<LibrarySection>('voicemail');
  const [draftText, setDraftText] = useState('');
  const [draftLanguage, setDraftLanguage] = useState('en');
  const [draftNotes, setDraftNotes] = useState('');
  const [editingId, setEditingId] = useState<string>('');
  const [editingText, setEditingText] = useState('');
  const [editingLanguage, setEditingLanguage] = useState('');
  const [editingNotes, setEditingNotes] = useState('');

  const phraseMeta = PHRASE_CATEGORY_META.find((item) => item.id === activeSection as PhraseCategory);
  const meta = phraseMeta;
  const activeCategory = activeSection as PhraseCategory;
  const rows = phraseMeta ? phrases.filter((item) => item.category === activeSection) : [];

  function startEdit(row: LibraryPhrase) {
    setEditingId(row._id);
    setEditingText(row.text);
    setEditingLanguage(row.language || '');
    setEditingNotes(row.notes || '');
  }

  function cancelEdit() {
    setEditingId('');
    setEditingText('');
    setEditingLanguage('');
    setEditingNotes('');
  }

  async function submitNew() {
    if (!draftText.trim()) return;
    await onCreate({
      category: activeCategory,
      text: draftText.trim(),
      language: draftLanguage.trim(),
      notes: draftNotes.trim()
    });
    setDraftText('');
    setDraftNotes('');
  }

  async function submitEdit() {
    if (!editingId || !editingText.trim()) return;
    await onUpdate(editingId, {
      text: editingText.trim(),
      language: editingLanguage.trim(),
      notes: editingNotes.trim()
    });
    cancelEdit();
  }

  if (loading && !phrases.length && !outcomes.length && !languageSettings.length && !analysisPrompts.length) {
    return (
      <section className="library-layout" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '240px' }}>
        <Spinner label="Loading library" size={22} />
      </section>
    );
  }

  return (
    <section className="library-layout">
      <div className="library-tabs">
        {PHRASE_CATEGORY_META.map((item) => (
          <button
            key={item.id}
            className={item.id === activeSection ? 'library-tab active' : 'library-tab'}
            onClick={() => { setActiveSection(item.id); cancelEdit(); }}
          >
            <strong>{item.label}</strong>
            <small>{phrases.filter((p) => p.category === item.id).length} phrases</small>
          </button>
        ))}
        <button
          className={activeSection === 'outcomes' ? 'library-tab active' : 'library-tab'}
          onClick={() => { setActiveSection('outcomes'); cancelEdit(); }}
        >
          <strong>Call outcomes</strong>
          <small>{outcomes.length} categories</small>
        </button>
        <button
          className={activeSection === 'languages' ? 'library-tab active' : 'library-tab'}
          onClick={() => { setActiveSection('languages'); cancelEdit(); }}
        >
          <strong>Language settings</strong>
          <small>{languageSettings.length} languages</small>
        </button>
        <button
          className={activeSection === 'analysis_prompts' ? 'library-tab active' : 'library-tab'}
          onClick={() => { setActiveSection('analysis_prompts'); cancelEdit(); }}
        >
          <strong>Analysis prompts</strong>
          <small>{analysisPrompts.length} prompts</small>
        </button>
      </div>

      {activeSection === 'outcomes' ? (
        <OutcomeCatalog outcomes={outcomes} onUpdate={onUpdateOutcome} />
      ) : activeSection === 'languages' ? (
        <LanguageSettingsCatalog
          settings={languageSettings}
          onUpsert={onUpsertLanguageSettings}
          onDelete={onDeleteLanguageSettings}
        />
      ) : activeSection === 'analysis_prompts' ? (
        <AnalysisPromptCatalog prompts={analysisPrompts} onUpdate={onUpdateAnalysisPrompt} />
      ) : (
        <PhraseEditor
          meta={meta!}
          rows={rows}
          editingId={editingId}
          editingText={editingText}
          editingLanguage={editingLanguage}
          editingNotes={editingNotes}
          draftText={draftText}
          draftLanguage={draftLanguage}
          draftNotes={draftNotes}
          setDraftText={setDraftText}
          setDraftLanguage={setDraftLanguage}
          setDraftNotes={setDraftNotes}
          setEditingText={setEditingText}
          setEditingLanguage={setEditingLanguage}
          setEditingNotes={setEditingNotes}
          onStartEdit={startEdit}
          onCancelEdit={cancelEdit}
          onSubmitEdit={submitEdit}
          onSubmitNew={submitNew}
          onDeleteRow={(row) => onDelete(row._id)}
        />
      )}
    </section>
  );
}
