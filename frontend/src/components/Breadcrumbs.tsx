import React from 'react';
import { ChevronRight } from 'lucide-react';

export type Crumb = { label: string; onClick?: () => void };

/** Replaces the old static page title/subtitle header — shows the actual navigation depth
 * (e.g. Agents > sunny_demo) so it stays meaningful once you're inside a bot's workspace,
 * not just a fixed label per view. */
export function Breadcrumbs({ crumbs }: { crumbs: Crumb[] }) {
  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      {crumbs.map((crumb, idx) => {
        const isLast = idx === crumbs.length - 1;
        return (
          <React.Fragment key={idx}>
            {idx > 0 && <ChevronRight size={13} className="crumb-sep" />}
            {crumb.onClick && !isLast ? (
              <button className="crumb" onClick={crumb.onClick}>{crumb.label}</button>
            ) : (
              <span className="crumb current">{crumb.label}</span>
            )}
          </React.Fragment>
        );
      })}
    </nav>
  );
}
