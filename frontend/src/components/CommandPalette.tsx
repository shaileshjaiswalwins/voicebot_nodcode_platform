import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Bot, ChevronRight, FileText, Megaphone, Search, XCircle } from 'lucide-react';
import type { View, CmdKResult, CmdKExtra } from '../types';
import type { Bot as BotType, Campaign, Transcript } from '../api';
import { CMD_VIEWS } from '../constants/ui';
import { matchScore, highlight } from '../utils/matching';

export function CommandPalette({ bots, campaigns, transcripts, onClose, onNavigate }: {
  bots: BotType[];
  campaigns: Campaign[];
  transcripts: Transcript[];
  onClose: () => void;
  onNavigate: (view: View, extra?: CmdKExtra) => void;
}) {
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const q = query.trim();

  const results: CmdKResult[] = useMemo(() => {
    const out: CmdKResult[] = [];

    // Views — always shown when query is empty, or when it matches
    CMD_VIEWS.forEach(v => {
      if (!q || matchScore(v.label, q) > 0 || matchScore(v.description || '', q) > 0) {
        out.push(v);
      }
    });

    if (q) {
      // Bots
      bots.forEach(bot => {
        const score = Math.max(
          matchScore(bot.name, q),
          matchScore(bot.description || '', q),
          matchScore(bot.assistant_id || '', q)
        );
        if (score > 0) out.push({ kind: 'bot', bot });
      });

      // Campaigns
      campaigns.forEach(campaign => {
        const score = Math.max(
          matchScore(campaign.name, q),
          matchScore(campaign.campaign_key, q)
        );
        if (score > 0) out.push({ kind: 'campaign', campaign });
      });

      // Transcripts — search recent ones (last 200, which is already what's loaded)
      const recent = transcripts.slice(0, 200);
      recent.forEach(t => {
        const score = Math.max(
          matchScore(t.call_id || '', q),
          matchScore(t.lead_id || '', q),
          matchScore(t.campaign_id || '', q),
          matchScore(t.status || '', q)
        );
        if (score > 0) out.push({ kind: 'transcript', transcript: t });
      });
    }

    return out.slice(0, 12);
  }, [q, bots, campaigns, transcripts]);

  // Reset cursor when results change
  useEffect(() => { setCursor(0); }, [results.length, q]);

  function selectResult(result: CmdKResult) {
    if (result.kind === 'view') { onNavigate(result.view); return; }
    if (result.kind === 'bot') { onNavigate('bots', { botId: result.bot._id }); return; }
    if (result.kind === 'campaign') { onNavigate('campaigns', { campaignKey: result.campaign.campaign_key }); return; }
    if (result.kind === 'transcript') { onNavigate('transcripts', { transcriptId: result.transcript._id }); return; }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setCursor(c => Math.min(c + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setCursor(c => Math.max(c - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (results[cursor]) selectResult(results[cursor]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  }

  // Scroll active item into view
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${cursor}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [cursor]);

  const grouped = useMemo(() => {
    const out: Array<{ label: string; items: Array<{ result: CmdKResult; idx: number }> }> = [];
    const groups: Record<string, { label: string; items: Array<{ result: CmdKResult; idx: number }> }> = {};
    let globalIdx = 0;
    results.forEach(r => {
      const g = r.kind === 'view' ? 'Views' : r.kind === 'bot' ? 'Agents' : r.kind === 'campaign' ? 'Campaigns' : 'Transcripts';
      if (!groups[g]) { groups[g] = { label: g, items: [] }; out.push(groups[g]); }
      groups[g].items.push({ result: r, idx: globalIdx++ });
    });
    return out;
  }, [results]);

  function resultIcon(r: CmdKResult) {
    if (r.kind === 'view') return r.icon;
    if (r.kind === 'bot') return <Bot size={15} />;
    if (r.kind === 'campaign') return <Megaphone size={15} />;
    return <FileText size={15} />;
  }

  function resultLabel(r: CmdKResult) {
    if (r.kind === 'view') return highlight(r.label, q);
    if (r.kind === 'bot') return highlight(r.bot.name, q);
    if (r.kind === 'campaign') return highlight(r.campaign.name, q);
    return highlight(r.transcript.call_id || r.transcript._id, q);
  }

  function resultSub(r: CmdKResult) {
    if (r.kind === 'view') return r.description || '';
    if (r.kind === 'bot') return r.bot.description || r.bot.assistant_id || '';
    if (r.kind === 'campaign') return r.campaign.campaign_key;
    return `${r.transcript.status || 'unknown'} · ${r.transcript.campaign_id || ''}`;
  }

  return (
    <div className="cmdk-backdrop" onClick={onClose}>
      <div className="cmdk-panel" onClick={e => e.stopPropagation()}>
        <div className="cmdk-input-row">
          <Search size={16} className="cmdk-search-icon" aria-hidden />
          <input
            ref={inputRef}
            className="cmdk-input"
            placeholder="Search agents, campaigns, transcripts…"
            aria-label="Search agents, campaigns, transcripts"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          {query && (
            <button className="cmdk-clear" onClick={() => { setQuery(''); inputRef.current?.focus(); }}>
              <XCircle size={15} />
            </button>
          )}
        </div>

        <div className="cmdk-results" ref={listRef}>
          {results.length === 0 && (
            <div className="cmdk-empty">No results for "{query}"</div>
          )}
          {grouped.map(group => (
            <div key={group.label} className="cmdk-group">
              <div className="cmdk-group-label">{group.label}</div>
              {group.items.map(({ result, idx }) => (
                <button
                  key={idx}
                  data-idx={idx}
                  className={`cmdk-item ${cursor === idx ? 'active' : ''}`}
                  onClick={() => selectResult(result)}
                  onMouseEnter={() => setCursor(idx)}
                >
                  <span className="cmdk-item-icon">{resultIcon(result)}</span>
                  <span className="cmdk-item-body">
                    <span className="cmdk-item-label">{resultLabel(result)}</span>
                    {resultSub(result) && <span className="cmdk-item-sub">{resultSub(result)}</span>}
                  </span>
                  <ChevronRight size={13} className="cmdk-item-arrow" />
                </button>
              ))}
            </div>
          ))}
        </div>

        <div className="cmdk-footer">
          <span><kbd className="kbd" style={{ fontSize: '10px' }}>↑↓</kbd> navigate</span>
          <span><kbd className="kbd" style={{ fontSize: '10px' }}>↵</kbd> open</span>
          <span><kbd className="kbd" style={{ fontSize: '10px' }}>Esc</kbd> close</span>
        </div>
      </div>
    </div>
  );
}
