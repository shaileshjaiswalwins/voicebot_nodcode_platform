import { describe, it, expect } from 'vitest';
import {
  newCustomFunction,
  newParam,
  newStoreVariable,
  validateFunction,
  sampleArgsFromParams,
  ensureFunctionIds,
  RESERVED_TOOL_NAMES,
} from './customFunctions';

describe('newCustomFunction', () => {
  it('creates a function with backend-matching defaults and a unique id', () => {
    const a = newCustomFunction();
    const b = newCustomFunction();
    expect(a.id).not.toBe(b.id);
    expect(a.method).toBe('POST');
    expect(a.timeout_ms).toBe(120000);
    expect(a.trigger).toBe('during_call');
    expect(a.enabled).toBe(true);
    expect(a.body_mode).toBe('form');
  });
});

describe('validateFunction', () => {
  const base = () => ({ ...newCustomFunction(), name: 'get_lead', url: 'http://x' });

  it('passes a well-formed during_call function', () => {
    expect(validateFunction(base())).toEqual([]);
  });

  it('requires a name and a url', () => {
    const errs = validateFunction({ ...newCustomFunction() });
    expect(errs.some((e) => /Name is required/.test(e))).toBe(true);
    expect(errs.some((e) => /URL is required/.test(e))).toBe(true);
  });

  it('rejects invalid identifier names for during_call functions', () => {
    const errs = validateFunction({ ...base(), name: '2 bad name' });
    expect(errs.some((e) => /valid identifier/.test(e))).toBe(true);
  });

  it('allows non-identifier names for pre/post_call functions', () => {
    const errs = validateFunction({ ...base(), name: 'fetch lead details', trigger: 'pre_call' });
    expect(errs).toEqual([]);
  });

  it('rejects reserved built-in names for during_call', () => {
    for (const name of RESERVED_TOOL_NAMES) {
      const errs = validateFunction({ ...base(), name });
      expect(errs.some((e) => /built-in tool name/.test(e))).toBe(true);
    }
  });

  it('rejects a name that collides with another function', () => {
    const errs = validateFunction(base(), ['get_lead']);
    expect(errs.some((e) => /already named/.test(e))).toBe(true);
  });

  it('rejects a non-positive timeout', () => {
    const errs = validateFunction({ ...base(), timeout_ms: 0 });
    expect(errs.some((e) => /Timeout/.test(e))).toBe(true);
  });

  it('requires each parameter to have a name', () => {
    const errs = validateFunction({ ...base(), parameters: [{ ...newParam(), name: '' }] });
    expect(errs.some((e) => /parameter needs a name/.test(e))).toBe(true);
  });

  it('requires a json_path when a store variable is named', () => {
    const errs = validateFunction({ ...base(), store_variables: [{ ...newStoreVariable(), variable: 'v', json_path: '' }] });
    expect(errs.some((e) => /needs a response path/.test(e))).toBe(true);
  });

  it('rejects a URL that is not an absolute http(s) URL', () => {
    const errs = validateFunction({ ...base(), url: 'api.example.com/x' });
    expect(errs.some((e) => /absolute http\(s\):\/\/ URL/.test(e))).toBe(true);
  });

  it('accepts https URLs', () => {
    expect(validateFunction({ ...base(), url: 'https://api.example.com/lead' })).toEqual([]);
  });

  it('flags an unusually large timeout', () => {
    const errs = validateFunction({ ...base(), timeout_ms: 700000 });
    expect(errs.some((e) => /unusually large/.test(e))).toBe(true);
  });

  it('rejects duplicate parameter names', () => {
    const errs = validateFunction({
      ...base(),
      parameters: [{ ...newParam(), name: 'mobile' }, { ...newParam(), name: 'mobile' }],
    });
    expect(errs.some((e) => /Duplicate parameter name "mobile"/.test(e))).toBe(true);
  });

  it('rejects duplicate store-variable names', () => {
    const errs = validateFunction({
      ...base(),
      store_variables: [
        { variable: 'name', json_path: 'a' },
        { variable: 'name', json_path: 'b' },
      ],
    });
    expect(errs.some((e) => /Duplicate store-variable name "name"/.test(e))).toBe(true);
  });

  it('de-duplicates identical messages (multiple blank params)', () => {
    const errs = validateFunction({
      ...base(),
      parameters: [{ ...newParam(), name: '' }, { ...newParam(), name: '' }],
    });
    const blanks = errs.filter((e) => /parameter needs a name/.test(e));
    expect(blanks).toHaveLength(1);
  });
});

describe('ensureFunctionIds', () => {
  it('assigns unique ids to functions with a blank id (legacy configs)', () => {
    const fns = [
      { ...newCustomFunction(), id: '' },
      { ...newCustomFunction(), id: '' },
    ];
    const [a, b] = ensureFunctionIds(fns);
    expect(a.id).toBeTruthy();
    expect(b.id).toBeTruthy();
    expect(a.id).not.toBe(b.id);
  });

  it('assigns unique ids to functions with a duplicate id', () => {
    const fns = [
      { ...newCustomFunction(), id: 'dup' },
      { ...newCustomFunction(), id: 'dup' },
    ];
    const [a, b] = ensureFunctionIds(fns);
    expect(a.id).toBe('dup');
    expect(b.id).not.toBe('dup');
  });

  it('leaves already-unique, non-blank ids untouched', () => {
    const fns = [
      { ...newCustomFunction(), id: 'fn-a' },
      { ...newCustomFunction(), id: 'fn-b' },
    ];
    const [a, b] = ensureFunctionIds(fns);
    expect(a.id).toBe('fn-a');
    expect(b.id).toBe('fn-b');
  });

  it('deep-clones nested collections so functions never share object references', () => {
    const shared = { store_id: '1' };
    const fns = [
      { ...newCustomFunction(), id: 'a', query_params: shared },
      { ...newCustomFunction(), id: 'b', query_params: shared },
    ];
    const [a, b] = ensureFunctionIds(fns);
    expect(a.query_params).not.toBe(b.query_params);
    a.query_params.store_id = 'changed';
    expect(b.query_params.store_id).toBe('1');
  });
});

describe('sampleArgsFromParams', () => {
  it('builds a typed sample object and skips unnamed params', () => {
    const args = sampleArgsFromParams([
      { name: 'mobile', type: 'string', required: true },
      { name: 'count', type: 'number', required: false },
      { name: 'flag', type: 'boolean', required: false },
      { name: '', type: 'string', required: false },
    ]);
    expect(args).toEqual({ mobile: '', count: 0, flag: false });
  });
});
