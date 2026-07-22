import { describe, it, expect } from 'vitest';
import { extractTemplateVars, interpolateTemplate } from './promptTemplate';

describe('extractTemplateVars', () => {
  it('extracts unique variable names in first-seen order', () => {
    const vars = extractTemplateVars('Hi {{name}}, {{reason}} again {{name}}, also {{ reason }}');
    expect(vars).toEqual(['name', 'reason']);
  });

  it('returns an empty list when there are no tokens', () => {
    expect(extractTemplateVars('Hello there')).toEqual([]);
  });
});

describe('interpolateTemplate', () => {
  it('substitutes name, phone_number, and arbitrary vars', () => {
    const out = interpolateTemplate('Hi {{name}} ({{phone_number}}), re: {{reason_of_calling}}', {
      name: 'Asha',
      phone_number: '+919876543210',
      vars: { reason_of_calling: 'payment reminder' },
    });
    expect(out).toBe('Hi Asha (+919876543210), re: payment reminder');
  });

  it('falls back name to "Customer" when blank, matching backend upload behavior', () => {
    const out = interpolateTemplate('Hello {{name}}', { name: '', vars: {} });
    expect(out).toBe('Hello Customer');
  });

  it('leaves unknown tokens untouched instead of blanking them', () => {
    const out = interpolateTemplate('Hi {{name}}, {{unknown_col}}', { name: 'Asha', vars: {} });
    expect(out).toBe('Hi Asha, {{unknown_col}}');
  });
});
