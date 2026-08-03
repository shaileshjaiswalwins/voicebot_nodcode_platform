import React from 'react';
import { ChipListEditor } from './ChipListEditor';

/** Chip-style editor for the call-end close phrases. Extracted from BuilderView so it can
 * be shared by the tabbed settings (BotConfigTabs) without a circular import. Now a thin
 * wrapper over the generic ChipListEditor (see that file for the free-typing-input
 * rationale) so this call site's copy stays unchanged for existing callers. */
export function CloseMarkersEditor({ markers, onChange }: { markers: string[]; onChange: (value: string[]) => void }) {
  return (
    <ChipListEditor
      items={markers}
      onChange={onChange}
      label="Call-end close phrases"
      helpText="Bot ends the call when it detects any of these phrases. Override the hardcoded Hindi defaults for non-Hindi bots."
      placeholder="e.g. thank you, goodbye, dhanyavaad"
      emptyText="Using hardcoded defaults (Hindi)"
    />
  );
}
