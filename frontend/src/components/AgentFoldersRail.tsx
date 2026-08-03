import React from 'react';
import { Folder, FolderOpen, Layers } from 'lucide-react';

/** Folders are a view over tags, not a separate entity — a bot with tag "Sales" shows up
 * under the "Sales" folder. There's no standalone "create folder" action: you make one by
 * tagging an agent (Basic Info tab), and it appears/disappears here automatically as tags
 * get added/removed. Keeps this purely additive to the Tags system already in place instead
 * of introducing a second, parallel grouping mechanism to keep in sync. */
export function AgentFoldersRail({
  tags,
  counts,
  selected,
  onSelect,
  totalCount,
}: {
  tags: string[];
  counts: Record<string, number>;
  selected: string | null;
  onSelect: (tag: string | null) => void;
  totalCount: number;
}) {
  return (
    <nav className="agent-folders-rail" aria-label="Agent folders">
      <div className="agent-folders-title">Folders</div>
      <button
        className={selected === null ? 'agent-folder-item active' : 'agent-folder-item'}
        onClick={() => onSelect(null)}
      >
        <Layers size={14} />
        <span>All Agents</span>
        <span className="count-badge">{totalCount}</span>
      </button>
      {tags.map((tag) => (
        <button
          key={tag}
          className={selected === tag ? 'agent-folder-item active' : 'agent-folder-item'}
          onClick={() => onSelect(tag)}
        >
          {selected === tag ? <FolderOpen size={14} /> : <Folder size={14} />}
          <span>{tag}</span>
          <span className="count-badge">{counts[tag] || 0}</span>
        </button>
      ))}
      {tags.length === 0 && (
        <p className="muted" style={{ fontSize: '0.78rem', padding: '0.4rem 0.5rem' }}>
          No folders yet — add a tag to an agent (Basic Info tab) to create one.
        </p>
      )}
    </nav>
  );
}
