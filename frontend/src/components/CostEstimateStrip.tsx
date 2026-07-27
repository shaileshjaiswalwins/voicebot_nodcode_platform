import React from 'react';
import { DollarSign } from 'lucide-react';
import type { AgentCostEstimate } from '../utils/agentCost';

function formatInr(value: number): string {
  return `₹${value.toFixed(2)}/min`;
}

export function CostEstimateStrip({ estimate }: { estimate: AgentCostEstimate }) {
  return (
    <div className="cost-estimate-strip">
      <DollarSign size={16} />
      <span>Estimated cost: <span className="cost-value">{formatInr(estimate.totalCostInrPerMin)}</span></span>
      <span className="cost-breakdown">
        {estimate.sttEntry && `STT ${formatInr(estimate.sttEntry.cost_inr_per_min)}`}
        {estimate.ttsEntry && ` · TTS ${formatInr(estimate.ttsEntry.cost_inr_per_min)}`}
        {estimate.llmEntry && ` · LLM ${formatInr(estimate.llmEntry.cost_inr_per_min)}`}
        {estimate.telephonyEntry && ` · Telephony ${formatInr(estimate.telephonyEntry.cost_inr_per_min)}`}
      </span>
    </div>
  );
}
