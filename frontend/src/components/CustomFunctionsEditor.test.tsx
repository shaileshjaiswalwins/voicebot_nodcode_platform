import React, { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { CustomFunctionsEditor } from './CustomFunctionsEditor';
import { KeyValueEditor } from './KeyValueEditor';
import { newCustomFunction } from '../utils/customFunctions';
import type { CustomFunction, FunctionTestResult } from '../types';

/** Controlled wrapper so tests can observe onChange output. */
function Harness({
  initial = [],
  onTest,
}: {
  initial?: CustomFunction[];
  onTest?: (fn: CustomFunction, args: Record<string, unknown>) => Promise<FunctionTestResult>;
}) {
  const [fns, setFns] = useState<CustomFunction[]>(initial);
  return <CustomFunctionsEditor functions={fns} onChange={setFns} onTest={onTest} />;
}

describe('CustomFunctionsEditor', () => {
  it('shows an empty state and adds a function', () => {
    render(<Harness />);
    expect(screen.getByText(/No custom functions yet/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/Add function/i));
    expect(screen.getByText(/Unnamed function/i)).toBeInTheDocument();
    // A newly-added function opens expanded, showing the endpoint fields.
    expect(screen.getByLabelText('URL')).toBeInTheDocument();
  });

  it('edits the name and reflects it in the card header', () => {
    render(<Harness initial={[{ ...newCustomFunction(), id: 'f1' }]} />);
    fireEvent.click(screen.getByLabelText('Expand'));
    const nameInput = screen.getByPlaceholderText(/get_lead_details/i);
    fireEvent.change(nameInput, { target: { value: 'get_lead' } });
    expect(screen.getAllByDisplayValue('get_lead').length).toBeGreaterThan(0);
  });

  it('removes a function', () => {
    render(<Harness initial={[{ ...newCustomFunction(), id: 'f1', name: 'to_remove' }]} />);
    fireEvent.click(screen.getByTitle('Remove function'));
    expect(screen.getByText(/No custom functions yet/i)).toBeInTheDocument();
  });

  it('disables Test while validation errors exist (no URL)', () => {
    const onTest = vi.fn();
    render(<Harness initial={[{ ...newCustomFunction(), id: 'f1', name: 'get_lead', url: '' }]} onTest={onTest} />);
    fireEvent.click(screen.getByLabelText('Expand'));
    const testBtn = screen.getByRole('button', { name: /Test/i });
    expect(testBtn).toBeDisabled();
  });

  it('runs a test and shows the result', async () => {
    const onTest = vi.fn(async () => ({
      ok: true, status_code: 200, latency_ms: 42, response: { hi: 'there' },
      extracted_vars: { lead_name: 'Priya' }, error: null,
      request: { method: 'POST', url: 'http://x', params: null, json: {}, data: null },
    }));
    render(<Harness initial={[{ ...newCustomFunction(), id: 'f1', name: 'get_lead', url: 'http://x' }]} onTest={onTest} />);
    fireEvent.click(screen.getByLabelText('Expand'));
    fireEvent.click(screen.getByRole('button', { name: /Test/i }));
    await waitFor(() => expect(onTest).toHaveBeenCalled());
    expect(await screen.findByText(/HTTP 200/)).toBeInTheDocument();
    expect(screen.getByText(/lead_name/)).toBeInTheDocument();
  });

  it('lets the user pick a trigger (before/during/after call)', () => {
    render(<Harness initial={[{ ...newCustomFunction(), id: 'f1', name: 'x', url: 'http://x' }]} />);
    fireEvent.click(screen.getByLabelText('Expand'));
    const triggerSelect = screen.getByDisplayValue('During call');
    fireEvent.change(triggerSelect, { target: { value: 'pre_call' } });
    expect(screen.getByDisplayValue('Before call')).toBeInTheDocument();
  });

  it('does not leak query params between two functions sharing a blank id', () => {
    render(<Harness initial={[
      { ...newCustomFunction(), id: '', name: 'fn_one' },
      { ...newCustomFunction(), id: '', name: 'fn_two' },
    ]} />);

    fireEvent.click(screen.getAllByLabelText('Expand')[0]);
    // Query Parameters and Request Body — Parameters both have an "Add Parameter" button;
    // Query Parameters renders first in DOM order.
    fireEvent.click(screen.getAllByText(/Add Parameter/i)[0]);
    // Query Parameters is the only KeyValueEditor using the default "key"/"value" placeholders
    // (Headers uses "Header-Name" for its key), and no Headers row exists yet.
    fireEvent.change(screen.getByPlaceholderText('key'), { target: { value: 'a' } });
    fireEvent.change(screen.getByPlaceholderText('value'), { target: { value: '1' } });
    fireEvent.click(screen.getByLabelText('Collapse'));

    fireEvent.click(screen.getAllByLabelText('Expand')[1]);
    expect(screen.queryByDisplayValue('a')).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue('1')).not.toBeInTheDocument();
  });

  it('keeps a card open across re-renders when the function had a blank id', () => {
    render(<Harness initial={[{ ...newCustomFunction(), id: '', name: 'fn_one' }]} />);
    fireEvent.click(screen.getByLabelText('Expand'));
    expect(screen.getByLabelText('URL')).toBeInTheDocument();
    // Editing a field re-renders the parent; the card must stay open, not collapse
    // because ensureFunctionIds reassigned a new random id out from under openId.
    fireEvent.change(screen.getByPlaceholderText(/get_lead_details/i), { target: { value: 'renamed' } });
    expect(screen.getByLabelText('URL')).toBeInTheDocument();
  });
});

describe('KeyValueEditor', () => {
  function KVHarness() {
    const [v, setV] = useState<Record<string, string>>({});
    return (
      <div>
        <KeyValueEditor value={v} onChange={setV} label="Headers" />
        <output data-testid="out">{JSON.stringify(v)}</output>
      </div>
    );
  }

  it('adds a pair and emits a record with the typed key/value', () => {
    render(<KVHarness />);
    fireEvent.click(screen.getByRole('button', { name: /^Add$/i }));
    fireEvent.change(screen.getByLabelText('Headers key 1'), { target: { value: 'Authorization' } });
    fireEvent.change(screen.getByLabelText('Headers value 1'), { target: { value: 'Bearer t' } });
    expect(screen.getByTestId('out').textContent).toBe(JSON.stringify({ Authorization: 'Bearer t' }));
  });

  it('does not emit rows with a blank key', () => {
    render(<KVHarness />);
    fireEvent.click(screen.getByRole('button', { name: /^Add$/i }));
    fireEvent.change(screen.getByLabelText('Headers value 1'), { target: { value: 'orphan' } });
    expect(screen.getByTestId('out').textContent).toBe('{}');
  });

  it('removes a pair', () => {
    render(<KVHarness />);
    fireEvent.click(screen.getByRole('button', { name: /^Add$/i }));
    fireEvent.change(screen.getByLabelText('Headers key 1'), { target: { value: 'X' } });
    fireEvent.change(screen.getByLabelText('Headers value 1'), { target: { value: '1' } });
    fireEvent.click(screen.getByTitle('Remove'));
    expect(screen.getByTestId('out').textContent).toBe('{}');
  });
});
