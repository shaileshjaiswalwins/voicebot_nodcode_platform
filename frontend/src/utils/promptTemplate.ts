const VAR_TOKEN_RE = /\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g;

/** Extract every {{variable_name}} token used in a prompt template, deduped, in first-seen order. */
export function extractTemplateVars(template: string): string[] {
  const seen = new Set<string>();
  for (const match of template.matchAll(VAR_TOKEN_RE)) {
    seen.add(match[1]);
  }
  return [...seen];
}

/** Render a prompt template against a row's vars (plus name/phone_number). Unknown tokens are
 * left as-is (e.g. "{{unknown_col}}") rather than silently blanked, so a bad reference is
 * obvious in the live preview instead of disappearing. */
export function interpolateTemplate(
  template: string,
  row: { name?: string; phone_number?: string; vars?: Record<string, string> },
): string {
  const values: Record<string, string> = {
    name: row.name || 'Customer',
    phone_number: row.phone_number || '',
    ...(row.vars || {}),
  };
  return template.replace(VAR_TOKEN_RE, (full, key) => (key in values ? values[key] : full));
}
