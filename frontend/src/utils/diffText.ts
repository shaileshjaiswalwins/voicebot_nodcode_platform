export type DiffOp = { type: 'equal' | 'del' | 'ins'; text: string };

function tokenize(text: string): string[] {
  // Words and whitespace runs as separate tokens (not just splitting on \s+) so the diff can
  // line up unchanged whitespace/newlines too — otherwise every paragraph break would show as
  // a spurious delete+insert pair around it.
  return text.match(/\s+|\S+/g) || [];
}

/** Word-level diff between two prompt texts (before/after a "Refine with AI" edit), so the
 * user can see exactly what changed instead of just the final merged text — deletions and
 * insertions rendered separately by the caller (DiffView) rather than a single opaque replace.
 * Classic LCS dynamic program over word tokens; flattened into one Int32Array rather than an
 * array of arrays since a full system prompt can be a few hundred to low thousands of tokens
 * and this avoids the extra per-row object/GC overhead at that size. */
export function diffWords(oldText: string, newText: string): DiffOp[] {
  const a = tokenize(oldText);
  const b = tokenize(newText);
  const n = a.length;
  const m = b.length;

  // Guard against the O(n*m) table blowing up on a pathologically long prompt — fall back to
  // marking the whole thing as one replaced block rather than freezing the tab computing a
  // token-level LCS that's bigger than is reasonable to hold in memory.
  if (n * m > 4_000_000) {
    const ops: DiffOp[] = [];
    if (oldText) ops.push({ type: 'del', text: oldText });
    if (newText) ops.push({ type: 'ins', text: newText });
    return ops;
  }

  // dp[i*(m+1)+j] = length of the LCS of a[i:] and b[j:] (suffix LCS), computed backwards so
  // the forward walk below can greedily pick equal/delete/insert at each step.
  const width = m + 1;
  const dp = new Int32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * width + j] =
        a[i] === b[j] ? dp[(i + 1) * width + (j + 1)] + 1 : Math.max(dp[(i + 1) * width + j], dp[i * width + (j + 1)]);
    }
  }

  const ops: DiffOp[] = [];
  const push = (type: DiffOp['type'], text: string) => {
    const last = ops[ops.length - 1];
    if (last && last.type === type) last.text += text;
    else ops.push({ type, text });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push('equal', a[i]);
      i++;
      j++;
    } else if (dp[(i + 1) * width + j] >= dp[i * width + (j + 1)]) {
      push('del', a[i]);
      i++;
    } else {
      push('ins', b[j]);
      j++;
    }
  }
  while (i < n) {
    push('del', a[i]);
    i++;
  }
  while (j < m) {
    push('ins', b[j]);
    j++;
  }
  return ops;
}
