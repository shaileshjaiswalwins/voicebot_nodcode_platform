import React, { useCallback, useMemo, useRef, useState, useEffect } from 'react';
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
import type { Connection, Edge, Node, NodeChange, EdgeChange, ReactFlowInstance, XYPosition } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { BookOpen, CheckCircle2, GitBranch, LayoutGrid, Plus, Sparkles, XCircle } from 'lucide-react';

import type { WorkflowGraphDef, WorkflowNode, WorkflowNodeKind, WorkflowNodeData } from '../api';
import type { CustomFunction } from '../types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Dialog } from '../components/Dialog';
import { workflowNodeTypes, wfNodeOutcomes, WF_NODE_TYPE_META } from '../components/WorkflowGraphNode';

// PM-friendly drag-and-drop canvas for a bot's WorkflowGraphDef (workflow_engine.py's real
// state-machine graph), mirroring FlowBuilderView's mechanics 1:1 but adapted to the newer
// kind/transitions/conditions/function/action schema — see api.ts's Workflow* types.

const PALETTE_KINDS: WorkflowNodeKind[] = ['start', 'conversation', 'condition', 'function', 'global', 'end_call'];
const PALETTE_DESCRIPTIONS: Record<WorkflowNodeKind, string> = {
  start: 'The entry point — every flow needs exactly one',
  conversation: 'A real LLM turn with its own narrow prompt',
  condition: 'Rule-based, not an LLM guess — branches on a stored value',
  function: 'Calls a webhook mid-call, stores the result',
  global: 'Reachable from anywhere, fires when its trigger matches',
  end_call: 'Closes the call',
};
const DND_TYPE = 'application/workflow-node-kind';

const DEFAULT_DATA: Record<WorkflowNodeKind, WorkflowNodeData> = {
  start: { kind: 'start', first_message: '' },
  conversation: { kind: 'conversation', prompt: '', transitions: [], variables: [] },
  condition: { kind: 'condition', conditions: [] },
  function: { kind: 'function', function: { url: '', method: 'GET', headers: {}, query_params: {}, body_format: 'json', custom_body: '' }, output_key: '' },
  global: { kind: 'global', trigger_description: '', action: 'end_call' },
  end_call: { kind: 'end_call', closing_message: '' },
};

function toRfNodes(nodes: WorkflowNode[]): Node[] {
  return nodes.map((n, i) => ({
    id: n.id,
    position: n.position || { x: 80 + i * 40, y: 80 + (i % 5) * 90 },
    data: { wfNode: n },
    type: 'workflowNode',
    width: 220,
  }));
}

function toRfEdges(nodes: WorkflowNode[], edges: WorkflowGraphDef['edges']): Edge[] {
  const nodesById = new Map(nodes.map((n) => [n.id, n]));
  return edges.map((e, i) => {
    const sourceNode = e.source ? nodesById.get(e.source) : undefined;
    const outcome = sourceNode && e.sourceHandle ? wfNodeOutcomes(sourceNode).find((o) => o.id === e.sourceHandle) : undefined;
    // Solid, source-colored lines rather than react-flow's default dashed/marching-ants
    // `animated` style — a call-script graph is a fixed, decided path, not something mid-flight,
    // so a dashed "tentative" line was sending the wrong signal about every edge in the graph.
    const color = sourceNode ? WF_NODE_TYPE_META[sourceNode.data.kind].color : 'var(--muted)';
    return {
      id: e.id || `edge_${i}`,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle || undefined,
      // The floating label on the line itself (which branch this edge is) turned out to matter
      // for readability even though the source node's own outcome chip already names it —
      // removing it made the graph read as a bare wiring diagram. Styled as a solid pill rather
      // than react-flow's plain default text so it doesn't visually merge into the dotted
      // canvas background or the edge line under it.
      label: outcome?.label || undefined,
      labelBgPadding: [6, 4] as [number, number],
      labelBgBorderRadius: 999,
      labelBgStyle: { fill: 'var(--surface)', stroke: color, strokeWidth: 1 },
      labelStyle: { fill: color, fontWeight: 700, fontSize: 11 },
      type: 'smoothstep',
      style: { stroke: color, strokeWidth: 1.75 },
    };
  });
}

function validateWorkflow(wf: WorkflowGraphDef): string[] {
  const issues: string[] = [];
  if (wf.nodes.length === 0) return issues;
  const startNodes = wf.nodes.filter((n) => n.data.kind === 'start');
  if (startNodes.length === 0) issues.push('Add a Start node — every flow needs exactly one entry point.');
  if (startNodes.length > 1) issues.push('Only one Start node is allowed — remove the extra one.');
  if (!wf.nodes.some((n) => n.data.kind === 'end_call')) issues.push('Add at least one End Call node so the bot knows how to hang up.');

  // Reachability — BFS from Start over edges, same walk autoLayout does for row-assignment.
  // Global nodes fire from anywhere by design, so they're exempt from this check.
  const startId = startNodes[0]?.id;
  if (startId) {
    const outgoing = new Map<string, string[]>(wf.nodes.map((n) => [n.id, []]));
    for (const e of wf.edges) {
      if (outgoing.has(e.source)) outgoing.get(e.source)!.push(e.target);
    }
    const reachable = new Set<string>([startId]);
    let frontier = [startId];
    while (frontier.length > 0) {
      const next: string[] = [];
      for (const id of frontier) {
        for (const child of outgoing.get(id) || []) {
          if (!reachable.has(child)) {
            reachable.add(child);
            next.push(child);
          }
        }
      }
      frontier = next;
    }
    for (const n of wf.nodes) {
      if (n.data.kind !== 'global' && !reachable.has(n.id)) {
        issues.push(`"${n.id}" is unreachable — no path from Start leads to it.`);
      }
    }
  }

  // Conversation nodes with no outgoing transition, or a transition not wired to an edge.
  for (const n of wf.nodes) {
    if (n.data.kind !== 'conversation') continue;
    const transitions = n.data.transitions || [];
    if (transitions.length === 0) {
      issues.push(`"${n.id}" is a conversation node with no transitions — the call has nowhere to go after it.`);
      continue;
    }
    const wiredHandles = new Set(wf.edges.filter((e) => e.source === n.id).map((e) => e.sourceHandle));
    for (const t of transitions) {
      if (!wiredHandles.has(t.id)) {
        issues.push(`"${n.id}" transition "${t.label || t.id}" has no outgoing edge connected to it.`);
      }
    }
  }

  // Condition nodes missing a catch-all fallback branch.
  for (const n of wf.nodes) {
    if (n.data.kind !== 'condition') continue;
    const conditions = n.data.conditions || [];
    if (conditions.length === 0) {
      issues.push(`"${n.id}" is a condition node with no conditions defined.`);
    } else if (!conditions.some((c) => c.is_fallback)) {
      issues.push(`"${n.id}" has no fallback branch — add a catch-all condition for values that match none of the others.`);
    }
  }

  return issues;
}

const LAYER_Y_GAP = 260;
const SIBLING_X_GAP = 340;

/** Longest-path layering from the Start node (falls back to the first node if none) — a node's
 * row is 1 + the max row of ALL its parents (computed in topological order via Kahn's
 * algorithm), not just whichever parent BFS happens to visit first. Plain BFS layering gives a
 * node the row of its *shortest* path in, so a node also reachable via a longer path gets an
 * edge that has to stretch across multiple rows to reach it — visually, edges arcing far off
 * the laid-out area. Longest-path layering keeps every edge exactly one row, top-to-bottom
 * (matching this call-script's natural read order and the builder panel's narrow-and-tall
 * aspect ratio), with same-row siblings spread out left to right. Hand-picked y-coordinates
 * (e.g. from a seed script) tend to under-space multi-line prompt cards, which is what caused
 * the overlapping mess this replaces; this ignores stored positions entirely and recomputes a
 * clean layered layout every time it's invoked. */
export function autoLayout(wf: WorkflowGraphDef): WorkflowGraphDef {
  if (wf.nodes.length === 0) return wf;
  const startId = wf.nodes.find((n) => n.data.kind === 'start')?.id || wf.nodes[0].id;

  const outgoing = new Map<string, string[]>(wf.nodes.map((n) => [n.id, []]));
  const incoming = new Map<string, string[]>(wf.nodes.map((n) => [n.id, []]));
  for (const e of wf.edges) {
    if (outgoing.has(e.source) && incoming.has(e.target)) {
      outgoing.get(e.source)!.push(e.target);
      incoming.get(e.target)!.push(e.source);
    }
  }

  const reachable = new Set<string>([startId]);
  let frontier = [startId];
  while (frontier.length > 0) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const child of outgoing.get(id) || []) {
        if (!reachable.has(child)) {
          reachable.add(child);
          next.push(child);
        }
      }
    }
    frontier = next;
  }

  const layer = new Map<string, number>([[startId, 0]]);
  const indegree = new Map<string, number>();
  for (const id of reachable) {
    indegree.set(id, (incoming.get(id) || []).filter((p) => reachable.has(p)).length);
  }
  const queue = [startId];
  while (queue.length > 0) {
    const id = queue.shift()!;
    for (const child of outgoing.get(id) || []) {
      if (!reachable.has(child)) continue;
      layer.set(child, Math.max(layer.get(child) ?? 0, (layer.get(id) ?? 0) + 1));
      indegree.set(child, (indegree.get(child) || 0) - 1);
      if (indegree.get(child) === 0) queue.push(child);
    }
  }

  // Anything unreached from Start (e.g. a Global node, which by design has no incoming edge) —
  // place it one row above whichever of its own targets is already placed, so its outgoing
  // edge stays local instead of spanning the whole canvas; only stack far below as a last
  // resort if it has no placed target either.
  let maxLayer = Math.max(0, ...Array.from(layer.values()));
  let remaining = wf.nodes.map((n) => n.id).filter((id) => !layer.has(id));
  let progress = true;
  while (remaining.length > 0 && progress) {
    progress = false;
    const stillRemaining: string[] = [];
    for (const id of remaining) {
      const targetLayers = (outgoing.get(id) || []).map((t) => layer.get(t)).filter((l): l is number => l !== undefined);
      if (targetLayers.length > 0) {
        layer.set(id, Math.max(0, Math.min(...targetLayers) - 1));
        progress = true;
      } else {
        stillRemaining.push(id);
      }
    }
    remaining = stillRemaining;
  }
  for (const id of remaining) {
    maxLayer += 1;
    layer.set(id, maxLayer);
  }

  const countByLayer = new Map<number, number>();
  const nextNodes = wf.nodes.map((n) => {
    const l = layer.get(n.id) ?? 0;
    const col = countByLayer.get(l) ?? 0;
    countByLayer.set(l, col + 1);
    return { ...n, position: { x: col * SIBLING_X_GAP, y: l * LAYER_Y_GAP } };
  });
  return { ...wf, nodes: nextNodes };
}

let nodeCounter = 0;
function nextId(prefix: string): string {
  nodeCounter += 1;
  return `${prefix}_${Date.now()}_${nodeCounter}`;
}

function WorkflowPalette({ wf, onAddNode }: { wf: WorkflowGraphDef; onAddNode: (kind: WorkflowNodeKind, position?: XYPosition) => void }) {
  const hasStart = wf.nodes.some((n) => n.data.kind === 'start');
  return (
    <>
      <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', marginBottom: '0.6rem' }}>
        Drag to canvas
      </div>
      {PALETTE_KINDS.map((kind) => {
        const disabled = kind === 'start' && hasStart;
        const meta = WF_NODE_TYPE_META[kind];
        return (
          <div
            key={kind}
            draggable={!disabled}
            onDragStart={(e) => {
              if (disabled) return;
              e.dataTransfer.setData(DND_TYPE, kind);
              e.dataTransfer.effectAllowed = 'move';
            }}
            onClick={() => !disabled && onAddNode(kind)}
            title={disabled ? 'Only one Start node is allowed' : `Click to add at centre, drag to position — ${PALETTE_DESCRIPTIONS[kind]}`}
            className="flow-palette-card"
            style={{ borderColor: meta.color + '33', background: meta.bg, color: meta.color, opacity: disabled ? 0.45 : 1, cursor: disabled ? 'not-allowed' : 'grab' }}
          >
            <span className="flow-graph-node-icon">{meta.icon}</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontWeight: 700, fontSize: '0.8rem', color: 'var(--text)' }}>{meta.label}</div>
              <div style={{ fontSize: '0.68rem', opacity: 0.75, color: 'var(--muted)' }}>{PALETTE_DESCRIPTIONS[kind]}</div>
            </div>
            <Plus size={13} style={{ marginLeft: 'auto', flexShrink: 0 }} />
          </div>
        );
      })}
      <p style={{ fontSize: '0.76rem', color: 'var(--muted)', marginTop: '0.75rem', paddingTop: '0.6rem', borderTop: '1px solid var(--border)' }}>
        Click to add at centre, drag to position. Then drag from a node's colored dot to the next node to connect them.
      </p>
    </>
  );
}

function WorkflowCanvas({
  wf,
  onAddNode,
  children,
}: {
  wf: WorkflowGraphDef;
  onAddNode: (kind: WorkflowNodeKind, position: XYPosition) => void;
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
        const kind = e.dataTransfer.getData(DND_TYPE) as WorkflowNodeKind;
        if (!kind) return;
        if (kind === 'start' && wf.nodes.some((n) => n.data.kind === 'start')) return;
        const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
        onAddNode(kind, position);
      }}
    >
      {children}
    </div>
  );
}

/** Sparkles trigger + popover for "Refine workflow with AI" — same interaction shape as
 * BotConfigTabs' PromptAssistButton (click Sparkles, describe the change, apply) but edits a
 * full WorkflowGraphDef instead of a text field, so it needs its own component: the apply step
 * has to update three fields at once (workflow/functions/global_prompt) and re-run autoLayout
 * + refit, none of which the text-only widget's onApply(text: string) shape can express. */
function WorkflowRefineButton({ onRefine, disabled }: { onRefine: (instruction: string) => Promise<void>; disabled?: boolean }) {
  const [instruction, setInstruction] = useState('');
  const [state, setState] = useState<'idle' | 'running' | 'failed'>('idle');
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      // `Node` in this file's scope is @xyflow/react's flow-node type (imported above), not
      // the DOM Node interface — globalThis.Node disambiguates.
      if (ref.current && !ref.current.contains(e.target as globalThis.Node)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const handleRefine = async () => {
    if (!instruction.trim()) return;
    setState('running');
    setError('');
    try {
      await onRefine(instruction.trim());
      setInstruction('');
      setState('idle');
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Refine failed.');
      setState('failed');
    }
  };

  return (
    <div className="prompt-assist" ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
      <button type="button" disabled={disabled} title="Describe a change to make to the graph — add/remove steps, branches, or API calls" onClick={() => setOpen((v) => !v)}>
        <Sparkles size={14} /> Refine with AI
      </button>
      {open && (
        <div className="prompt-assist-popover" style={{ right: 0, left: 'auto', minWidth: '320px' }}>
          <textarea
            rows={3}
            autoFocus
            placeholder='e.g. "add a step before ending the call that asks if they want a reminder text" or "also handle the case where the caller wants to reschedule"'
            value={instruction}
            disabled={state === 'running'}
            onChange={(e) => setInstruction(e.target.value)}
          />
          <button type="button" className="primary" disabled={state === 'running' || !instruction.trim()} onClick={handleRefine}>
            <Sparkles size={13} /> {state === 'running' ? 'Updating graph…' : 'Apply change'}
          </button>
          {state === 'failed' && <div className="notice error" role="alert">{error}</div>}
        </div>
      )}
    </div>
  );
}

export function WorkflowBuilderView({
  workflow,
  onChange,
  functions = [],
  onFunctionsChange,
  globalPrompt = '',
  onGlobalPromptChange,
  onRefineWithAI,
}: {
  workflow: WorkflowGraphDef;
  onChange: (wf: WorkflowGraphDef) => void;
  /** The three fields "Refine with AI" can touch — all optional so existing callers that
   * don't wire them simply don't get the Refine button (see the `onRefineWithAI &&` guard
   * below), rather than needing every call site updated in lockstep with this feature. */
  functions?: CustomFunction[];
  onFunctionsChange?: (fns: CustomFunction[]) => void;
  globalPrompt?: string;
  onGlobalPromptChange?: (text: string) => void;
  onRefineWithAI?: (
    instruction: string,
    currentWorkflow: WorkflowGraphDef,
    currentFunctions: CustomFunction[],
    currentGlobalPrompt: string,
  ) => Promise<{ global_prompt: string; workflow: WorkflowGraphDef; functions: CustomFunction[] }>;
}) {
  const wf = workflow || { nodes: [], edges: [] };
  const [selectedNodeId, setSelectedNodeId] = useState<string>('');
  const [pendingRemovalIds, setPendingRemovalIds] = useState<string[] | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null);

  /** Auto arrange moves every node, which can shift the graph well outside whatever's
   * currently in view — refit once the new positions have rendered rather than leaving the
   * canvas looking empty (the ReactFlow `fitView` prop only ever applies on first mount). */
  function handleAutoArrange() {
    onChange(autoLayout(wf));
    setTimeout(() => rfInstance?.fitView({ padding: 0.2 }), 60);
  }

  /** Applies a "Refine with AI" result the same way handleAutoArrange applies a manual
   * re-layout: swap in the new graph (re-laid-out with the builder's own algorithm, not
   * whatever positions the backend guessed — same reasoning as Create workflow with AI), then
   * refit once the new positions have rendered. Also pushes the returned functions/global_prompt
   * back up, since a refine instruction can touch either. */
  async function handleRefine(instruction: string) {
    if (!onRefineWithAI) return;
    const result = await onRefineWithAI(instruction, wf, functions, globalPrompt);
    onChange(autoLayout(result.workflow));
    onFunctionsChange?.(result.functions);
    onGlobalPromptChange?.(result.global_prompt);
    setTimeout(() => rfInstance?.fitView({ padding: 0.2 }), 60);
  }

  const rfNodes = useMemo(() => toRfNodes(wf.nodes), [wf.nodes]);
  const rfEdges = useMemo(() => toRfEdges(wf.nodes, wf.edges), [wf.nodes, wf.edges]);
  const issues = useMemo(() => validateWorkflow(wf), [wf]);

  const selectedNode = wf.nodes.find((n) => n.id === selectedNodeId);

  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const removeIds = changes.filter((c) => c.type === 'remove').map((c) => c.id);
      const positionChanges = changes.filter((c) => c.type !== 'remove');
      if (removeIds.length > 0) setPendingRemovalIds(removeIds);
      if (positionChanges.length === 0) return;

      const updated = applyNodeChanges(positionChanges, rfNodes);
      const byId = new Map(wf.nodes.map((n) => [n.id, n]));
      const nextNodes: WorkflowNode[] = [];
      for (const rf of updated) {
        const original = byId.get(rf.id);
        if (!original) continue;
        nextNodes.push({ ...original, position: rf.position });
      }
      onChange({ ...wf, nodes: nextNodes });
    },
    [wf, onChange, rfNodes]
  );

  function confirmRemoveNodes() {
    if (!pendingRemovalIds) return;
    const idSet = new Set(pendingRemovalIds);
    onChange({
      ...wf,
      nodes: wf.nodes.filter((n) => !idSet.has(n.id)),
      edges: wf.edges.filter((e) => !idSet.has(e.source) && !idSet.has(e.target)),
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
      const updatedIds = new Set(updated.map((rf) => rf.id));
      const byId = new Map(wf.edges.map((e, i) => [e.id || `edge_${i}`, e]));
      const nextEdges = Array.from(updatedIds)
        .map((id) => byId.get(id))
        .filter((e): e is WorkflowGraphDef['edges'][number] => Boolean(e));
      onChange({ ...wf, edges: nextEdges });
    },
    [wf, onChange, rfEdges]
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      const newEdge = {
        id: nextId('edge'),
        source: connection.source!,
        target: connection.target!,
        sourceHandle: connection.sourceHandle || '',
      };
      const rfResult = addEdge(connection, rfEdges);
      if (rfResult.length > rfEdges.length) {
        onChange({ ...wf, edges: [...wf.edges, newEdge] });
      }
    },
    [wf, onChange, rfEdges]
  );

  function addNode(kind: WorkflowNodeKind, position?: XYPosition) {
    if (kind === 'start' && wf.nodes.some((n) => n.data.kind === 'start')) return;
    const id = nextId(kind);
    const node: WorkflowNode = {
      id,
      position: position || { x: 80 + wf.nodes.length * 40, y: 80 + (wf.nodes.length % 5) * 90 },
      data: { ...DEFAULT_DATA[kind] },
    };
    onChange({ ...wf, nodes: [...wf.nodes, node] });
    setSelectedNodeId(id);
  }

  function updateSelectedNodeData(patch: Partial<WorkflowNodeData>) {
    if (!selectedNode) return;
    onChange({
      ...wf,
      nodes: wf.nodes.map((n) => (n.id === selectedNode.id ? { ...n, data: { ...n.data, ...patch } } : n)),
    });
  }

  /** Removing a transition/condition also drops any edge wired to that outcome's handle, so
   * the canvas never shows a dangling connector pointing at nothing. */
  function removeOutcome(listKey: 'transitions' | 'conditions', outcomeId: string) {
    if (!selectedNode) return;
    const list = ((selectedNode.data[listKey] as { id: string }[] | undefined) || []).filter((o) => o.id !== outcomeId);
    onChange({
      ...wf,
      nodes: wf.nodes.map((n) => (n.id === selectedNode.id ? { ...n, data: { ...n.data, [listKey]: list } } : n)),
      edges: wf.edges.filter((e) => !(e.source === selectedNode.id && e.sourceHandle === outcomeId)),
    });
  }

  function deleteSelectedNode() {
    if (!selectedNode) return;
    setPendingRemovalIds([selectedNode.id]);
  }

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
      <div className="flow-toolbar">
        <div className="flow-toolbar-title">
          <strong>Workflow graph</strong>
          <span className={issues.length ? 'flow-status-badge invalid' : 'flow-status-badge valid'}>
            {issues.length ? <XCircle size={12} /> : <CheckCircle2 size={12} />}
            {issues.length ? `${issues.length} issue${issues.length > 1 ? 's' : ''}` : 'Valid'}
          </span>
        </div>
        <div className="flow-toolbar-actions">
          {onRefineWithAI && <WorkflowRefineButton onRefine={handleRefine} disabled={wf.nodes.length === 0} />}
          <button disabled={wf.nodes.length === 0} onClick={handleAutoArrange} title="Recompute a clean top-to-bottom layout from the Start node — useful after seeding/importing a graph whose positions overlap">
            <LayoutGrid size={14} /> Auto arrange
          </button>
          <button onClick={() => setGuideOpen(true)}>
            <BookOpen size={14} /> Node guide
          </button>
        </div>
      </div>

      <div className="flow-builder-grid">
        <div className="panel" style={{ padding: '0.75rem' }}>
          <WorkflowPalette wf={wf} onAddNode={addNode} />
        </div>

        <div className="panel" style={{ padding: 0, overflow: 'hidden', position: 'relative' }}>
          {wf.nodes.length === 0 && (
            <div className="flow-canvas-hint">
              <GitBranch size={22} />
              <strong>This flow is empty</strong>
              <p>Drag a node from the palette on the left to start building the conversation graph.</p>
            </div>
          )}
          <ReactFlowProvider>
            <WorkflowCanvas wf={wf} onAddNode={addNode}>
              <ReactFlow
                nodes={rfNodes}
                edges={rfEdges}
                nodeTypes={workflowNodeTypes}
                onNodesChange={handleNodesChange}
                onEdgesChange={handleEdgesChange}
                onConnect={handleConnect}
                onNodeClick={(_, node) => setSelectedNodeId(node.id)}
                onPaneClick={() => setSelectedNodeId('')}
                onInit={setRfInstance}
                fitView
              >
                <Background />
                <Controls />
              </ReactFlow>
            </WorkflowCanvas>
          </ReactFlowProvider>
        </div>

        <div className="panel" style={{ padding: '0.75rem' }}>
          {!selectedNode ? (
            <>
              <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', marginBottom: '0.6rem' }}>
                Flow overview
              </div>
              <p style={{ fontSize: '0.82rem', color: 'var(--muted)' }}>Click any node to configure it.</p>
              <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.35rem', fontSize: '0.78rem' }}>
                {(Object.keys(WF_NODE_TYPE_META) as WorkflowNodeKind[]).map((kind) => {
                  const count = wf.nodes.filter((n) => n.data.kind === kind).length;
                  if (!count) return null;
                  return (
                    <div key={kind} style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--muted)' }}>
                      <span>{WF_NODE_TYPE_META[kind].label}</span>
                      <span>{count}</span>
                    </div>
                  );
                })}
              </div>
              <div style={{ marginTop: '0.9rem', paddingTop: '0.75rem', borderTop: '1px solid var(--border)' }}>
                {issues.length === 0 ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: 'var(--success, #059669)', fontSize: '0.82rem' }}>
                    <CheckCircle2 size={14} /> No issues
                  </div>
                ) : (
                  <ul style={{ margin: 0, paddingLeft: '1.1rem', display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                    {issues.map((issue) => (
                      <li key={issue} style={{ fontSize: '0.78rem', color: 'var(--error)' }}>{issue}</li>
                    ))}
                  </ul>
                )}
              </div>
            </>
          ) : (
            <>
              <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', marginBottom: '0.6rem' }}>
                Node inspector
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                <label>
                  Type
                  <select
                    value={selectedNode.data.kind}
                    onChange={(e) => {
                      const kind = e.target.value as WorkflowNodeKind;
                      onChange({ ...wf, nodes: wf.nodes.map((n) => (n.id === selectedNode.id ? { ...n, data: { ...DEFAULT_DATA[kind] } } : n)) });
                    }}
                  >
                    {(Object.keys(WF_NODE_TYPE_META) as WorkflowNodeKind[]).map((kind) => (
                      <option key={kind} value={kind} disabled={kind === 'start' && wf.nodes.some((n) => n.data.kind === 'start' && n.id !== selectedNode.id)}>
                        {WF_NODE_TYPE_META[kind].label}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  Label (optional, shown on the card)
                  <input
                    value={selectedNode.data.label || ''}
                    placeholder={WF_NODE_TYPE_META[selectedNode.data.kind].label}
                    onChange={(e) => updateSelectedNodeData({ label: e.target.value })}
                  />
                </label>

                {selectedNode.data.kind === 'start' && (
                  <label>
                    Opening greeting (first_message)
                    <textarea
                      rows={3}
                      value={selectedNode.data.first_message || ''}
                      onChange={(e) => updateSelectedNodeData({ first_message: e.target.value })}
                    />
                  </label>
                )}

                {selectedNode.data.kind === 'conversation' && (
                  <>
                    <label>
                      Prompt (this node's own narrow job)
                      <textarea
                        rows={4}
                        value={selectedNode.data.prompt || ''}
                        onChange={(e) => updateSelectedNodeData({ prompt: e.target.value })}
                      />
                    </label>
                    <div>
                      <div style={{ fontSize: '0.78rem', fontWeight: 600, marginBottom: '0.35rem' }}>Transitions</div>
                      {(selectedNode.data.transitions || []).map((t) => (
                        <div key={t.id} style={{ display: 'flex', gap: '0.35rem', marginBottom: '0.35rem' }}>
                          <input
                            style={{ flex: 1 }}
                            value={t.label || ''}
                            placeholder="e.g. caller wants to book an appointment"
                            onChange={(e) => {
                              const transitions = (selectedNode.data.transitions || []).map((x) => (x.id === t.id ? { ...x, label: e.target.value } : x));
                              updateSelectedNodeData({ transitions });
                            }}
                          />
                          <button className="danger-button" onClick={() => removeOutcome('transitions', t.id)}>×</button>
                        </div>
                      ))}
                      <button
                        style={{ width: '100%' }}
                        onClick={() => {
                          const transitions = [...(selectedNode.data.transitions || []), { id: nextId('t'), label: '' }];
                          updateSelectedNodeData({ transitions });
                        }}
                      >
                        <Plus size={12} /> Add transition
                      </button>
                    </div>
                  </>
                )}

                {selectedNode.data.kind === 'condition' && (
                  <div>
                    <div style={{ fontSize: '0.78rem', fontWeight: 600, marginBottom: '0.35rem' }}>Conditions</div>
                    {(selectedNode.data.conditions || []).map((c) => (
                      <div key={c.id} style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', marginBottom: '0.5rem', padding: '0.4rem', border: '1px solid var(--border)', borderRadius: 6 }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexDirection: 'row' }}>
                          <input
                            type="checkbox"
                            checked={Boolean(c.is_fallback)}
                            onChange={(e) => {
                              const conditions = (selectedNode.data.conditions || []).map((x) => (x.id === c.id ? { ...x, is_fallback: e.target.checked } : x));
                              updateSelectedNodeData({ conditions });
                            }}
                          />
                          Fallback (matches if nothing else does)
                        </label>
                        {!c.is_fallback && (
                          <>
                            <input
                              placeholder="path (e.g. lead.city)"
                              value={c.path || ''}
                              onChange={(e) => {
                                const conditions = (selectedNode.data.conditions || []).map((x) => (x.id === c.id ? { ...x, path: e.target.value } : x));
                                updateSelectedNodeData({ conditions });
                              }}
                            />
                            <select
                              value={c.op || 'eq'}
                              onChange={(e) => {
                                const conditions = (selectedNode.data.conditions || []).map((x) => (x.id === c.id ? { ...x, op: e.target.value as typeof c.op } : x));
                                updateSelectedNodeData({ conditions });
                              }}
                            >
                              <option value="eq">equals</option>
                              <option value="ne">not equals</option>
                              <option value="gt">greater than</option>
                              <option value="lt">less than</option>
                              <option value="contains">contains</option>
                              <option value="exists">exists</option>
                            </select>
                            {c.op !== 'exists' && (
                              <input
                                placeholder="value"
                                value={c.value == null ? '' : String(c.value)}
                                onChange={(e) => {
                                  const conditions = (selectedNode.data.conditions || []).map((x) => (x.id === c.id ? { ...x, value: e.target.value } : x));
                                  updateSelectedNodeData({ conditions });
                                }}
                              />
                            )}
                          </>
                        )}
                        <button className="danger-button" onClick={() => removeOutcome('conditions', c.id)}>Remove condition</button>
                      </div>
                    ))}
                    <button
                      style={{ width: '100%' }}
                      onClick={() => {
                        const conditions = [...(selectedNode.data.conditions || []), { id: nextId('c'), path: '', op: 'eq' as const, value: '', is_fallback: false }];
                        updateSelectedNodeData({ conditions });
                      }}
                    >
                      <Plus size={12} /> Add condition
                    </button>
                    <p style={{ fontSize: '0.72rem', color: 'var(--muted)', marginTop: '0.4rem' }}>
                      Add one condition checked <strong>Fallback</strong> to catch anything the other rules don't.
                    </p>
                  </div>
                )}

                {selectedNode.data.kind === 'function' && (
                  <>
                    <label>
                      Endpoint URL
                      <input
                        value={selectedNode.data.function?.url || ''}
                        placeholder="https://..."
                        onChange={(e) => updateSelectedNodeData({ function: { ...selectedNode.data.function, url: e.target.value } })}
                      />
                    </label>
                    <label>
                      Method
                      <select
                        value={selectedNode.data.function?.method || 'GET'}
                        onChange={(e) => updateSelectedNodeData({ function: { ...selectedNode.data.function, method: e.target.value as 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' } })}
                      >
                        {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Store response as (output_key)
                      <input
                        value={selectedNode.data.output_key || ''}
                        placeholder="e.g. lead_lookup"
                        onChange={(e) => updateSelectedNodeData({ output_key: e.target.value })}
                      />
                    </label>
                  </>
                )}

                {selectedNode.data.kind === 'global' && (
                  <>
                    <label>
                      Trigger description
                      <textarea
                        rows={3}
                        value={selectedNode.data.trigger_description || ''}
                        placeholder="e.g. caller asks what our business hours are"
                        onChange={(e) => updateSelectedNodeData({ trigger_description: e.target.value })}
                      />
                    </label>
                    <label>
                      Action
                      <select
                        value={selectedNode.data.action || 'end_call'}
                        onChange={(e) => updateSelectedNodeData({ action: e.target.value as 'end_call' | 'continue' | 'transfer' })}
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
                          value={selectedNode.data.transfer_number || ''}
                          onChange={(e) => updateSelectedNodeData({ transfer_number: e.target.value })}
                        />
                      </label>
                    )}
                  </>
                )}

                {selectedNode.data.kind === 'end_call' && (
                  <label>
                    Closing message
                    <textarea
                      rows={3}
                      value={selectedNode.data.closing_message || ''}
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
          description="This removes the node and any edges connected to it from the graph."
          confirmLabel="Delete"
          tone="danger"
          onCancel={cancelRemoveNodes}
          onConfirm={confirmRemoveNodes}
        />
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
              ['start', 'The entry point — every flow needs exactly one. The opening greeting is spoken the instant the call connects.'],
              ['conversation', 'A real LLM turn with its own narrow job. Add Transitions to hand off to whatever should happen next — each becomes its own connector dot.'],
              ['condition', 'A rule, not an LLM guess. Compares a stored value at a path against a target. Add one condition marked Fallback to catch anything else.'],
              ['function', 'Calls an external endpoint mid-call and stores the response under output_key, for a later Condition or Conversation node to reference.'],
              ['global', 'Always available — fires from any node whenever its trigger description matches what the caller says, then ends the call, transfers, or continues.'],
              ['end_call', 'A terminal node — says its closing message, then hangs up. A flow can have as many as it needs.'],
            ].map(([kind, description]) => {
              const meta = WF_NODE_TYPE_META[kind as WorkflowNodeKind];
              return (
                <div key={kind} style={{ display: 'flex', gap: '0.6rem', border: '1px solid var(--border)', borderRadius: 8, padding: '0.6rem' }}>
                  <span style={{ flexShrink: 0, width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 6, background: meta.bg, color: meta.color, border: `1px solid ${meta.color}33` }}>
                    {meta.icon}
                  </span>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: '0.85rem' }}>{meta.label}</div>
                    <div style={{ fontSize: '0.78rem', color: 'var(--muted)' }}>{description}</div>
                  </div>
                </div>
              );
            })}
            <p style={{ fontSize: '0.76rem', color: 'var(--muted)', marginTop: '0.2rem' }}>
              <strong>Connecting nodes:</strong> drag from a node's colored dot to the next node.
            </p>
          </div>
        </Dialog>
      )}
    </section>
  );
}
