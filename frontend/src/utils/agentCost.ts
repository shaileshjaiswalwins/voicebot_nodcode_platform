import type { PricingConfig, PricingModelEntry } from '../api';
import type { RuntimeConfig } from '../types';

/** Normalizes a model string to a pricing-entry key: lowercase, non-alnum runs -> "_".
 * "gemini-3.1-flash-lite" -> "gemini_3_1_flash_lite", matching backend/pricing.py's keys. */
export function normalizeModelKey(raw: string): string {
  return raw.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

function findEntry(entries: PricingModelEntry[], rawModel: string | undefined): PricingModelEntry | undefined {
  if (!entries.length) return undefined;
  if (!rawModel) return entries[0];
  const key = normalizeModelKey(rawModel);
  return entries.find((e) => e.key === key) || entries[0];
}

export type AgentCostEstimate = {
  totalCostInrPerMin: number;
  llmEntry?: PricingModelEntry;
  ttsEntry?: PricingModelEntry;
  sttEntry?: PricingModelEntry;
  telephonyEntry?: PricingModelEntry;
  latencyMinMs?: number;
  latencyMaxMs?: number;
  tokensMin?: number;
  tokensMax?: number;
};

/** Estimates a bot's ₹/min cost + latency/token range from its configured llm_model/
 * tts_model/stt_model against the admin-configured PricingConfig. Falls back to each
 * category's first entry when the bot's model field is blank or unmatched (mirrors
 * RuntimeConfig's "empty = pipeline default" convention already used in backend/models.py).
 * Telephony has no per-bot selector today, so it always falls back to the first entry. */
export function estimateAgentCost(config: RuntimeConfig, pricing: PricingConfig): AgentCostEstimate {
  const llmOptionsModel = config.llm_options?.model;
  const llmModel = (typeof llmOptionsModel === 'string' && llmOptionsModel) || config.llm_model;
  const llmEntry = findEntry(pricing.llm, llmModel);
  const ttsEntry = findEntry(pricing.tts, config.tts_model);
  const sttEntry = findEntry(pricing.stt, config.stt_model);
  const telephonyEntry = findEntry(pricing.telephony, undefined);

  const totalCostInrPerMin =
    (llmEntry?.cost_inr_per_min || 0) +
    (ttsEntry?.cost_inr_per_min || 0) +
    (sttEntry?.cost_inr_per_min || 0) +
    (telephonyEntry?.cost_inr_per_min || 0);

  return {
    totalCostInrPerMin,
    llmEntry,
    ttsEntry,
    sttEntry,
    telephonyEntry,
    latencyMinMs: llmEntry?.latency_ms_min ?? undefined,
    latencyMaxMs: llmEntry?.latency_ms_max ?? undefined,
    tokensMin: llmEntry?.tokens_min ?? undefined,
    tokensMax: llmEntry?.tokens_max ?? undefined,
  };
}
