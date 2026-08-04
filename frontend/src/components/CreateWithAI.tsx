import React, { useState } from 'react';
import { Sparkles } from 'lucide-react';
import { Dialog } from './Dialog';
import { Spinner } from './Spinner';

/** Create Agent > Create with AI — a single free-text description in, a fully-formed agent
 * out (agent_name/initial_message/system_prompt via backend/prompt_assist.py's
 * generate_agent_from_description). Everything else stays on platform defaults; the goal is
 * the fewest possible steps from "I need a bot" to "I have a bot to test."
 *
 * Also reused for "Create a workflow with AI" (backend's generate_workflow_from_description) —
 * same one-box-in shape, different copy/label/generated result, hence the optional props. */
export function CreateWithAI({
  onContinue,
  onBack,
  busy,
  error,
  title = 'Create with AI',
  label = 'Describe the agent you want to build',
  placeholder = 'e.g. "A friendly agent that calls HR candidates to schedule their first interview and confirm their availability."',
  hint = "We'll generate the persona name, opening greeting, and full system prompt from this. Everything else uses sensible defaults — edit anything after.",
  continueLabel = 'Continue',
  generatingLabel = 'Generating…',
}: {
  onContinue: (description: string) => void;
  onBack: () => void;
  busy: boolean;
  error?: string;
  title?: string;
  label?: string;
  placeholder?: string;
  hint?: string;
  continueLabel?: string;
  generatingLabel?: string;
}) {
  const [description, setDescription] = useState('');

  return (
    <Dialog
      title={title}
      icon={<Sparkles size={17} />}
      maxWidth={520}
      onClose={onBack}
      closeOnBackdrop={!busy}
      closeOnEscape={!busy}
      footer={
        <>
          <button onClick={onBack} disabled={busy}>Back</button>
          <button className="primary" onClick={() => onContinue(description)} disabled={!description.trim() || busy}>
            {busy ? <Spinner label="Generating" size={13} /> : <Sparkles size={15} />} {busy ? generatingLabel : continueLabel}
          </button>
        </>
      }
    >
      <label className="full">
        {label}
        <textarea
          rows={5}
          autoFocus
          placeholder={placeholder}
          value={description}
          disabled={busy}
          onChange={(e) => setDescription(e.target.value)}
        />
        <small>{hint}</small>
      </label>
      {error && <div className="notice error" role="alert">{error}</div>}
    </Dialog>
  );
}
