import React, { useCallback, useMemo, useState } from 'react';
import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
} from '@xyflow/react';
import type { Connection, Edge, Node, NodeChange, EdgeChange } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Eye, GitBranch, Plus, Save } from 'lucide-react';
import type { Bot as BotType, BotVersion, Flow, FlowNode, FlowNodeType } from '../api';
import { api } from '../api';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Dialog } from '../components/Dialog';
import { flowNodeTypes, NODE_TYPE_META } from '../components/FlowGraphNode';

// Node-graph builder for a bot's conversation flow (Phase 2b, carried into v1 per the
// STT->LLM->TTS architecture — see nocode_platform_alignment.md). The graph is stored
// inside BotConfig.flow, so it is versioned/published/rolled-back with everything else
// rather than as a parallel system (Bland-style named-node determinism + Vapi-style
// no-code accessibility).

function toRfNodes(nodes: FlowNode[]): Node[] {
  return nodes.map((n) => ({
    id: n.id,
    position: n.position,
    data: { flowNode: n },
    type: 'flowNode',
    width: 200,
  }));
}

function toRfEdges(edges: Flow['edges']): Edge[] {
  return edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.label || e.condition || undefined,
    animated: Boolean(e.condition),
  }));
}

let nodeCounter = 0;
function nextId(prefix: string): string {
  nodeCounter += 1;
  return `${prefix}_${Date.now()}_${nodeCounter}`;
}

export function FlowBuilderView({
  selectedBot,
  editingVersion,
  flow,
  onChange,
  onSave,
  saveState,
}: {
  selectedBot?: BotType;
  editingVersion?: BotVersion;
  flow: Flow;
  onChange: (flow: Flow) => void;
  onSave: () => void;
  saveState?: 'idle' | 'running' | 'failed';
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string>('');
  const [pendingRemovalIds, setPendingRemovalIds] = useState<string[] | null>(null);
  const [previewText, setPreviewText] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');

  function loadPreview() {
    setPreviewLoading(true);
    setPreviewError('');
    setPreviewText('');
    api.compileFlowPreview(flow)
      .then((res) => setPreviewText(res.compiled_prompt))
      .catch((err) => {
        setPreviewError(err instanceof Error ? err.message : 'Failed to compile preview.');
        setPreviewText(null);
      })
      .finally(() => setPreviewLoading(false));
  }

  const rfNodes = useMemo(() => toRfNodes(flow.nodes), [flow.nodes]);
  const rfEdges = useMemo(() => toRfEdges(flow.edges), [flow.edges]);

  const selectedNode = flow.nodes.find((n) => n.id === selectedNodeId);

  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const removeIds = changes.filter((c) => c.type === 'remove').map((c) => c.id);
      const positionChanges = changes.filter((c) => c.type !== 'remove');
      if (removeIds.length > 0) {
        setPendingRemovalIds(removeIds);
      }
      if (positionChanges.length === 0) return;

      const updated = applyNodeChanges(positionChanges, rfNodes);
      const byId = new Map(flow.nodes.map((n) => [n.id, n]));
      const nextNodes: FlowNode[] = updated
        .map((rf) => {
          const original = byId.get(rf.id);
          if (!original) return null;
          return { ...original, position: rf.position };
        })
        .filter((n): n is FlowNode => n !== null);
      onChange({ ...flow, nodes: nextNodes });
    },
    [flow, onChange, rfNodes]
  );

  function confirmRemoveNodes() {
    if (!pendingRemovalIds) return;
    const idSet = new Set(pendingRemovalIds);
    onChange({
      ...flow,
      nodes: flow.nodes.filter((n) => !idSet.has(n.id)),
      edges: flow.edges.filter((e) => !idSet.has(e.source) && !idSet.has(e.target)),
    });
    if (selectedNodeId && idSet.has(selectedNodeId)) setSelectedNodeId('');
    setPendingRemovalIds(null);
  }

  function cancelRemoveNodes() {
    setPendingRemovalIds(null);
  }

  const handleEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const updated = applyEdgeChanges(changes, rfEdges);
      const byId = new Map(flow.edges.map((e) => [e.id, e]));
      const nextEdges = updated
        .map((rf) => byId.get(rf.id))
        .filter((e): e is Flow['edges'][number] => Boolean(e));
      onChange({ ...flow, edges: nextEdges });
    },
    [flow, onChange, rfEdges]
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      const newEdge = { id: nextId('edge'), source: connection.source!, target: connection.target!, label: '', condition: '' };
      const rfResult = addEdge(connection, rfEdges);
      if (rfResult.length > rfEdges.length) {
        onChange({ ...flow, edges: [...flow.edges, newEdge] });
      }
    },
    [flow, onChange, rfEdges]
  );

  function addNode(type: FlowNodeType) {
    const id = nextId(type);
    const node: FlowNode = {
      id,
      type,
      position: { x: 80 + flow.nodes.length * 40, y: 80 + (flow.nodes.length % 5) * 90 },
      data: type === 'message' ? { text: 'New message…' } : type === 'condition' ? { expression: '' } : {},
    };
    onChange({ ...flow, nodes: [...flow.nodes, node] });
    setSelectedNodeId(id);
  }

  function updateSelectedNodeData(patch: Record<string, unknown>) {
    if (!selectedNode) return;
    onChange({
      ...flow,
      nodes: flow.nodes.map((n) => (n.id === selectedNode.id ? { ...n, data: { ...n.data, ...patch } } : n)),
    });
  }

  function updateSelectedNodeType(type: FlowNodeType) {
    if (!selectedNode) return;
    onChange({ ...flow, nodes: flow.nodes.map((n) => (n.id === selectedNode.id ? { ...n, type } : n)) });
  }

  function deleteSelectedNode() {
    if (!selectedNode) return;
    setPendingRemovalIds([selectedNode.id]);
  }

  if (!selectedBot) {
    return (
      <div className="panel">
        <p>Select an agent first — the flow you build here is stored on that agent's draft version.</p>
      </div>
    );
  }

  return (
    <section className="flow-builder-grid">
      <div className="panel" style={{ padding: '0.75rem' }}>
        <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', marginBottom: '0.6rem' }}>
          Add node
        </div>
        {(Object.keys(NODE_TYPE_META) as FlowNodeType[]).map((type) => (
          <button
            key={type}
            onClick={() => addNode(type)}
            title={`Add a ${NODE_TYPE_META[type].label.toLowerCase()} node`}
            style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', width: '100%', marginBottom: '0.4rem', justifyContent: 'flex-start' }}
          >
            <span style={{ color: NODE_TYPE_META[type].color, display: 'inline-flex' }}>{NODE_TYPE_META[type].icon}</span>
            {NODE_TYPE_META[type].label}
            <Plus size={13} style={{ marginLeft: 'auto', color: 'var(--muted)' }} />
          </button>
        ))}
        {flow.nodes.length > 0 && (
          <p style={{ fontSize: '0.76rem', color: 'var(--muted)', marginTop: '0.75rem', paddingTop: '0.6rem', borderTop: '1px solid var(--border)' }}>
            Drag from the dot at the bottom of a node to the dot at the top of another to connect them.
          </p>
        )}
        <div style={{ marginTop: '1rem', fontSize: '0.78rem', color: 'var(--muted)' }}>
          {editingVersion ? `Editing v${editingVersion.version} (${editingVersion.state})` : 'No draft selected'}
        </div>
        <button
          style={{ marginTop: '0.75rem', width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.4rem' }}
          disabled={flow.nodes.length === 0 || previewLoading}
          onClick={loadPreview}
        >
          <Eye size={14} /> {previewLoading ? 'Compiling…' : 'Preview compiled prompt'}
        </button>
        <button className="primary" style={{ marginTop: '0.5rem', width: '100%' }} disabled={saveState === 'running'} onClick={onSave}>
          <Save size={14} /> {saveState === 'running' ? 'Saving…' : saveState === 'failed' ? 'Retry save' : 'Save flow'}
        </button>
      </div>

      <div className="panel" style={{ padding: 0, overflow: 'hidden', position: 'relative' }}>
        {flow.nodes.length === 0 && (
          <div className="flow-canvas-hint">
            <GitBranch size={22} />
            <strong>This flow is empty</strong>
            <p>Add a node from the palette on the left to start building the conversation graph.</p>
          </div>
        )}
        <ReactFlowProvider>
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={flowNodeTypes}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onConnect={handleConnect}
            onNodeClick={(_, node) => setSelectedNodeId(node.id)}
            onPaneClick={() => setSelectedNodeId('')}
            fitView
          >
            <Background />
            <Controls />
          </ReactFlow>
        </ReactFlowProvider>
      </div>

      <div className="panel" style={{ padding: '0.75rem' }}>
        <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', marginBottom: '0.6rem' }}>
          Node inspector
        </div>
        {!selectedNode ? (
          <p style={{ fontSize: '0.82rem', color: 'var(--muted)' }}>Select a node to edit it, or add one from the palette on the left.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            <label>
              Type
              <select value={selectedNode.type} onChange={(e) => updateSelectedNodeType(e.target.value as FlowNodeType)}>
                {(Object.keys(NODE_TYPE_META) as FlowNodeType[]).map((type) => (
                  <option key={type} value={type}>{NODE_TYPE_META[type].label}</option>
                ))}
              </select>
            </label>
            {selectedNode.type === 'message' && (
              <label>
                Spoken text
                <textarea
                  rows={4}
                  value={(selectedNode.data.text as string) || ''}
                  onChange={(e) => updateSelectedNodeData({ text: e.target.value })}
                />
              </label>
            )}
            {selectedNode.type === 'condition' && (
              <label>
                Condition expression
                <input
                  value={(selectedNode.data.expression as string) || ''}
                  placeholder="e.g. lead.intent == 'interested'"
                  onChange={(e) => updateSelectedNodeData({ expression: e.target.value })}
                />
              </label>
            )}
            {selectedNode.type === 'tool_call' && (
              <label>
                Tool / API name
                <input
                  value={(selectedNode.data.tool_name as string) || ''}
                  placeholder="e.g. fetch_lead"
                  onChange={(e) => updateSelectedNodeData({ tool_name: e.target.value })}
                />
              </label>
            )}
            {selectedNode.type === 'transfer' && (
              <label>
                Transfer to bot (Squads-lite handoff)
                <input
                  value={(selectedNode.data.target_bot_id as string) || ''}
                  placeholder="Target bot ID"
                  onChange={(e) => updateSelectedNodeData({ target_bot_id: e.target.value })}
                />
              </label>
            )}
            <button className="danger-button full" onClick={deleteSelectedNode}>Delete node</button>
          </div>
        )}
      </div>

      {pendingRemovalIds && (
        <ConfirmDialog
          title={pendingRemovalIds.length > 1 ? `Delete ${pendingRemovalIds.length} nodes?` : 'Delete node?'}
          description="This removes the node and any edges connected to it from the flow. This can't be undone once you save."
          confirmLabel="Delete"
          tone="danger"
          onCancel={cancelRemoveNodes}
          onConfirm={confirmRemoveNodes}
        />
      )}

      {previewText !== null && (
        <Dialog
          title="Compiled prompt preview"
          icon={<Eye size={17} />}
          maxWidth={640}
          onClose={() => setPreviewText(null)}
          footer={<button className="primary" onClick={() => setPreviewText(null)}>Close</button>}
        >
          <p style={{ fontSize: '0.8rem', color: 'var(--muted)', marginBottom: '0.75rem' }}>
            This is the exact step-script text appended to the agent's system prompt from this flow. The
            model is instructed to follow it, but — unlike a strict state machine — it can still
            skip or reorder steps in edge cases.
          </p>
          {previewLoading ? (
            <p style={{ fontSize: '0.82rem', color: 'var(--muted)' }}>Compiling…</p>
          ) : previewError ? (
            <p style={{ color: 'var(--error)' }}>{previewError}</p>
          ) : (
            <pre style={{
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: '0.78rem',
              lineHeight: 1.5,
              background: 'var(--surface-2)',
              border: '1px solid var(--border)',
              borderRadius: 8,
              padding: '0.75rem',
              maxHeight: '55vh',
              overflowY: 'auto',
            }}>
              {previewText || '(empty flow — nothing will be compiled)'}
            </pre>
          )}
        </Dialog>
      )}
    </section>
  );
}
