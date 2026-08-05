import React, { useState } from 'react';
import { Plus } from 'lucide-react';

export type DynamicVariable = { name: string; default_value?: string };

/** Declares {{var_name}} placeholders for a bot (BotConfig.dynamic_variables) and lets the
 * PM insert one into the prompt/opening line with a click instead of typing `{{...}}` by
 * hand and risking a typo that silently never gets substituted. `onInsert` is wired by the
 * parent to insert the token at the currently-focused field's cursor position — this
 * component only manages the declared list, it doesn't know which field is focused. */
export function DynamicVariablesEditor({
  variables,
  onChange,
  onInsert,
}: {
  variables: DynamicVariable[];
  onChange: (vars: DynamicVariable[]) => void;
  onInsert: (token: string) => void;
}) {
  const [name, setName] = useState('');
  const [defaultValue, setDefaultValue] = useState('');

  function add() {
    const trimmed = name.trim().replace(/\s+/g, '_');
    if (!trimmed || variables.some((v) => v.name === trimmed)) return;
    onChange([...variables, { name: trimmed, default_value: defaultValue.trim() }]);
    setName('');
    setDefaultValue('');
  }

  function remove(varName: string) {
    onChange(variables.filter((v) => v.name !== varName));
  }

  return (
    <label className="full">
      Dynamic variables
      <small>
        Declare a variable, then click its chip to insert <code>{'{{name}}'}</code> into the system prompt or
        opening line at your cursor. Default value fills in when a real/test call doesn't supply one.
      </small>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.4rem', marginBottom: '0.4rem', minHeight: '2rem' }}>
        {variables.map((v) => (
          <span
            key={v.name}
            style={{ background: 'var(--surface-2)', borderRadius: '4px', padding: '2px 8px', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '4px' }}
          >
            <button
              type="button"
              title={`Insert {{${v.name}}} at cursor`}
              onClick={() => onInsert(`{{${v.name}}}`)}
              style={{ padding: 0, background: 'none', border: 'none', cursor: 'pointer', font: 'inherit' }}
            >
              {'{{'}
              {v.name}
              {'}}'}
            </button>
            <button
              type="button"
              title="Remove this variable"
              style={{ padding: 0, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)', lineHeight: 1 }}
              onClick={() => remove(v.name)}
            >
              ×
            </button>
          </span>
        ))}
        {variables.length === 0 && (
          <span style={{ fontSize: '0.78rem', color: 'var(--muted)', fontStyle: 'italic' }}>No dynamic variables yet</span>
        )}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="variable name, e.g. first_name"
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          style={{ flex: 1 }}
        />
        <input
          value={defaultValue}
          onChange={(e) => setDefaultValue(e.target.value)}
          placeholder="default value (optional)"
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          style={{ flex: 1 }}
        />
        <button type="button" onClick={add} style={{ whiteSpace: 'nowrap' }}><Plus size={14} /> Add</button>
      </div>
    </label>
  );
}
