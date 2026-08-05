import type { CustomFunction } from '../types';
import type { WorkflowNode } from '../api';

// Must match backend/prompt_assist.py's PLACEHOLDER_URL exactly — the sentinel the AI workflow
// generator writes into any URL it can't know (it has no visibility into the user's real APIs)
// so the builder can flag it instead of silently shipping a dead/fake endpoint.
export const WORKFLOW_URL_PLACEHOLDER = 'TODO_SET_URL';

export function nodeNeedsAttention(node: WorkflowNode): boolean {
  return node.data.kind === 'function' && node.data.function?.url === WORKFLOW_URL_PLACEHOLDER;
}

export function functionNeedsAttention(fn: CustomFunction): boolean {
  return fn.url === WORKFLOW_URL_PLACEHOLDER;
}
