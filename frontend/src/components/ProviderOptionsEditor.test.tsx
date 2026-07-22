import React, { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ProviderOptionsEditor } from './ProviderOptionsEditor';
import { SARVAM_TTS_FIELDS, GEMINI_LLM_FIELDS } from '../constants/providerParams';

function Harness({ fields, initial = {} }: { fields: typeof SARVAM_TTS_FIELDS; initial?: Record<string, unknown> }) {
  const [v, setV] = useState<Record<string, unknown>>(initial);
  return (
    <div>
      <ProviderOptionsEditor fields={fields} value={v} onChange={setV} />
      <output data-testid="out">{JSON.stringify(v)}</output>
    </div>
  );
}

describe('ProviderOptionsEditor', () => {
  it('renders a field per spec entry', () => {
    render(<Harness fields={SARVAM_TTS_FIELDS} />);
    expect(screen.getByLabelText('Pace')).toBeInTheDocument();
    expect(screen.getByLabelText('Pitch')).toBeInTheDocument();
    expect(screen.getByLabelText('Output codec')).toBeInTheDocument();
  });

  it('writes a numeric value into the options object', () => {
    render(<Harness fields={SARVAM_TTS_FIELDS} />);
    fireEvent.change(screen.getByLabelText('Pace'), { target: { value: '1.3' } });
    expect(JSON.parse(screen.getByTestId('out').textContent!)).toEqual({ pace: 1.3 });
  });

  it('clearing a field removes the key so it falls back to the pipeline default', () => {
    render(<Harness fields={SARVAM_TTS_FIELDS} initial={{ pace: 1.3 }} />);
    fireEvent.change(screen.getByLabelText('Pace'), { target: { value: '' } });
    expect(JSON.parse(screen.getByTestId('out').textContent!)).toEqual({});
  });

  it('select fields default to empty (pipeline default) and store the chosen value', () => {
    render(<Harness fields={SARVAM_TTS_FIELDS} />);
    fireEvent.change(screen.getByLabelText('Output codec'), { target: { value: 'mp3' } });
    expect(JSON.parse(screen.getByTestId('out').textContent!)).toEqual({ output_audio_codec: 'mp3' });
  });

  it('bool fields store true when checked and drop the key when unchecked', () => {
    render(<Harness fields={SARVAM_TTS_FIELDS} />);
    const cb = screen.getByLabelText('Enable preprocessing');
    fireEvent.click(cb);
    expect(JSON.parse(screen.getByTestId('out').textContent!)).toEqual({ enable_preprocessing: true });
    fireEvent.click(cb);
    expect(JSON.parse(screen.getByTestId('out').textContent!)).toEqual({});
  });

  it('warns when a numeric value is outside the spec min/max but still stores it', () => {
    render(<Harness fields={SARVAM_TTS_FIELDS} />);
    fireEvent.change(screen.getByLabelText('Pace'), { target: { value: '9' } }); // max is 3.0
    expect(JSON.parse(screen.getByTestId('out').textContent!)).toEqual({ pace: 9 });
    expect(screen.getByRole('alert')).toHaveTextContent(/Above recommended maximum/);
  });

  it('shows no range warning for an in-range value', () => {
    render(<Harness fields={SARVAM_TTS_FIELDS} />);
    fireEvent.change(screen.getByLabelText('Pace'), { target: { value: '1.2' } });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('supports Gemini fields too', () => {
    render(<Harness fields={GEMINI_LLM_FIELDS} />);
    fireEvent.change(screen.getByLabelText('Max output tokens'), { target: { value: '512' } });
    expect(JSON.parse(screen.getByTestId('out').textContent!)).toEqual({ max_output_tokens: 512 });
  });
});
