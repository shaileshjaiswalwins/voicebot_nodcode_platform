import React, { useCallback, useMemo, useState } from 'react';
import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
} from '@xyflow/react';
import type { Connection, Edge, Node, NodeChange, EdgeChange, XYPosition } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { BookOpen, CheckCircle2, Eye, GitBranch, Phone, Plus, Save, XCircle } from 'lucide-react';
import type { Bot as BotType, BotVersion, Flow, FlowNode, FlowNodeType } from '../api';
import { api } from '../api';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Dialog } from '../components/Dialog';
import { flowNodeTypes, nodeOutcomes, NODE_TYPE_META } from '../components/FlowGraphNode';

// Node-graph builder for a bot's conversation flow (Phase 2b, carried into v1 per the
// STT->LLM->TTS architecture — see nocode_platform_alignment.md). The graph is stored
// inside BotConfig.flow, so it is versioned/published/rolled-back with everything else
// rather than as a parallel system (Bland-style named-node determinism + Vapi-style
// no-code accessibility).

const PALETTE_TYPES: FlowNodeType[] = ['start', 'message', 'condition', 'tool_call', 'global', 'end'];
const PALETTE_DESCRIPTIONS: Record<FlowNodeType, string> = {
  start: 'The entry point — every flow needs exactly one',
  message: 'A real LLM turn with its own narrow prompt',
  condition: 'Rule-based, not an LLM guess — branches on a stored value',
  tool_call: 'Calls a webhook mid-call, stores the result',
  transfer: 'Hands the call off to another agent',
  global: 'Reachable from anywhere, fires when its trigger matches',
  end: 'Closes the call',
};
const DND_TYPE = 'application/flow-node-type';

const DEFAULT_DATA: Record<FlowNodeType, Record<string, unknown>> = {
  start: { first_message: '' },
  message: { prompt: '', transitions: [], variables: [] },
  condition: { rules: [] },
  tool_call: { url: '', method: 'GET', output_key: '' },
  transfer: {},
  global: { trigger_description: '', action: 'end_call' },
  end: { closing_message: '' },
};

function toRfNodes(nodes: FlowNode[]): Node[] {
  return nodes.map((n) => ({
    id: n.id,
    position: n.position,
    data: { flowNode: n },
    type: 'flowNode',
    width: 220,
  }));
}

function toRfEdges(nodes: FlowNode[], edges: Flow['edges']): Edge[] {
  const nodesById = new Map(nodes.map((n) => [n.id, n]));
  return edges.map((e) => {
    const sourceNode = nodesById.get(e.source);
    const outcome = sourceNode && e.source_handle ? nodeOutcomes(sourceNode).find((o) => o.id === e.source_handle) : undefined;
    const label = e.label || outcome?.label || e.condition || undefined;
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.source_handle || undefined,
      label,
      animated: Boolean(e.condition || e.source_handle),
    };
  });
}

function validateFlow(flow: Flow): string[] {
  const issues: string[] = [];
  if (flow.nodes.length === 0) return issues;
  const startNodes = flow.nodes.filter((n) => n.type === 'start');
  if (startNodes.length === 0) issues.push('Add a Start node — every flow needs exactly one entry point.');
  if (startNodes.length > 1) issues.push('Only one Start node is allowed — remove the extra one.');
  if (!flow.nodes.some((n) => n.type === 'end')) issues.push('Add at least one End Call node so the bot knows how to hang up.');
  return issues;
}

let nodeCounter = 0;
function nextId(prefix: string): string {
  nodeCounter += 1;
  return `${prefix}_${Date.now()}_${nodeCounter}`;
}

function FlowPalette({ flow, onAddNode }: { flow: Flow; onAddNode: (type: FlowNodeType, position?: XYPosition) => void }) {
  const hasStart = flow.nodes.some((n) => n.type === 'start');
  return (
    <>
      <div style={{ fontSize: 'var(--font-size-xs)', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', marginBottom: '0.6rem' }}>
        Drag to canvas
      </div>
      {PALETTE_TYPES.map((type) => {
        const disabled = type === 'start' && hasStart;
        const meta = NODE_TYPE_META[type];
        return (
          <div
            key={type}
            draggable={!disabled}
            onDragStart={(e) => {
              if (disabled) return;
              e.dataTransfer.setData(DND_TYPE, type);
              e.dataTransfer.effectAllowed = 'move';
            }}
            onClick={() => !disabled && onAddNode(type)}
            title={disabled ? 'Only one Start node is allowed' : `Click to add at centre, drag to position — ${PALETTE_DESCRIPTIONS[type]}`}
            className="flow-palette-card"
            style={{ borderColor: meta.color + '33', background: meta.bg, color: meta.color, opacity: disabled ? 0.45 : 1, cursor: disabled ? 'not-allowed' : 'grab' }}
          >
            <span className="flow-graph-node-icon">{meta.icon}</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontWeight: 700, fontSize: 'var(--font-size-md)', color: 'var(--text)' }}>{meta.label}</div>
              <div style={{ fontSize: 'var(--font-size-xs)', opacity: 0.75, color: 'var(--muted)' }}>{PALETTE_DESCRIPTIONS[type]}</div>
            </div>
            <Plus size={13} style={{ marginLeft: 'auto', flexShrink: 0 }} />
          </div>
        );
      })}
      <p style={{ fontSize: 'var(--font-size-sm)', color: 'var(--muted)', marginTop: '0.75rem', paddingTop: '0.6rem', borderTop: '1px solid var(--border)' }}>
        Click to add at centre, drag to position. Then drag from a node's colored dot to the next node to connect them.
      </p>
    </>
  );
}

function FlowCanvas({
  flow,
  onAddNode,
  children,
}: {
  flow: Flow;
  onAddNode: (type: FlowNodeType, position: XYPosition) => void;
  children: React.ReactNode;
}) {
  const { screenToFlowPosition } = useReactFlow();
  return (
    <div
      style={{ width: '100%', height: '100%' }}
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
      }}
      onDrop={(e) => {
        e.preventDefault();
        const type = e.dataTransfer.getData(DND_TYPE) as FlowNodeType;
        if (!type) return;
        if (type === 'start' && flow.nodes.some((n) => n.type === 'start')) return;
        const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
        onAddNode(type, position);
      }}
    >
      {children}
    </div>
  );
}

export function FlowBuilderView({
  selectedBot,
  editingVersion,
  flow,
  onChange,
  onSave,
  saveState,
  onNavigateTest,
}: {
  selectedBot?: BotType;
  editingVersion?: BotVersion;
  flow: Flow;
  onChange: (flow: Flow) => void;
  onSave: () => void;
  saveState?: 'idle' | 'running' | 'failed';
  onNavigateTest?: () => void;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string>('');
  const [pendingRemovalIds, setPendingRemovalIds] = useState<string[] | null>(null);
  const [previewText, setPreviewText] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [guideOpen, setGuideOpen] = useState(false);

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
  const rfEdges = useMemo(() => toRfEdges(flow.nodes, flow.edges), [flow.nodes, flow.edges]);
  const issues = useMemo(() => validateFlow(flow), [flow]);

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
      const newEdge = {
        id: nextId('edge'),
        source: connection.source!,
        target: connection.target!,
        source_handle: connection.sourceHandle || '',
        label: '',
        condition: '',
      };
      const rfResult = addEdge(connection, rfEdges);
      if (rfResult.length > rfEdges.length) {
        onChange({ ...flow, edges: [...flow.edges, newEdge] });
      }
    },
    [flow, onChange, rfEdges]
  );

  function addNode(type: FlowNodeType, position?: XYPosition) {
    if (type === 'start' && flow.nodes.some((n) => n.type === 'start')) return;
    const id = nextId(type);
    const node: FlowNode = {
      id,
      type,
      position: position || { x: 80 + flow.nodes.length * 40, y: 80 + (flow.nodes.length % 5) * 90 },
      data: { ...DEFAULT_DATA[type] },
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

  /** Removing a transition/rule also drops any edge wired to that outcome's handle, so the
   * canvas never shows a dangling connector pointing at nothing. */
  function removeOutcome(listKey: 'transitions' | 'rules', outcomeId: string) {
    if (!selectedNode) return;
    const list = ((selectedNode.data[listKey] as { id: string }[] | undefined) || []).filter((o) => o.id !== outcomeId);
    onChange({
      ...flow,
      nodes: flow.nodes.map((n) => (n.id === selectedNode.id ? { ...n, data: { ...n.data, [listKey]: list } } : n)),
      edges: flow.edges.filter((e) => !(e.source === selectedNode.id && e.source_handle === outcomeId)),
    });
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
    <section style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
      <div className="flow-toolbar">
        <div className="flow-toolbar-title">
          <strong>{selectedBot.name}</strong>
          <span className={issues.length ? 'flow-status-badge invalid' : 'flow-status-badge valid'}>
            {issues.length ? <XCircle size={12} /> : <CheckCircle2 size={12} />}
            {issues.length ? `${issues.length} issue${issues.length > 1 ? 's' : ''}` : 'Valid'}
          </span>
        </div>
        <div className="flow-toolbar-actions">
          <button onClick={() => setGuideOpen(true)}>
            <BookOpen size={14} /> Node guide
          </button>
          {onNavigateTest && (
            <button onClick={onNavigateTest}>
              <Phone size={14} /> Test
            </button>
          )}
          <button className="primary" disabled={saveState === 'running'} onClick={onSave}>
            <Save size={14} /> {saveState === 'running' ? 'Saving…' : saveState === 'failed' ? 'Retry save' : 'Save'}
          </button>
        </div>
      </div>

      <div className="flow-builder-grid">
        <div className="panel" style={{ padding: '0.75rem' }}>
          <FlowPalette flow={flow} onAddNode={addNode} />
          <div style={{ marginTop: '1rem', fontSize: 'var(--font-size-sm)', color: 'var(--muted)' }}>
            {editingVersion ? `Editing v${editingVersion.version} (${editingVersion.state})` : 'No draft selected'}
          </div>
          <button
            style={{ marginTop: '0.75rem', width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.4rem' }}
            disabled={flow.nodes.length === 0 || previewLoading}
            onClick={loadPreview}
          >
            <Eye size={14} /> {previewLoading ? 'Compiling…' : 'Preview compiled prompt'}
          </button>
        </div>

        <div className="panel" style={{ padding: 0, overflow: 'hidden', position: 'relative' }}>
          {flow.nodes.length === 0 && (
            <div className="flow-canvas-hint">
              <GitBranch size={22} />
              <strong>This flow is empty</strong>
              <p>Drag a node from the palette on the left to start building the conversation graph.</p>
            </div>
          )}
          <ReactFlowProvider>
            <FlowCanvas flow={flow} onAddNode={addNode}>
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
            </FlowCanvas>
          </ReactFlowProvider>
        </div>

        <div className="panel" style={{ padding: '0.75rem' }}>
          {!selectedNode ? (
            <>
              <div style={{ fontSize: 'var(--font-size-xs)', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', marginBottom: '0.6rem' }}>
                Flow overview
              </div>
              <p style={{ fontSize: 'var(--font-size-md)', color: 'var(--muted)' }}>Click any node to configure it.</p>
              <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.35rem', fontSize: 'var(--font-size-sm)' }}>
                {(Object.keys(NODE_TYPE_META) as FlowNodeType[]).map((type) => {
                  const count = flow.nodes.filter((n) => n.type === type).length;
                  if (!count) return null;
                  return (
                    <div key={type} style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--muted)' }}>
                      <span>{NODE_TYPE_META[type].label}</span>
                      <span>{count}</span>
                    </div>
                  );
                })}
              </div>
              <div style={{ marginTop: '0.9rem', paddingTop: '0.75rem', borderTop: '1px solid var(--border)' }}>
                {issues.length === 0 ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: 'var(--success, #059669)', fontSize: 'var(--font-size-md)' }}>
                    <CheckCircle2 size={14} /> No issues
                  </div>
                ) : (
                  <ul style={{ margin: 0, paddingLeft: '1.1rem', display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                    {issues.map((issue) => (
                      <li key={issue} style={{ fontSize: 'var(--font-size-sm)', color: 'var(--error)' }}>{issue}</li>
                    ))}
                  </ul>
                )}
              </div>
            </>
          ) : (
            <>
              <div style={{ fontSize: 'var(--font-size-xs)', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', marginBottom: '0.6rem' }}>
                Node inspector
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                <label>
                  Type
                  <select
                    value={selectedNode.type}
                    onChange={(e) => {
                      const type = e.target.value as FlowNodeType;
                      onChange({ ...flow, nodes: flow.nodes.map((n) => (n.id === selectedNode.id ? { ...n, type, data: { ...DEFAULT_DATA[type] } } : n)) });
                    }}
                  >
                    {(Object.keys(NODE_TYPE_META) as FlowNodeType[]).map((type) => (
                      <option key={type} value={type} disabled={type === 'start' && flow.nodes.some((n) => n.type === 'start' && n.id !== selectedNode.id)}>
                        {NODE_TYPE_META[type].label}
                      </option>
                    ))}
                  </select>
                </label>

                {selectedNode.type === 'start' && (
                  <label>
                    Opening greeting (first_message)
                    <textarea
                      rows={3}
                      value={(selectedNode.data.first_message as string) || ''}
                      onChange={(e) => updateSelectedNodeData({ first_message: e.target.value })}
                    />
                  </label>
                )}

                {selectedNode.type === 'message' && (
                  <>
                    <label>
                      Prompt (this node's own narrow job)
                      <textarea
                        rows={4}
                        value={(selectedNode.data.prompt as string) || ''}
                        onChange={(e) => updateSelectedNodeData({ prompt: e.target.value })}
                      />
                    </label>
                    <div>
                      <div style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, marginBottom: '0.35rem' }}>Transitions</div>
                      {((selectedNode.data.transitions as { id: string; label: string }[] | undefined) || []).map((t) => (
                        <div key={t.id} style={{ display: 'flex', gap: '0.35rem', marginBottom: '0.35rem' }}>
                          <input
                            style={{ flex: 1 }}
                            value={t.label}
                            placeholder="e.g. caller wants to book an appointment"
                            onChange={(e) => {
                              const transitions = ((selectedNode.data.transitions as { id: string; label: string }[]) || []).map((x) =>
                                x.id === t.id ? { ...x, label: e.target.value } : x
                              );
                              updateSelectedNodeData({ transitions });
                            }}
                          />
                          <button className="danger-button" onClick={() => removeOutcome('transitions', t.id)}>×</button>
                        </div>
                      ))}
                      <button
                        style={{ width: '100%' }}
                        onClick={() => {
                          const transitions = [...(((selectedNode.data.transitions as { id: string; label: string }[]) || [])), { id: nextId('t'), label: '' }];
                          updateSelectedNodeData({ transitions });
                        }}
                      >
                        <Plus size={12} /> Add transition
                      </button>
                    </div>
                  </>
                )}

                {selectedNode.type === 'condition' && (
                  <div>
                    <div style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, marginBottom: '0.35rem' }}>Rules</div>
                    {((selectedNode.data.rules as { id: string; variable: string; operator: string; value: string }[] | undefined) || []).map((r) => (
                      <div key={r.id} style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', marginBottom: '0.5rem', padding: '0.4rem', border: '1px solid var(--border)', borderRadius: 6 }}>
                        <input
                          placeholder="variable"
                          value={r.variable}
                          onChange={(e) => {
                            const rules = ((selectedNode.data.rules as typeof r[]) || []).map((x) => (x.id === r.id ? { ...x, variable: e.target.value } : x));
                            updateSelectedNodeData({ rules });
                          }}
                        />
                        <select
                          value={r.operator}
                          onChange={(e) => {
                            const rules = ((selectedNode.data.rules as typeof r[]) || []).map((x) => (x.id === r.id ? { ...x, operator: e.target.value } : x));
                            updateSelectedNodeData({ rules });
                          }}
                        >
                          <option value="equals">equals</option>
                          <option value="not_equals">not equals</option>
                          <option value="greater_than">greater than</option>
                          <option value="less_than">less than</option>
                          <option value="contains">contains</option>
                          <option value="exists">exists</option>
                        </select>
                        {r.operator !== 'exists' && (
                          <input
                            placeholder="value"
                            value={r.value}
                            onChange={(e) => {
                              const rules = ((selectedNode.data.rules as typeof r[]) || []).map((x) => (x.id === r.id ? { ...x, value: e.target.value } : x));
                              updateSelectedNodeData({ rules });
                            }}
                          />
                        )}
                        <button className="danger-button" onClick={() => removeOutcome('rules', r.id)}>Remove rule</button>
                      </div>
                    ))}
                    <button
                      style={{ width: '100%' }}
                      onClick={() => {
                        const rules = [...(((selectedNode.data.rules as { id: string; variable: string; operator: string; value: string }[]) || [])), { id: nextId('r'), variable: '', operator: 'equals', value: '' }];
                        updateSelectedNodeData({ rules });
                      }}
                    >
                      <Plus size={12} /> Add rule
                    </button>
                    <p style={{ fontSize: 'var(--font-size-xs)', color: 'var(--muted)', marginTop: '0.4rem' }}>
                      Always falls through to a fixed <strong>Else</strong> outcome if no rule matches.
                    </p>
                  </div>
                )}

                {selectedNode.type === 'tool_call' && (
                  <>
                    <label>
                      Endpoint URL
                      <input
                        value={(selectedNode.data.url as string) || ''}
                        placeholder="https://..."
                        onChange={(e) => updateSelectedNodeData({ url: e.target.value })}
                      />
                    </label>
                    <label>
                      Method
                      <select
                        value={(selectedNode.data.method as string) || 'GET'}
                        onChange={(e) => updateSelectedNodeData({ method: e.target.value })}
                      >
                        {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Store response as (output_key)
                      <input
                        value={(selectedNode.data.output_key as string) || ''}
                        placeholder="e.g. lead_lookup"
                        onChange={(e) => updateSelectedNodeData({ output_key: e.target.value })}
                      />
                    </label>
                  </>
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

                {selectedNode.type === 'global' && (
                  <>
                    <label>
                      Trigger description
                      <textarea
                        rows={3}
                        value={(selectedNode.data.trigger_description as string) || ''}
                        placeholder="e.g. caller asks what our business hours are"
                        onChange={(e) => updateSelectedNodeData({ trigger_description: e.target.value })}
                      />
                    </label>
                    <label>
                      Action
                      <select
                        value={(selectedNode.data.action as string) || 'end_call'}
                        onChange={(e) => updateSelectedNodeData({ action: e.target.value })}
                      >
                        <option value="end_call">End call</option>
                        <option value="transfer">Transfer to a number</option>
                        <option value="continue">Continue (then resume flow)</option>
                      </select>
                    </label>
                    {selectedNode.data.action === 'transfer' && (
                      <label>
                        Transfer number
                        <input
                          value={(selectedNode.data.transfer_number as string) || ''}
                          onChange={(e) => updateSelectedNodeData({ transfer_number: e.target.value })}
                        />
                      </label>
                    )}
                    {selectedNode.data.action === 'continue' && (
                      <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexDirection: 'row' }}>
                        <input
                          type="checkbox"
                          checked={Boolean(selectedNode.data.resume)}
                          onChange={(e) => updateSelectedNodeData({ resume: e.target.checked })}
                        />
                        Resume exactly where the caller left off
                      </label>
                    )}
                  </>
                )}

                {selectedNode.type === 'end' && (
                  <label>
                    Closing message
                    <textarea
                      rows={3}
                      value={(selectedNode.data.closing_message as string) || ''}
                      onChange={(e) => updateSelectedNodeData({ closing_message: e.target.value })}
                    />
                  </label>
                )}

                <button className="danger-button full" onClick={deleteSelectedNode}>Delete node</button>
              </div>
            </>
          )}
        </div>
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
          <p style={{ fontSize: 'var(--font-size-md)', color: 'var(--muted)', marginBottom: '0.75rem' }}>
            This is the exact step-script text appended to the agent's system prompt from this flow. The
            model is instructed to follow it, but — unlike a strict state machine — it can still
            skip or reorder steps in edge cases.
          </p>
          {previewLoading ? (
            <p style={{ fontSize: 'var(--font-size-md)', color: 'var(--muted)' }}>Compiling…</p>
          ) : previewError ? (
            <p style={{ color: 'var(--error)' }}>{previewError}</p>
          ) : (
            <pre style={{
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: 'var(--font-size-sm)',
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

      {guideOpen && (
        <Dialog
          title="How workflow nodes work"
          icon={<BookOpen size={17} />}
          maxWidth={560}
          onClose={() => setGuideOpen(false)}
          footer={<button className="primary" onClick={() => setGuideOpen(false)}>Close</button>}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', maxHeight: '55vh', overflowY: 'auto' }}>
            {[
              ['start', 'The entry point — every flow needs exactly one. The opening greeting is spoken the instant the call connects, before anything else happens.'],
              ['message', 'A real LLM turn with its own narrow job — give each Conversation node one focused task rather than one giant do-everything prompt. Add Transitions to hand off to whatever should happen next: each one becomes its own connector dot on the node.'],
              ['condition', "A rule, not an LLM guess. Compares a stored value against a target — equals, not-equals, greater/less-than, contains, or exists. Always falls through to a fixed Else branch for anything the rules don't cover."],
              ['tool_call', 'Calls an external endpoint mid-call and stores the response under output_key, so a later Condition or Conversation node can reference it.'],
              ['transfer', "Hands the call off to a different agent (bot) entirely — this platform's own multi-agent handoff mechanism."],
              ['global', 'Always available — has no incoming connection and is not part of the main flow. Fires from any node whenever its trigger description matches what the caller says, then ends the call, transfers, or continues.'],
              ['end', 'A terminal node — says its closing message, then hangs up. A flow can have as many End Call nodes as it needs, one per distinct outcome.'],
            ].map(([type, description]) => {
              const meta = NODE_TYPE_META[type as FlowNodeType];
              return (
                <div key={type} style={{ display: 'flex', gap: '0.6rem', border: '1px solid var(--border)', borderRadius: 8, padding: '0.6rem' }}>
                  <span style={{ flexShrink: 0, width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 6, background: meta.bg, color: meta.color, border: `1px solid ${meta.color}33` }}>
                    {meta.icon}
                  </span>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 'var(--font-size-lg)' }}>{meta.label}</div>
                    <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--muted)' }}>{description}</div>
                  </div>
                </div>
              );
            })}
            <p style={{ fontSize: 'var(--font-size-sm)', color: 'var(--muted)', marginTop: '0.2rem' }}>
              <strong>Connecting nodes:</strong> drag from a node's colored dot to the next node. Conversation
              and Condition nodes get one dot per transition/rule — connect the one you want to whatever
              should happen next.
            </p>
          </div>
        </Dialog>
      )}
    </section>
  );
}
