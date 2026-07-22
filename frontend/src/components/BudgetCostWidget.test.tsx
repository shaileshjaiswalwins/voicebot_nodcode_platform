import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { BudgetCostWidget } from './BudgetCostWidget';
import { api } from '../api';

vi.mock('../api', () => ({
  api: {
    pricingMatrix: vi.fn(),
    pricingTiers: vi.fn(),
    pricingBudgetRoute: vi.fn(),
  },
}));

const MATRIX = {
  stt: { sarvam_saras_v3: 0.25, deepgram_nova2: 0.41 },
  llm: { gemini_3_1_flash_lite: 0.87, gpt_4o: 3.85 },
  tts: { sarvam_bulbul_v3: 1.60, elevenlabs_turbo: 5.76 },
  telephony: { sip_direct: 0.0, plivo: 0.55 },
};

const TIERS = [
  { name: 'Budget', stt: 'deepgram_nova2', llm: 'gemini_3_1_flash_lite', tts: 'sarvam_bulbul_v3', telephony: 'plivo', cost_per_min: 2.5 },
  { name: 'Current default', stt: 'sarvam_saras_v3', llm: 'gemini_3_1_flash_lite', tts: 'sarvam_bulbul_v3', telephony: 'sip_direct', cost_per_min: 2.72 },
  { name: 'Ultra', stt: 'deepgram_nova2', llm: 'gpt_4o', tts: 'elevenlabs_turbo', telephony: 'plivo', cost_per_min: 10.57 },
];

function totalCostText() {
  return document.querySelector('.budget-cost-total')?.textContent || '';
}

beforeEach(() => {
  vi.mocked(api.pricingMatrix).mockResolvedValue(MATRIX);
  vi.mocked(api.pricingTiers).mockResolvedValue(TIERS);
});

describe('BudgetCostWidget', () => {
  it('renders the default stack total cost from the fetched matrix', async () => {
    render(<BudgetCostWidget />);
    await waitFor(() => {
      expect(totalCostText()).toContain('2.72');
    });
  });

  it('renders one tier card per named tier from the backend, cheapest included', async () => {
    render(<BudgetCostWidget />);
    await waitFor(() => {
      expect(screen.getByText('Budget')).toBeInTheDocument();
      expect(screen.getByText('Ultra')).toBeInTheDocument();
    });
  });

  it('clicking a tier card applies that stack and updates total cost', async () => {
    render(<BudgetCostWidget />);
    await waitFor(() => expect(screen.getByText('Ultra')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Ultra'));

    await waitFor(() => {
      expect(totalCostText()).toContain('10.57');
    });
  });

  it('applies a budget-route result when the slider changes', async () => {
    vi.mocked(api.pricingBudgetRoute).mockResolvedValue({
      stt: 'deepgram_nova2', llm: 'gpt_4o', tts: 'elevenlabs_turbo', telephony: 'plivo', estimated_cost_per_min: 10.57,
    });
    render(<BudgetCostWidget />);
    await waitFor(() => expect(totalCostText()).toContain('2.72'));

    const slider = screen.getByRole('slider');
    fireEvent.change(slider, { target: { value: '15' } });

    await waitFor(() => {
      expect(api.pricingBudgetRoute).toHaveBeenCalledWith(15);
    });
    await waitFor(() => {
      expect(totalCostText()).toContain('10.57');
    });
  });

  it('advanced per-provider dropdowns are hidden until toggled', async () => {
    render(<BudgetCostWidget />);
    await waitFor(() => expect(screen.getByText('Budget')).toBeInTheDocument());
    expect(screen.queryAllByRole('combobox')).toHaveLength(0);

    fireEvent.click(screen.getByText(/Show advanced/));
    expect(screen.getAllByRole('combobox').length).toBe(4);
  });
});
