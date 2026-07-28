import React from 'react';
import { Handle, Position } from '@xyflow/react';
import { MessageSquare, Phone, PhoneOff, Play, Radio, Split, Zap } from 'lucide-react';
import type { WorkflowNode, WorkflowNodeKind } from '../api';

export const WF_NODE_TYPE_META: Record<WorkflowNodeKind, { label: string; icon: React.ReactNode; color: string; bg: string }> = {
  start: { label: 'Start', icon: <Play size={13} />, color: '#059669', bg: '#ecfdf5' },
  conversation: { label: 'Conversation', icon: <MessageSquare size={13} />, color: '#2563eb', bg: '#eff6ff' },
  condition: { label: 'Condition', icon: <Split size={13} />, color: '#7c3aed', bg: '#f5f3ff' },
  function: { label: 'Function (API Call)', icon: <Zap size={13} />, color: '#d97706', bg: '#fffbeb' },
  global: { label: 'Global', icon: <Radio size={13} />, color: '#a21caf', bg: '#fdf4ff' },
  end_call: { label: 'End Call', icon: <PhoneOff size={13} />, color: '#dc2626', bg: '#fef2f2' },
};

/** Start and Global are reached without an incoming connection (Start is the implicit entry,
 * Global fires from anywhere its trigger matches), same as the old Flow schema. */
export function wfHasTargetHandle(kind: WorkflowNodeKind): boolean {
  return kind !== 'start' && kind !== 'global';
}

export type WfOutcome = { id: string; label: string };

/** One named outcome per node = one connector dot, kept in sync with workflow_engine.py's
 * WorkflowGraph.resolve()/_target_of() reading of the same `data` shape. */
export function wfNodeOutcomes(node: WorkflowNode): WfOutcome[] {
  const data = node.data;
  switch (data.kind) {
    case 'start':
      return [{ id: 'out', label: '' }];
    case 'conversation': {
      const transitions = data.transitions || [];
      return transitions.length
        ? transitions.map((t) => ({ id: t.id, label: t.label || t.key || '' }))
        : [{ id: 'default', label: 'Continue' }];
    }
    case 'condition': {
      const conditions = data.conditions || [];
      return conditions.map((c) => ({
        id: c.id,
        label: c.is_fallback ? 'Fallback' : `${c.path || '?'} ${c.op || 'eq'} ${c.value ?? ''}`.trim(),
      }));
    }
    case 'function':
      return [{ id: 'done', label: 'Continue' }];
    case 'global':
      return data.action === 'continue' ? [{ id: 'resume', label: 'Resume flow' }] : [];
    case 'end_call':
    default:
      return [];
  }
}

function wfNodeBody(node: WorkflowNode): string {
  const data = node.data;
  switch (data.kind) {
    case 'start':
      return data.first_message || 'No greeting set';
    case 'conversation':
      return data.prompt || 'Empty prompt — click to edit';
    case 'condition': {
      const n = (data.conditions || []).length;
      return n ? `${n} condition${n > 1 ? 's' : ''}` : 'No conditions set';
    }
    case 'function': {
      const url = data.function?.url || '';
      const method = data.function?.method || 'GET';
      return url ? `${method} ${url}` : 'No endpoint set';
    }
    case 'global':
      return data.trigger_description || 'No trigger set';
    case 'end_call':
      return data.closing_message || 'Ends the call';
    default:
      return '';
  }
}

export interface WorkflowGraphNodeData {
  wfNode: WorkflowNode;
  selected?: boolean;
  [key: string]: unknown;
}

/** Visual card for one WorkflowGraphDef node — the drag-and-drop, PM-friendly counterpart to
 * the old FlowGraphNode, adapted to the workflow_engine.py schema (kind/transitions/conditions/
 * function/action instead of type/transitions/rules/url+method/action). */
export function WorkflowGraphNode({ data, selected }: { data: WorkflowGraphNodeData; selected?: boolean }) {
  const node = data.wfNode;
  const kind = node.data.kind;
  const meta = WF_NODE_TYPE_META[kind];
  const body = wfNodeBody(node);
  const outcomes = wfNodeOutcomes(node);
  const isTerminal = kind === 'end_call';
  const isGlobal = kind === 'global';

  return (
    <div
      className={`flow-graph-node${isTerminal ? ' flow-graph-node-terminal' : ''}${isGlobal ? ' flow-graph-node-global' : ''}`}
      style={{ borderColor: meta.color, boxShadow: selected ? `0 0 0 2px ${meta.color}55` : undefined }}
    >
      {wfHasTargetHandle(kind) && <Handle type="target" position={Position.Top} style={{ background: meta.color }} />}
      <div className="flow-graph-node-header" style={{ background: meta.bg, color: meta.color }}>
        <span className="flow-graph-node-icon">{meta.icon}</span>
        <span className="flow-graph-node-type">{node.data.label || meta.label}</span>
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

export const workflowNodeTypes = { workflowNode: WorkflowGraphNode };
