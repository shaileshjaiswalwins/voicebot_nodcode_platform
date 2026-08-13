import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AnalysisFieldsEditor, slugifyKey, fieldErrors } from './AnalysisFieldsEditor';
import type { AnalysisFieldDef } from '../types';

function makeField(overrides: Partial<AnalysisFieldDef> = {}): AnalysisFieldDef {
  return {
    key: 'field_1',
    label: 'Appointment booked',
    type: 'boolean',
    description: '',
    ...overrides,
  } as AnalysisFieldDef;
}

describe('AnalysisFieldsEditor card expand/collapse', () => {
  it('keeps the card open and accepts every keystroke when typing a multi-character field key', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <AnalysisFieldsEditor fields={[makeField({ key: 'field_1' })]} onChange={onChange} />,
    );

    // Open the card.
    fireEvent.click(screen.getByRole('button', { name: /expand/i }));

    let keyInput = screen.getByPlaceholderText(/appointment_booked/i) as HTMLInputElement;
    expect(keyInput).toBeInTheDocument();

    // Regression: each keystroke used to change `field.key`, which was also used to derive the
    // expand/collapse identity, collapsing (and unmounting) the card after a single character.
    const chars = ['m', 'y', '_', 'k', 'e', 'y'];
    let currentFields: AnalysisFieldDef[] = [makeField({ key: 'field_1' })];
    for (const ch of chars) {
      const nextKey = currentFields[0].key + ch;
      fireEvent.change(keyInput, { target: { value: nextKey } });
      expect(onChange).toHaveBeenCalled();
      currentFields = onChange.mock.calls[onChange.mock.calls.length - 1][0];
      // Simulate the parent feeding the updated fields back in, as it would in real usage.
      rerender(<AnalysisFieldsEditor fields={currentFields} onChange={onChange} />);
      keyInput = screen.getByPlaceholderText(/appointment_booked/i) as HTMLInputElement;
      expect(keyInput).toBeInTheDocument();
    }

    expect(currentFields[0].key).toBe('field_1mykey');
    // The card must still be open — the key input (only rendered when expanded) is present.
    expect(screen.getByPlaceholderText(/appointment_booked/i)).toBeInTheDocument();
  });

  it('does not collapse a different card when a sibling field key changes', () => {
    const onChange = vi.fn();
    const fields = [makeField({ key: 'a', label: 'A' }), makeField({ key: 'b', label: 'B' })];
    const { rerender } = render(<AnalysisFieldsEditor fields={fields} onChange={onChange} />);

    const expandButtons = screen.getAllByRole('button', { name: /expand/i });
    fireEvent.click(expandButtons[1]); // open the second card

    const keyInputs = screen.getAllByPlaceholderText(/appointment_booked/i);
    expect(keyInputs).toHaveLength(1); // only the open card renders the key input

    fireEvent.change(keyInputs[0], { target: { value: 'bb' } });
    const nextFields = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    rerender(<AnalysisFieldsEditor fields={nextFields} onChange={onChange} />);

    // Still exactly one open card, still the second one.
    expect(screen.getAllByPlaceholderText(/appointment_booked/i)).toHaveLength(1);
  });
});

describe('AnalysisFieldsEditor label -> key auto-derive', () => {
  it('derives the key from the label for a freshly added field until the key is hand-edited', () => {
    const onChange = vi.fn();
    const { rerender } = render(<AnalysisFieldsEditor fields={[]} onChange={onChange} />);

    fireEvent.click(screen.getByRole('button', { name: /add field/i }));
    let fields: AnalysisFieldDef[] = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    rerender(<AnalysisFieldsEditor fields={fields} onChange={onChange} />);

    const labelInput = screen.getByPlaceholderText(/appointment booked/i);
    fireEvent.change(labelInput, { target: { value: 'Call Outcome' } });
    fields = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(fields[0].label).toBe('Call Outcome');
    expect(fields[0].key).toBe(slugifyKey('Call Outcome'));

    rerender(<AnalysisFieldsEditor fields={fields} onChange={onChange} />);

    // Now hand-edit the key — it should stop tracking the label after this.
    const keyInput = screen.getByPlaceholderText(/appointment_booked/i);
    fireEvent.change(keyInput, { target: { value: 'custom_key' } });
    fields = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(fields[0].key).toBe('custom_key');
    rerender(<AnalysisFieldsEditor fields={fields} onChange={onChange} />);

    fireEvent.change(screen.getByPlaceholderText(/appointment booked/i), { target: { value: 'Call Outcome 2' } });
    fields = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(fields[0].label).toBe('Call Outcome 2');
    expect(fields[0].key).toBe('custom_key'); // unchanged — key was hand-edited
  });

  it('does not rewrite the key of a pre-existing field when only its label is edited', () => {
    const onChange = vi.fn();
    const fields = [makeField({ key: 'important_existing_key', label: 'Old label' })];
    render(<AnalysisFieldsEditor fields={fields} onChange={onChange} />);

    fireEvent.click(screen.getByRole('button', { name: /expand/i }));
    fireEvent.change(screen.getByPlaceholderText(/appointment booked/i), { target: { value: 'New label' } });

    const next = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(next[0].label).toBe('New label');
    expect(next[0].key).toBe('important_existing_key');
  });
});

describe('slugifyKey', () => {
  it('lowercases and underscores a normal label', () => {
    expect(slugifyKey('Appointment Booked')).toBe('appointment_booked');
  });

  it('strips special characters', () => {
    expect(slugifyKey('Caller\'s Intent (Primary)!')).toBe('caller_s_intent_primary');
  });

  it('falls back to "field" for an empty or whitespace-only label', () => {
    expect(slugifyKey('')).toBe('field');
    expect(slugifyKey('   ')).toBe('field');
  });
});

describe('fieldErrors', () => {
  it('flags a missing key and missing label', () => {
    const field = makeField({ key: '', label: '' });
    const errors = fieldErrors(field, [field]);
    expect(errors).toContain('Field key is required.');
    expect(errors).toContain('Display name is required.');
  });

  it('flags a duplicate key across fields', () => {
    const a = makeField({ key: 'dup', label: 'A' });
    const b = makeField({ key: 'dup', label: 'B' });
    expect(fieldErrors(a, [a, b])).toContain('Field key must be unique.');
    expect(fieldErrors(b, [a, b])).toContain('Field key must be unique.');
  });

  it('does not flag unique keys across fields', () => {
    const a = makeField({ key: 'one', label: 'A' });
    const b = makeField({ key: 'two', label: 'B' });
    expect(fieldErrors(a, [a, b])).not.toContain('Field key must be unique.');
  });

  it('requires at least one non-empty enum option when type is enum', () => {
    const withNone = makeField({ type: 'enum', enum_options: [] });
    expect(fieldErrors(withNone, [withNone])).toContain('Enum fields need at least one allowed option.');

    const withBlankOnly = makeField({ type: 'enum', enum_options: ['   '] });
    expect(fieldErrors(withBlankOnly, [withBlankOnly])).toContain('Enum fields need at least one allowed option.');

    const withOption = makeField({ type: 'enum', enum_options: ['Yes'] });
    expect(fieldErrors(withOption, [withOption])).not.toContain('Enum fields need at least one allowed option.');
  });

  it('does not require enum options for non-enum types', () => {
    const boolField = makeField({ type: 'boolean', enum_options: undefined });
    expect(fieldErrors(boolField, [boolField])).not.toContain('Enum fields need at least one allowed option.');
  });
});
