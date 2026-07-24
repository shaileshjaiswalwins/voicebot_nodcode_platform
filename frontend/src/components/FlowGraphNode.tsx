import React from 'react';
import { Handle, Position } from '@xyflow/react';
import { GitMerge, MessageSquare, PhoneOff, Play, Radio, Split, Zap } from 'lucide-react';
import type { FlowNode, FlowNodeType } from '../api';

export const NODE_TYPE_META: Record<FlowNodeType, { label: string; icon: React.ReactNode; color: string; bg: string }> = {
  start: { label: 'Start', icon: <Play size={13} />, color: '#059669', bg: '#ecfdf5' },
  message: { label: 'Conversation', icon: <MessageSquare size={13} />, color: '#2563eb', bg: '#eff6ff' },
  condition: { label: 'Condition', icon: <Split size={13} />, color: '#7c3aed', bg: '#f5f3ff' },
  tool_call: { label: 'Function (API Call)', icon: <Zap size={13} />, color: '#d97706', bg: '#fffbeb' },
  transfer: { label: 'Transfer', icon: <GitMerge size={13} />, color: '#0891b2', bg: '#ecfeff' },
  global: { label: 'Global', icon: <Radio size={13} />, color: '#a21caf', bg: '#fdf4ff' },
  end: { label: 'End Call', icon: <PhoneOff size={13} />, color: '#dc2626', bg: '#fef2f2' },
};

/** true for node types that can be the target of an incoming edge. Start and Global nodes
 * are reached without a connection (Start is the implicit entry, Global fires from anywhere
 * whenever its trigger matches) so they render no target handle at all. */
export function hasTargetHandle(type: FlowNodeType): boolean {
  return type !== 'start' && type !== 'global';
}

export type FlowOutcome = { id: string; label: string };

/** One named outcome per node = one connector dot on the card. Message nodes get one dot
 * per author-defined transition, Condition nodes one per rule plus a fixed trailing Else,
 * everything else gets zero or one. Kept in sync with flow_compiler.py's reading of the
 * same `data` shape. */
export function nodeOutcomes(node: FlowNode): FlowOutcome[] {
  switch (node.type) {
    case 'start':
      return [{ id: 'out', label: '' }];
    case 'message': {
      const transitions = (node.data.transitions as FlowOutcome[] | undefined) || [];
      return transitions.length ? transitions : [{ id: 'default', label: 'Continue' }];
    }
    case 'condition': {
      const rules = (node.data.rules as { id: string; variable: string; operator: string; value: string }[] | undefined) || [];
      const ruleOutcomes = rules.map((r) => ({
        id: r.id,
        label: `${r.variable || '?'} ${r.operator || 'equals'} ${r.value || ''}`.trim(),
      }));
      return [...ruleOutcomes, { id: 'else', label: 'Else' }];
    }
    case 'tool_call':
      return [{ id: 'done', label: 'Continue' }];
    case 'transfer':
      return [{ id: 'out', label: '' }];
    case 'global':
      return node.data.action === 'continue' ? [{ id: 'resume', label: 'Resume flow' }] : [];
    case 'end':
    default:
      return [];
  }
}

function nodeBody(node: FlowNode): string {
  const data = node.data;
  switch (node.type) {
    case 'start':
      return (data.first_message as string) || 'No greeting set';
    case 'message':
      return (data.prompt as string) || (data.text as string) || 'Empty prompt — click to edit';
    case 'condition': {
      const rules = (data.rules as unknown[] | undefined) || [];
      return rules.length ? `${rules.length} rule${rules.length > 1 ? 's' : ''}, else fall through` : 'No rules set — else fall through';
    }
    case 'tool_call': {
      const url = (data.url as string) || '';
      const method = (data.method as string) || 'GET';
      return url ? `${method} ${url}` : 'No endpoint set';
    }
    case 'transfer':
      return (data.target_bot_id as string) ? `To bot ${data.target_bot_id as string}` : 'No target bot';
    case 'global':
      return (data.trigger_description as string) || 'No trigger set';
    case 'end':
      return (data.closing_message as string) || 'Ends the call';
    default:
      return '';
  }
}

export interface FlowGraphNodeData {
  flowNode: FlowNode;
  selected?: boolean;
  [key: string]: unknown;
}

/** Custom React Flow node — the built-in `default` node type only renders `data.label` as
 * plain text with no icon/color, which is why the graph previously showed empty pills. */
export function FlowGraphNode({ data, selected }: { data: FlowGraphNodeData; selected?: boolean }) {
  const node = data.flowNode;
  const meta = NODE_TYPE_META[node.type];
  const body = nodeBody(node);
  const outcomes = nodeOutcomes(node);
  const isTerminal = node.type === 'end';
  const isGlobal = node.type === 'global';

  return (
    <div
      className={`flow-graph-node${isTerminal ? ' flow-graph-node-terminal' : ''}${isGlobal ? ' flow-graph-node-global' : ''}`}
      style={{
        borderColor: meta.color,
        boxShadow: selected ? `0 0 0 2px ${meta.color}55` : undefined,
      }}
    >
      {hasTargetHandle(node.type) && <Handle type="target" position={Position.Top} style={{ background: meta.color }} />}
      <div className="flow-graph-node-header" style={{ background: meta.bg, color: meta.color }}>
        <span className="flow-graph-node-icon">{meta.icon}</span>
        <span className="flow-graph-node-type">{meta.label}</span>
        {isTerminal && <span className="flow-graph-node-terminal-tag">Terminal</span>}
        {isGlobal && <span className="flow-graph-node-terminal-tag" style={{ color: meta.color, background: meta.bg }}>Global</span>}
      </div>
      <div className="flow-graph-node-body">&ldquo;{body}&rdquo;</div>
      {outcomes.length > 0 && (
        <div className="flow-graph-node-outcomes">
          {outcomes.map((o) => (
            <div key={o.id} className="flow-graph-node-outcome">
              <span>{o.label || 'Continue'}</span>
              <Handle
                type="source"
                position={Position.Right}
                id={o.id}
                style={{ position: 'relative', transform: 'none', top: 'auto', right: 'auto', left: 'auto', background: meta.color }}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export const flowNodeTypes = { flowNode: FlowGraphNode };
