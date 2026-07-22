import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AdminView } from './AdminView';
import type { PricingConfig } from '../api';

function makeConfig(): PricingConfig {
  return {
    stt: [{ key: 'sarvam_saras_v3', label: 'sarvam-saras-v3', cost_inr_per_min: 0.25 }],
    llm: [{ key: 'gemini_3_1_flash_lite', label: 'gemini-3.1-flash-lite', cost_inr_per_min: 0.87, latency_ms_min: 970, latency_ms_max: 1450, tokens_min: 597, tokens_max: 1000 }],
    tts: [{ key: 'sarvam_bulbul_v3', label: 'sarvam-bulbul-v3', cost_inr_per_min: 1.60 }],
    telephony: [{ key: 'sip_direct', label: 'sip-direct', cost_inr_per_min: 0 }],
  };
}

describe('AdminView', () => {
  it('shows a loading state when config is null', () => {
    render(<AdminView config={null} onSave={vi.fn()} />);
    expect(screen.getByText(/Loading pricing configuration/)).toBeInTheDocument();
  });

  it('renders existing entries for every category', () => {
    render(<AdminView config={makeConfig()} onSave={vi.fn()} />);
    expect(screen.getByDisplayValue('gemini-3.1-flash-lite')).toBeInTheDocument();
    expect(screen.getByDisplayValue('sarvam-bulbul-v3')).toBeInTheDocument();
    expect(screen.getByDisplayValue('sarvam-saras-v3')).toBeInTheDocument();
    expect(screen.getByDisplayValue('sip-direct')).toBeInTheDocument();
  });

  it('adding an LLM row shows the latency/token fields', () => {
    render(<AdminView config={makeConfig()} onSave={vi.fn()} />);
    const addButtons = screen.getAllByText(/Add LLM/);
    fireEvent.click(addButtons[0]);
    expect(screen.getAllByText('Latency min (ms)').length).toBe(2);
  });

  it('deleting a row removes it', () => {
    render(<AdminView config={makeConfig()} onSave={vi.fn()} />);
    expect(screen.getByDisplayValue('sip-direct')).toBeInTheDocument();
    const removeButtons = screen.getAllByTitle('Remove');
    fireEvent.click(removeButtons[removeButtons.length - 1]);
    expect(screen.queryByDisplayValue('sip-direct')).not.toBeInTheDocument();
  });

  it('editing the label auto-slugifies the key and calls onSave with it', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<AdminView config={makeConfig()} onSave={onSave} />);
    const llmLabelInput = screen.getByDisplayValue('gemini-3.1-flash-lite');
    fireEvent.change(llmLabelInput, { target: { value: 'GPT 5.1' } });

    fireEvent.click(screen.getByText('Save changes'));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const saved: PricingConfig = onSave.mock.calls[0][0];
    expect(saved.llm[0].key).toBe('gpt_5_1');
    expect(saved.llm[0].label).toBe('GPT 5.1');
  });

  it('shows a failed state and allows retry when save rejects', async () => {
    const onSave = vi.fn().mockRejectedValue(new Error('nope'));
    render(<AdminView config={makeConfig()} onSave={onSave} />);
    fireEvent.click(screen.getByText('Save changes'));
    await waitFor(() => expect(screen.getByText('Retry save')).toBeInTheDocument());
  });
});
