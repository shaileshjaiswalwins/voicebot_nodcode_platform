import React from 'react';
import { Handle, Position } from '@xyflow/react';
import { GitBranch, MessageSquare, PhoneOff, Split, Wrench } from 'lucide-react';
import type { FlowNode, FlowNodeType } from '../api';

export const NODE_TYPE_META: Record<FlowNodeType, { label: string; icon: React.ReactNode; color: string; bg: string }> = {
  message: { label: 'Message', icon: <MessageSquare size={13} />, color: '#2563eb', bg: '#eff6ff' },
  condition: { label: 'Condition', icon: <Split size={13} />, color: '#d97706', bg: '#fffbeb' },
  tool_call: { label: 'Tool call', icon: <Wrench size={13} />, color: '#7c3aed', bg: '#f5f3ff' },
  transfer: { label: 'Transfer', icon: <GitBranch size={13} />, color: '#0891b2', bg: '#ecfeff' },
  end: { label: 'End call', icon: <PhoneOff size={13} />, color: '#dc2626', bg: '#fef2f2' },
};

function nodeBody(node: FlowNode): string {
  switch (node.type) {
    case 'message':
      return (node.data.text as string) || 'Empty message — click to edit';
    case 'condition':
      return (node.data.expression as string) || 'No condition set';
    case 'tool_call':
      return (node.data.tool_name as string) ? `Calls ${node.data.tool_name as string}` : 'No tool selected';
    case 'transfer':
      return (node.data.target_bot_id as string) ? `To bot ${node.data.target_bot_id as string}` : 'No target bot';
    case 'end':
      return 'Ends the call';
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

  return (
    <div
      className="flow-graph-node"
      style={{
        borderColor: meta.color,
        boxShadow: selected ? `0 0 0 2px ${meta.color}55` : undefined,
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: meta.color }} />
      <div className="flow-graph-node-header" style={{ background: meta.bg, color: meta.color }}>
        <span className="flow-graph-node-icon">{meta.icon}</span>
        <span className="flow-graph-node-type">{meta.label}</span>
      </div>
      <div className="flow-graph-node-body">{body}</div>
      <Handle type="source" position={Position.Bottom} style={{ background: meta.color }} />
    </div>
  );
}

export const flowNodeTypes = { flowNode: FlowGraphNode };
