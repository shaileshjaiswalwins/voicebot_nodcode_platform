import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CostBreakdownPopover } from './CostBreakdownPopover';
import type { AgentCostEstimate } from '../utils/agentCost';

const ESTIMATE: AgentCostEstimate = {
  totalCostInrPerMin: 2.72,
  llmEntry: { key: 'gemini_3_1_flash_lite', label: 'gemini-3.1-flash-lite', cost_inr_per_min: 0.87 },
  ttsEntry: { key: 'sarvam_bulbul_v3', label: 'sarvam-bulbul-v3', cost_inr_per_min: 1.60 },
  sttEntry: { key: 'sarvam_saras_v3', label: 'sarvam-saras-v3', cost_inr_per_min: 0.25 },
  telephonyEntry: { key: 'sip_direct', label: 'sip-direct', cost_inr_per_min: 0 },
};

describe('CostBreakdownPopover', () => {
  it('does not render the breakdown until hovered', () => {
    render(
      <CostBreakdownPopover estimate={ESTIMATE}>
        <span>₹2.72/min</span>
      </CostBreakdownPopover>
    );
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });

  it('shows LLM/TTS/STT/Telephony rows and the total on hover', () => {
    render(
      <CostBreakdownPopover estimate={ESTIMATE}>
        <span>₹2.72/min</span>
      </CostBreakdownPopover>
    );
    fireEvent.mouseEnter(screen.getByText('₹2.72/min').parentElement!);

    expect(screen.getByText(/LLM: gemini-3.1-flash-lite/)).toBeInTheDocument();
    expect(screen.getByText(/TTS: sarvam-bulbul-v3/)).toBeInTheDocument();
    expect(screen.getByText(/STT: sarvam-saras-v3/)).toBeInTheDocument();
    expect(screen.getByText(/Telephony: sip-direct/)).toBeInTheDocument();
    expect(screen.getByText('₹2.720/min')).toBeInTheDocument();
  });

  it('hides again on mouse leave', () => {
    render(
      <CostBreakdownPopover estimate={ESTIMATE}>
        <span>₹2.72/min</span>
      </CostBreakdownPopover>
    );
    const wrapper = screen.getByText('₹2.72/min').parentElement!;
    fireEvent.mouseEnter(wrapper);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.mouseLeave(wrapper);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });
});
