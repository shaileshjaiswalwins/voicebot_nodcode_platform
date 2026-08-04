import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { TestInputsModal } from './TestInputsModal';
import type { CustomFunction } from '../types';

describe('TestInputsModal', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const functions: CustomFunction[] = [];
  const basePreCallProps = { preCallParams: {}, onChangePreCallParams: vi.fn() };

  it('persists dynamic variables when the X button closes the modal without an explicit Save', () => {
    const onChangeDynamicVariables = vi.fn();
    const onChangeFunctionMocks = vi.fn();
    const onClose = vi.fn();
    render(
      <TestInputsModal
        open
        onClose={onClose}
        functions={functions}
        dynamicVariables={{}}
        onChangeDynamicVariables={onChangeDynamicVariables}
        functionMocks={{}}
        onChangeFunctionMocks={onChangeFunctionMocks}
        {...basePreCallProps}
      />
    );

    fireEvent.click(screen.getByText(/Add/i));
    fireEvent.change(screen.getByPlaceholderText('Enter the variable name'), { target: { value: 'owner_name' } });
    fireEvent.change(screen.getByPlaceholderText('Enter the value'), { target: { value: 'Raj' } });

    fireEvent.click(screen.getByLabelText('Close'));

    expect(onChangeDynamicVariables).toHaveBeenCalledWith({ owner_name: 'Raj' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('persists dynamic variables when the overlay is clicked to close', () => {
    const onChangeDynamicVariables = vi.fn();
    render(
      <TestInputsModal
        open
        onClose={vi.fn()}
        functions={functions}
        dynamicVariables={{}}
        onChangeDynamicVariables={onChangeDynamicVariables}
        functionMocks={{}}
        onChangeFunctionMocks={vi.fn()}
        {...basePreCallProps}
      />
    );

    fireEvent.click(screen.getByText(/Add/i));
    fireEvent.change(screen.getByPlaceholderText('Enter the variable name'), { target: { value: 'city' } });
    fireEvent.change(screen.getByPlaceholderText('Enter the value'), { target: { value: 'Delhi' } });

    fireEvent.click(screen.getByRole('dialog'));

    expect(onChangeDynamicVariables).toHaveBeenCalledWith({ city: 'Delhi' });
  });

  it('auto-saves after a debounce even without closing the modal', () => {
    const onChangeDynamicVariables = vi.fn();
    render(
      <TestInputsModal
        open
        onClose={vi.fn()}
        functions={functions}
        dynamicVariables={{}}
        onChangeDynamicVariables={onChangeDynamicVariables}
        functionMocks={{}}
        onChangeFunctionMocks={vi.fn()}
        {...basePreCallProps}
      />
    );

    fireEvent.click(screen.getByText(/Add/i));
    fireEvent.change(screen.getByPlaceholderText('Enter the variable name'), { target: { value: 'lead_id' } });
    fireEvent.change(screen.getByPlaceholderText('Enter the value'), { target: { value: '42' } });

    expect(onChangeDynamicVariables).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(onChangeDynamicVariables).toHaveBeenCalledWith({ lead_id: '42' });
  });

  it('re-seeds local rows from props when reopened', () => {
    const { rerender } = render(
      <TestInputsModal
        open={false}
        onClose={vi.fn()}
        functions={functions}
        dynamicVariables={{ existing: 'value' }}
        onChangeDynamicVariables={vi.fn()}
        functionMocks={{}}
        onChangeFunctionMocks={vi.fn()}
        {...basePreCallProps}
      />
    );
    rerender(
      <TestInputsModal
        open
        onClose={vi.fn()}
        functions={functions}
        dynamicVariables={{ existing: 'value' }}
        onChangeDynamicVariables={vi.fn()}
        functionMocks={{}}
        onChangeFunctionMocks={vi.fn()}
        {...basePreCallProps}
      />
    );
    expect(screen.getByDisplayValue('existing')).toBeInTheDocument();
    expect(screen.getByDisplayValue('value')).toBeInTheDocument();
  });

  it('persists pre-call parameter overrides on close, separately from dynamic variables', () => {
    const onChangePreCallParams = vi.fn();
    render(
      <TestInputsModal
        open
        onClose={vi.fn()}
        functions={functions}
        dynamicVariables={{}}
        onChangeDynamicVariables={vi.fn()}
        functionMocks={{}}
        onChangeFunctionMocks={vi.fn()}
        preCallParams={{}}
        onChangePreCallParams={onChangePreCallParams}
      />
    );

    fireEvent.click(screen.getByRole('tab', { name: 'Pre-call Parameters' }));
    fireEvent.click(screen.getByText(/Add/i));
    fireEvent.change(screen.getByPlaceholderText('Enter the param name'), { target: { value: 'owner_name' } });
    fireEvent.change(screen.getByPlaceholderText('Enter the value'), { target: { value: 'Priya Sharma' } });

    fireEvent.click(screen.getByLabelText('Close'));

    expect(onChangePreCallParams).toHaveBeenCalledWith({ owner_name: 'Priya Sharma' });
  });
});
