import React, { useEffect, useMemo, useState } from 'react';
import { IndianRupee, Check } from 'lucide-react';
import { api, type PricingTier } from '../api';

type PricingMatrix = { stt: Record<string, number>; llm: Record<string, number>; tts: Record<string, number>; telephony: Record<string, number> };

const LABELS: Record<string, string> = {
  sarvam_saras_v3: 'Sarvam Saras v3', deepgram_nova2: 'Deepgram Nova-2', whisper_large: 'Whisper Large', deepgram_flux: 'Deepgram Flux',
  gemini_2_5_flash: 'Gemini 2.5 Flash', gemini_3_1_flash_lite: 'Gemini 3.1 Flash-Lite', gpt_4o_mini: 'GPT-4o-mini', gpt_4o: 'GPT-4o', claude_3_5_sonnet: 'Claude 3.5 Sonnet',
  sarvam_bulbul_v3: 'Sarvam Bulbul v3', deepgram_aura: 'Deepgram Aura', cartesia_sonic: 'Cartesia Sonic', elevenlabs_turbo: 'ElevenLabs Turbo',
  inhouse_dialer: 'In-house Dialer', sip_direct: 'SIP Direct', plivo: 'Plivo',
};

const TIER_ACCENT: Record<string, string> = {
  Budget: '#2f9e6f',
  'Current default': '#3468d1',
  Performance: '#b9770e',
  Ultra: '#9b3fc7',
};

function isSameStack(tier: PricingTier, stt: string, llm: string, tts: string, telephony: string) {
  return tier.stt === stt && tier.llm === llm && tier.tts === tts && tier.telephony === telephony;
}

/** Live INR/min cost estimator + budget-based auto stack selector. Reads its rate table
 * and named tiers from the backend's pricing.py (GET /api/pricing/matrix, /tiers) so the
 * numbers can never drift out of sync between frontend display and what the budget router
 * actually computes. Tier cards mirror the PRD's Budget/Performance/Ultra comparison table. */
export function BudgetCostWidget({ onStackChange }: { onStackChange?: (stack: { stt: string; llm: string; tts: string; telephony: string; cost: number }) => void }) {
  const [matrix, setMatrix] = useState<PricingMatrix | null>(null);
  const [tiers, setTiers] = useState<PricingTier[]>([]);
  const [stt, setStt] = useState('sarvam_saras_v3');
  const [llm, setLlm] = useState('gemini_3_1_flash_lite');
  const [tts, setTts] = useState('sarvam_bulbul_v3');
  const [telephony, setTelephony] = useState('sip_direct');
  const [budget, setBudget] = useState(2.72);
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    api.pricingMatrix().then(setMatrix).catch(() => setMatrix(null));
    api.pricingTiers().then(setTiers).catch(() => setTiers([]));
  }, []);

  const cost = useMemo(() => {
    if (!matrix) return 0;
    return (matrix.stt[stt] || 0) + (matrix.llm[llm] || 0) + (matrix.tts[tts] || 0) + (matrix.telephony[telephony] || 0);
  }, [matrix, stt, llm, tts, telephony]);

  useEffect(() => {
    onStackChange?.({ stt, llm, tts, telephony, cost });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stt, llm, tts, telephony, cost]);

  function applyTier(tier: PricingTier) {
    setStt(tier.stt); setLlm(tier.llm); setTts(tier.tts); setTelephony(tier.telephony);
    setBudget(tier.cost_per_min);
  }

  async function applyBudget(value: number) {
    setBudget(value);
    try {
      const route = await api.pricingBudgetRoute(value);
      setStt(route.stt); setLlm(route.llm); setTts(route.tts); setTelephony(route.telephony);
    } catch {
      // budget-route call failing shouldn't block manual dropdown selection
    }
  }

  if (!matrix) return null;

  return (
    <div className="panel compact budget-cost-widget">
      <div className="budget-cost-header">
        <strong>Model stack &amp; cost</strong>
        <span className="budget-cost-total"><IndianRupee size={14} />{cost.toFixed(2)}<span className="budget-cost-unit">/min</span></span>
      </div>

      <div className="tier-card-row">
        {tiers.map((tier) => {
          const active = isSameStack(tier, stt, llm, tts, telephony);
          const accent = TIER_ACCENT[tier.name] || '#666';
          return (
            <button
              key={tier.name}
              type="button"
              className={`tier-card${active ? ' tier-card-active' : ''}`}
              style={{ '--tier-accent': accent } as React.CSSProperties}
              onClick={() => applyTier(tier)}
            >
              {active && <span className="tier-card-check"><Check size={12} /></span>}
              <span className="tier-card-name">{tier.name}</span>
              <span className="tier-card-price"><IndianRupee size={11} />{tier.cost_per_min.toFixed(2)}<small>/min</small></span>
              <span className="tier-card-stack">{LABELS[tier.stt] || tier.stt}</span>
              <span className="tier-card-stack">{LABELS[tier.llm] || tier.llm}</span>
              <span className="tier-card-stack">{LABELS[tier.tts] || tier.tts}</span>
            </button>
          );
        })}
      </div>

      <label className="budget-slider-label">
        Auto-select by budget: ₹{budget.toFixed(2)}/min
        <input type="range" min={2} max={15} step={0.25} value={budget} onChange={(e) => applyBudget(Number(e.target.value))} />
      </label>

      <button type="button" className="link-toggle" onClick={() => setShowAdvanced((v) => !v)}>
        {showAdvanced ? 'Hide' : 'Show'} advanced per-provider selection
      </button>

      {showAdvanced && (
        <div className="budget-cost-dropdowns">
          <select value={stt} onChange={(e) => setStt(e.target.value)}>
            {Object.keys(matrix.stt).map((k) => <option key={k} value={k}>{LABELS[k] || k} (₹{matrix.stt[k]})</option>)}
          </select>
          <select value={llm} onChange={(e) => setLlm(e.target.value)}>
            {Object.keys(matrix.llm).map((k) => <option key={k} value={k}>{LABELS[k] || k} (₹{matrix.llm[k]})</option>)}
          </select>
          <select value={tts} onChange={(e) => setTts(e.target.value)}>
            {Object.keys(matrix.tts).map((k) => <option key={k} value={k}>{LABELS[k] || k} (₹{matrix.tts[k]})</option>)}
          </select>
          <select value={telephony} onChange={(e) => setTelephony(e.target.value)}>
            {Object.keys(matrix.telephony).map((k) => <option key={k} value={k}>{LABELS[k] || k} (₹{matrix.telephony[k]})</option>)}
          </select>
        </div>
      )}
    </div>
  );
}
