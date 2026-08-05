import type { CustomFunction, FunctionParam, HttpMethod, StoreVariable } from '../types';

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

function newFunctionId(): string {
  return `fn_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/** Guarantee every function has a unique, non-empty id, and that no two functions share
 * object references for their nested collections. Legacy configs (and the backend model's
 * `id: str = ""` default) can produce functions with a blank or duplicate id — since the
 * editor matches functions by id, that silently merges edits across unrelated functions. */
export function ensureFunctionIds(functions: CustomFunction[]): CustomFunction[] {
  const seen = new Set<string>();
  return functions.map((fn) => {
    const needsId = !fn.id || seen.has(fn.id);
    const id = needsId ? newFunctionId() : fn.id;
    seen.add(id);
    return {
      ...fn,
      id,
      headers: { ...fn.headers },
      query_params: { ...fn.query_params },
      parameters: fn.parameters.map((p) => ({ ...p })),
      store_variables: fn.store_variables.map((sv) => ({ ...sv })),
      raw_body_schema: { ...fn.raw_body_schema },
    };
  });
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
  const url = (fn.url || '').trim();
  if (!url) {
    errors.push('API endpoint URL is required.');
  } else if (!/^https?:\/\/.+/i.test(url)) {
    errors.push('API endpoint URL must be an absolute http(s):// URL.');
  }
  if (!Number.isFinite(fn.timeout_ms) || fn.timeout_ms <= 0) {
    errors.push('Timeout must be a positive number of milliseconds.');
  } else if (fn.timeout_ms > 600000) {
    errors.push('Timeout is unusually large (> 600000 ms / 10 min) — most calls should finish far sooner.');
  }
  const seenParams = new Set<string>();
  for (const p of fn.parameters) {
    const pname = (p.name || '').trim();
    if (!pname) {
      errors.push('Every parameter needs a name.');
      continue;
    }
    if (seenParams.has(pname)) {
      errors.push(`Duplicate parameter name "${pname}" — parameter names must be unique.`);
    }
    seenParams.add(pname);
  }
  const seenVars = new Set<string>();
  for (const sv of fn.store_variables) {
    const vname = (sv.variable || '').trim();
    if (vname && !(sv.json_path || '').trim()) {
      errors.push(`Store-variable "${vname}" needs a response path.`);
    }
    if (vname) {
      if (seenVars.has(vname)) {
        errors.push(`Duplicate store-variable name "${vname}" — variable names must be unique.`);
      }
      seenVars.add(vname);
    }
  }
  // De-duplicate: multiple blank params/vars can push identical messages, and the UI keys
  // its error list by the message string.
  return Array.from(new Set(errors));
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

/** Split a shell command line into tokens, respecting single/double quotes (no escape-sequence
 * handling beyond that — good enough for curl commands copy-pasted from docs/DevTools). */
function tokenizeShellCommand(input: string): string[] {
  const tokens: string[] = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(input))) {
    tokens.push(m[1] ?? m[2] ?? m[3]);
  }
  return tokens;
}

function inferParamType(value: unknown): FunctionParam['type'] {
  if (typeof value === 'number') return 'number';
  if (typeof value === 'boolean') return 'boolean';
  if (Array.isArray(value)) return 'array';
  if (value !== null && typeof value === 'object') return 'object';
  return 'string';
}

/** Deterministic curl-command parser — no LLM round-trip, so it's instant and offline. Handles
 * the common flags a PM would copy from API docs or DevTools "Copy as cURL": -X/--request,
 * -H/--header, -d/--data(-raw|-binary) (parsed as JSON into Request Body parameters), and the
 * bare URL argument. Anything it can't confidently parse (auth flags, multipart -F, -G query
 * mode, etc.) is left alone rather than guessed — same "safe default over guess" rule as the
 * AI-assist path (generate_function_from_description). Throws with a plain-English message on
 * unparseable input so the caller can show it inline instead of silently returning junk. */
export function parseCurlCommand(raw: string): Partial<CustomFunction> {
  const text = raw.trim().replace(/\\\n/g, ' ');
  if (!text) throw new Error('Paste a curl command first.');
  const tokens = tokenizeShellCommand(text);
  const start = tokens[0]?.toLowerCase() === 'curl' ? 1 : 0;

  let url = '';
  let method: HttpMethod | undefined;
  const headers: Record<string, string> = {};
  let bodyRaw: string | undefined;

  for (let i = start; i < tokens.length; i++) {
    const tok = tokens[i];
    if (tok === '-X' || tok === '--request') {
      method = (tokens[++i] || '').toUpperCase() as HttpMethod;
    } else if (tok === '-H' || tok === '--header') {
      const header = tokens[++i] || '';
      const idx = header.indexOf(':');
      if (idx > 0) headers[header.slice(0, idx).trim()] = header.slice(idx + 1).trim();
    } else if (tok === '-d' || tok === '--data' || tok === '--data-raw' || tok === '--data-binary' || tok === '--data-ascii') {
      bodyRaw = tokens[++i];
    } else if (tok === '-u' || tok === '--user' || tok === '-b' || tok === '--cookie' || tok === '-A' || tok === '--user-agent') {
      i++; // skip value — not modeled by CustomFunction, don't guess
    } else if (tok.startsWith('-')) {
      // unrecognized flag (e.g. -k, -s, -L, -F) — skip, no associated value assumed
    } else if (!url) {
      url = tok;
    }
  }

  if (!url) throw new Error("Couldn't find a URL in that curl command.");

  const parameters: FunctionParam[] = [];
  if (bodyRaw !== undefined) {
    try {
      const parsed = JSON.parse(bodyRaw);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        for (const [key, value] of Object.entries(parsed)) {
          parameters.push({ name: key, description: '', type: inferParamType(value), required: true });
        }
      }
    } catch {
      // Not JSON (e.g. form-encoded "a=1&b=2") — leave parameters empty rather than guess.
    }
  }

  return {
    url,
    method: method || (bodyRaw !== undefined ? 'POST' : 'GET'),
    headers,
    body_mode: 'json',
    parameters,
  };
}
