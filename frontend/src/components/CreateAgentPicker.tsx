import React from 'react';
import { ArrowRight, Clock, LayoutTemplate, Sparkles, Wrench } from 'lucide-react';
import { Dialog } from './Dialog';

type Speed = 'fastest' | 'medium' | 'longest';

const SPEED_LABEL: Record<Speed, string> = {
  fastest: 'Fastest',
  medium: 'Medium',
  longest: 'Most control',
};

/** First screen of the Create Agent flow — replaces the old behavior of jumping straight
 * into a blank form modal, which gave a non-technical user nothing to work from. Three
 * paths, cheapest-first: describe it in English, pick a ready-made template, or build by
 * hand — all three land in the same builder once done. Each option carries a rough time
 * estimate so a user can pick a path that matches how much time they actually have. */
export function CreateAgentPicker({
  onSelectAI,
  onSelectTemplate,
  onSelectScratch,
  onClose,
}: {
  onSelectAI: () => void;
  onSelectTemplate: () => void;
  onSelectScratch: () => void;
  onClose: () => void;
}) {
  const options: Array<{
    key: string;
    icon: React.ReactNode;
    title: string;
    description: string;
    time: string;
    speed: Speed;
    onClick: () => void;
  }> = [
    {
      key: 'ai',
      icon: <Sparkles size={20} />,
      title: 'Create with AI',
      description: 'Describe the agent you want in a sentence or two — we\'ll write the greeting and prompt for you.',
      time: '~1 minute',
      speed: 'fastest',
      onClick: onSelectAI,
    },
    {
      key: 'template',
      icon: <LayoutTemplate size={20} />,
      title: 'Create from template',
      description: 'Pick a ready-made agent for HR, Support, Sales, Marketing, or Scheduling and tweak it if needed.',
      time: '~3–5 minutes',
      speed: 'medium',
      onClick: onSelectTemplate,
    },
    {
      key: 'scratch',
      icon: <Wrench size={20} />,
      title: 'Create from scratch',
      description: 'Fill in every field yourself — for when you already know exactly what you want.',
      time: '15+ minutes',
      speed: 'longest',
      onClick: onSelectScratch,
    },
  ];

  return (
    // closeOnBackdrop intentionally false: an accidental click just outside the card grid
    // used to silently discard this step (no unsaved data, but still a jarring dead-end for
    // a new user) — dismiss only via the explicit close control or Escape now.
    <Dialog title="Create a new agent" maxWidth={760} onClose={onClose} closeOnBackdrop={false}>
      <p className="create-agent-subtitle">Pick a starting point — all three land you in the same builder.</p>
      <div className="create-agent-options">
        {options.map((opt) => (
          <button key={opt.key} className="create-agent-option" onClick={opt.onClick}>
            <div className={`create-agent-option-badge create-agent-option-badge-${opt.speed}`}>
              <Clock size={11} />
              {SPEED_LABEL[opt.speed]}
            </div>
            <div className="create-agent-option-icon"><span>{opt.icon}</span></div>
            <strong>{opt.title}</strong>
            <p>{opt.description}</p>
            <div className="create-agent-option-footer">
              <span className="create-agent-option-time">{opt.time}</span>
              <ArrowRight size={15} className="create-agent-option-arrow" />
            </div>
          </button>
        ))}
      </div>
    </Dialog>
  );
}
