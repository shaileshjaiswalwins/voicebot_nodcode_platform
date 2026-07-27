import React, { useState } from 'react';
import type { AgentCostEstimate } from '../utils/agentCost';

function formatInr(value: number): string {
  return `₹${value.toFixed(3)}/min`;
}

/** Rich hover popover breaking a bot's total cost down into LLM/TTS/STT/Telephony — same
 * hover-toggle mechanic as Tooltip.tsx, but with its own multi-row bubble markup: Tooltip
 * is single-line, pointer-events:none, white-space:nowrap, which can't render this content. */
export function CostBreakdownPopover({ estimate, children }: { estimate: AgentCostEstimate; children: React.ReactElement }) {
  const [visible, setVisible] = useState(false);
  return (
    <span
      className="cost-breakdown-wrapper"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
      onFocus={() => setVisible(true)}
      onBlur={() => setVisible(false)}
    >
      {children}
      {visible && (
        <div className="agent-details-popover" role="tooltip">
          <div className="agent-details-popover-total">
            <span>Cost per minute</span>
            <strong>{formatInr(estimate.totalCostInrPerMin)}</strong>
          </div>
          <div className="agent-details-popover-divider" />
          <div className="agent-details-popover-rows">
            {estimate.llmEntry && (
              <div className="agent-details-popover-row">
                <span>LLM: {estimate.llmEntry.label}</span>
                <span>{formatInr(estimate.llmEntry.cost_inr_per_min)}</span>
              </div>
            )}
            {estimate.ttsEntry && (
              <div className="agent-details-popover-row">
                <span>TTS: {estimate.ttsEntry.label}</span>
                <span>{formatInr(estimate.ttsEntry.cost_inr_per_min)}</span>
              </div>
            )}
            {estimate.sttEntry && (
              <div className="agent-details-popover-row">
                <span>STT: {estimate.sttEntry.label}</span>
                <span>{formatInr(estimate.sttEntry.cost_inr_per_min)}</span>
              </div>
            )}
            {estimate.telephonyEntry && (
              <div className="agent-details-popover-row">
                <span>Telephony: {estimate.telephonyEntry.label}</span>
                <span>{formatInr(estimate.telephonyEntry.cost_inr_per_min)}</span>
              </div>
            )}
          </div>
        </div>
      )}
    </span>
  );
}
