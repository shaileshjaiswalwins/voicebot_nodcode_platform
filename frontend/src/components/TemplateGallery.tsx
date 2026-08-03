import React, { useState } from 'react';
import { LayoutTemplate } from 'lucide-react';
import { Dialog } from './Dialog';
import { EmptyState } from './EmptyState';
import { AGENT_TEMPLATE_DEPARTMENTS, AGENT_TEMPLATES } from '../constants/agentTemplates';
import type { AgentTemplate, AgentTemplateDepartment } from '../constants/agentTemplates';

/** Create Agent > Create from template — one click on a card creates the agent immediately
 * (prefilled name/greeting/prompt from the template) and lands the user in the builder,
 * where editing is optional, not required. Matches the "fewest clicks to a testable agent"
 * goal — no forced customize step in between. */
export function TemplateGallery({
  onUseTemplate,
  onBack,
  busy,
}: {
  onUseTemplate: (template: AgentTemplate) => void;
  onBack: () => void;
  busy: boolean;
}) {
  const [department, setDepartment] = useState<AgentTemplateDepartment>(AGENT_TEMPLATE_DEPARTMENTS[0]);
  const templates = AGENT_TEMPLATES.filter((t) => t.department === department);

  return (
    <Dialog
      title="Create from template"
      icon={<LayoutTemplate size={17} />}
      maxWidth={760}
      onClose={onBack}
      closeOnBackdrop={!busy}
      closeOnEscape={!busy}
      footer={<button onClick={onBack} disabled={busy}>Back</button>}
    >
      <div className="template-department-tabs">
        {AGENT_TEMPLATE_DEPARTMENTS.map((dept) => (
          <button
            key={dept}
            className={dept === department ? 'template-department-tab active' : 'template-department-tab'}
            onClick={() => setDepartment(dept)}
          >
            {dept}
          </button>
        ))}
      </div>

      {templates.length === 0 ? (
        <EmptyState
          icon={<LayoutTemplate size={28} />}
          heading="No templates here yet"
          description="More templates for this department are on the way — try another department, or create this agent with AI or from scratch instead."
        />
      ) : (
        <div className="template-grid">
          {templates.map((template) => (
            <button
              key={template.id}
              className="template-card"
              disabled={busy}
              onClick={() => onUseTemplate(template)}
            >
              <strong>{template.name}</strong>
              <p>{template.description}</p>
            </button>
          ))}
        </div>
      )}
    </Dialog>
  );
}
