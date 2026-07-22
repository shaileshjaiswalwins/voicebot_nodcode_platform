import { describe, it, expect } from 'vitest';
import { normalizeModelKey, estimateAgentCost } from './agentCost';
import type { PricingConfig } from '../api';

describe('normalizeModelKey', () => {
  it('lowercases and replaces non-alphanumeric runs with underscores', () => {
    expect(normalizeModelKey('gemini-3.1-flash-lite')).toBe('gemini_3_1_flash_lite');
  });

  it('trims leading/trailing underscores', () => {
    expect(normalizeModelKey('  GPT-4o  ')).toBe('gpt_4o');
  });
});

const PRICING: PricingConfig = {
  stt: [{ key: 'sarvam_saras_v3', label: 'sarvam-saras-v3', cost_inr_per_min: 0.25 }],
  llm: [
    { key: 'gemini_3_1_flash_lite', label: 'gemini-3.1-flash-lite', cost_inr_per_min: 0.87, latency_ms_min: 970, latency_ms_max: 1450, tokens_min: 597, tokens_max: 1000 },
    { key: 'gpt_4o', label: 'gpt-4o', cost_inr_per_min: 3.85, latency_ms_min: 1100, latency_ms_max: 1700, tokens_min: 650, tokens_max: 1100 },
  ],
  tts: [{ key: 'sarvam_bulbul_v3', label: 'sarvam-bulbul-v3', cost_inr_per_min: 1.60 }],
  telephony: [{ key: 'sip_direct', label: 'sip-direct', cost_inr_per_min: 0 }],
};

describe('estimateAgentCost', () => {
  it('matches an exact model string to its normalized pricing key', () => {
    const est = estimateAgentCost({ llm_model: 'gpt-4o', tts_model: 'sarvam-bulbul-v3' }, PRICING);
    expect(est.llmEntry?.key).toBe('gpt_4o');
    expect(est.ttsEntry?.key).toBe('sarvam_bulbul_v3');
    expect(est.totalCostInrPerMin).toBeCloseTo(3.85 + 1.60 + 0.25 + 0);
  });

  it('falls back to the first entry when the model field is blank', () => {
    const est = estimateAgentCost({}, PRICING);
    expect(est.llmEntry?.key).toBe('gemini_3_1_flash_lite');
    expect(est.ttsEntry?.key).toBe('sarvam_bulbul_v3');
  });

  it('falls back to the first entry when the model string matches nothing', () => {
    const est = estimateAgentCost({ llm_model: 'totally-unknown-model' }, PRICING);
    expect(est.llmEntry?.key).toBe('gemini_3_1_flash_lite');
  });

  it('surfaces latency and token range from the matched LLM entry', () => {
    const est = estimateAgentCost({ llm_model: 'gemini-3.1-flash-lite' }, PRICING);
    expect(est.latencyMinMs).toBe(970);
    expect(est.latencyMaxMs).toBe(1450);
    expect(est.tokensMin).toBe(597);
    expect(est.tokensMax).toBe(1000);
  });

  it('includes STT and telephony entries in the total', () => {
    const est = estimateAgentCost({}, PRICING);
    expect(est.sttEntry?.key).toBe('sarvam_saras_v3');
    expect(est.telephonyEntry?.key).toBe('sip_direct');
  });
});
