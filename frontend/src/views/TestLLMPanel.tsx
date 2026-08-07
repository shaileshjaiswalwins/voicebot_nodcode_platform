import React, { useEffect, useRef, useState } from 'react';
import { Bot, Sparkles, User } from 'lucide-react';
import { api } from '../api';
import type { ChatTurn } from '../api';
import type { DynamicVariables, FunctionMocks } from '../components/TestInputsModal';

const MAX_SIMULATED_TURNS = 8;

function ChatTranscript({ turns, pending }: { turns: ChatTurn[]; pending?: boolean }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }); }, [turns.length, pending]);

  return (
    <div className="chat-transcript">
      {turns.map((turn, idx) => (
        <div key={idx} className={turn.role === 'user' ? 'chat-turn user' : 'chat-turn'}>
          <div className="chat-avatar">{turn.role === 'user' ? <User size={14} /> : <Bot size={14} />}</div>
          <div className="chat-bubble"><p>{turn.text}</p></div>
        </div>
      ))}
      {pending && (
        <div className="chat-turn">
          <div className="chat-avatar"><Bot size={14} /></div>
          <div className="chat-bubble"><p className="muted">…</p></div>
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}

/** Manual Chat: tester talks straight to the bot's LLM as text, no LiveKit/voice involved. */
function ManualChat({
  botId,
  systemPrompt,
  dynamicVariables,
  functionMocks,
}: {
  botId: string;
  systemPrompt: string;
  dynamicVariables: DynamicVariables;
  functionMocks: FunctionMocks;
}) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');

  async function send() {
    const text = input.trim();
    if (!text || sending) return;
    const nextTurns = [...turns, { role: 'user' as const, text }];
    setTurns(nextTurns);
    setInput('');
    setSending(true);
    setError('');
    try {
      const { text: reply } = await api.llmChatReply(botId, systemPrompt, nextTurns, dynamicVariables, functionMocks);
      setTurns([...nextTurns, { role: 'bot', text: reply }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Manual chat failed.');
    } finally {
      setSending(false);
    }
  }

  return (
    <div>
      <ChatTranscript turns={turns} pending={sending} />
      {!turns.length && <p className="muted" style={{ fontSize: 'var(--font-size-md)' }}>Type a message below to start chatting with the bot's LLM directly.</p>}
      {error && <div className="notice error" role="alert">{error}</div>}
      <div className="agent-chat-input" style={{ marginTop: '0.6rem' }}>
        <input
          placeholder="Type a message…"
          value={input}
          disabled={sending}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); send(); } }}
        />
        <button className="primary" onClick={send} disabled={sending || !input.trim()}>Send</button>
      </div>
    </div>
  );
}

/** AI Simulated Chat: a caller-persona LLM and the bot's LLM converse turn-by-turn — each pair
 * renders as soon as it comes back, instead of waiting for a full transcript at the end. */
function SimulatedChat({
  botId,
  systemPrompt,
  dynamicVariables,
  functionMocks,
}: {
  botId: string;
  systemPrompt: string;
  dynamicVariables: DynamicVariables;
  functionMocks: FunctionMocks;
}) {
  const [persona, setPersona] = useState('');
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');

  async function simulate() {
    if (!persona.trim() || running) return;
    setRunning(true);
    setError('');
    setTurns([]);
    let history: ChatTurn[] = [];
    try {
      for (let i = 0; i < MAX_SIMULATED_TURNS; i++) {
        const result = await api.llmChatSimulateTurn(botId, systemPrompt, persona, history, dynamicVariables, functionMocks);
        if (result.ended || !result.caller_text) break;
        history = [...history, { role: 'user', text: result.caller_text }, { role: 'bot', text: result.bot_text || '' }];
        setTurns(history);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Simulated chat failed.');
    } finally {
      setRunning(false);
    }
  }

  return (
    <div>
      <label className="full">
        User Prompt
        <small>Provide a persona to generate the simulated caller's responses.</small>
        <textarea
          rows={3}
          placeholder="You are a customer who wants to return a package…"
          value={persona}
          disabled={running}
          onChange={(e) => setPersona(e.target.value)}
        />
      </label>
      <button className="primary" onClick={simulate} disabled={running || !persona.trim()} style={{ marginTop: '0.5rem' }}>
        <Sparkles size={13} /> {running ? 'Simulating…' : 'Simulate Conversation'}
      </button>
      {error && <div className="notice error" role="alert" style={{ marginTop: '0.5rem' }}>{error}</div>}
      <div style={{ marginTop: '0.75rem' }}>
        <ChatTranscript turns={turns} pending={running} />
      </div>
    </div>
  );
}

export function TestLLMPanel({
  botId,
  systemPrompt,
  dynamicVariables,
  functionMocks,
}: {
  botId: string;
  systemPrompt: string;
  dynamicVariables: DynamicVariables;
  functionMocks: FunctionMocks;
}) {
  const [subMode, setSubMode] = useState<'manual' | 'simulated'>('manual');

  return (
    <div>
      <div className="mode-toggle" role="tablist" style={{ marginBottom: '0.75rem' }}>
        <button role="tab" aria-selected={subMode === 'manual'} className={subMode === 'manual' ? 'mode-btn active' : 'mode-btn'} onClick={() => setSubMode('manual')}>
          Manual Chat
        </button>
        <button role="tab" aria-selected={subMode === 'simulated'} className={subMode === 'simulated' ? 'mode-btn active' : 'mode-btn'} onClick={() => setSubMode('simulated')}>
          AI Simulated Chat
        </button>
      </div>
      {subMode === 'manual' ? (
        <ManualChat botId={botId} systemPrompt={systemPrompt} dynamicVariables={dynamicVariables} functionMocks={functionMocks} />
      ) : (
        <SimulatedChat botId={botId} systemPrompt={systemPrompt} dynamicVariables={dynamicVariables} functionMocks={functionMocks} />
      )}
    </div>
  );
}
