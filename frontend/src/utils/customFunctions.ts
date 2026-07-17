import type { CustomFunction, FunctionParam, StoreVariable } from '../types';

/** Create a blank custom function with sane defaults matching backend/models.py. */
export function newCustomFunction(): CustomFunction {
  return {
    id: `fn_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    name: '',
    description: '',
    url: '',
    method: 'POST',
    timeout_ms: 120000,
    headers: {},
    query_params: {},
    body_mode: 'form',
    parameters: [],
    raw_body_schema: {},
    store_variables: [],
    trigger: 'during_call',
    enabled: true,
  };
}

export function newParam(): FunctionParam {
  return { name: '', description: '', type: 'string', required: false };
}

export function newStoreVariable(): StoreVariable {
  return { variable: '', json_path: '' };
}

/** Names reserved by the built-in tools — a during_call custom function may not reuse them. */
export const RESERVED_TOOL_NAMES = ['FetchLead', 'FetchCategorySchema'];

/** Validate a function for save. Returns a list of human-readable problems (empty = valid). */
export function validateFunction(fn: CustomFunction, existingNames: string[] = []): string[] {
  const errors: string[] = [];
  const name = (fn.name || '').trim();
  if (!name) {
    errors.push('Name is required.');
  } else if (fn.trigger === 'during_call' && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
    errors.push('During-call function names must be a valid identifier (letters, digits, underscore; not starting with a digit).');
  }
  if (fn.trigger === 'during_call' && RESERVED_TOOL_NAMES.includes(name)) {
    errors.push(`"${name}" is a built-in tool name — choose another.`);
  }
  if (existingNames.filter((n) => n === name).length > 0) {
    errors.push(`Another function is already named "${name}".`);
  }
  if (!(fn.url || '').trim()) {
    errors.push('API endpoint URL is required.');
  }
  if (!Number.isFinite(fn.timeout_ms) || fn.timeout_ms <= 0) {
    errors.push('Timeout must be a positive number of milliseconds.');
  }
  for (const p of fn.parameters) {
    if (!(p.name || '').trim()) {
      errors.push('Every parameter needs a name.');
      break;
    }
  }
  for (const sv of fn.store_variables) {
    if ((sv.variable || '').trim() && !(sv.json_path || '').trim()) {
      errors.push(`Store-variable "${sv.variable}" needs a response path.`);
    }
  }
  return errors;
}

/** Build the sample args object a Test run should send, from the declared parameters. */
export function sampleArgsFromParams(params: FunctionParam[]): Record<string, unknown> {
  const args: Record<string, unknown> = {};
  for (const p of params) {
    const name = (p.name || '').trim();
    if (!name) continue;
    args[name] =
      p.type === 'number' ? 0 : p.type === 'boolean' ? false : p.type === 'object' ? {} : p.type === 'array' ? [] : '';
  }
  return args;
}
