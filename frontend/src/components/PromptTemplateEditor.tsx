import React, { useEffect, useState } from 'react';
import { Eye, Save } from 'lucide-react';
import { api, type Campaign, type CampaignLead } from '../api';
import { extractTemplateVars, interpolateTemplate } from '../utils/promptTemplate';

/** Prompt editor with {{variable}} detection, unknown-token validation against the campaign's
 * uploaded CSV columns, and a live preview against a chosen lead row. */
export function PromptTemplateEditor({ campaign, leads }: { campaign: Campaign; leads: CampaignLead[] }) {
  const [template, setTemplate] = useState(campaign.prompt_template || '');
  const [unknownVars, setUnknownVars] = useState<string[]>([]);
  const [previewLeadId, setPreviewLeadId] = useState<string>(leads[0]?._id || '');
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'failed'>('idle');

  useEffect(() => {
    setTemplate(campaign.prompt_template || '');
  }, [campaign._id, campaign.prompt_template]);

  useEffect(() => {
    if (!leads.length) return;
    if (!leads.some((l) => l._id === previewLeadId)) setPreviewLeadId(leads[0]._id);
  }, [leads, previewLeadId]);

  useEffect(() => {
    const timer = setTimeout(() => {
      api
        .validateCampaignPromptTemplate(campaign.campaign_key, template)
        .then((r) => setUnknownVars(r.unknown_vars))
        .catch(() => setUnknownVars([]));
    }, 300);
    return () => clearTimeout(timer);
  }, [campaign.campaign_key, template]);

  async function handleSave() {
    setSaveState('saving');
    try {
      await api.saveCampaignPromptTemplate(campaign.campaign_key, template);
      setSaveState('saved');
    } catch {
      setSaveState('failed');
    }
  }

  const previewLead = leads.find((l) => l._id === previewLeadId);
  const usedVars = extractTemplateVars(template);

  return (
    <div className="panel compact prompt-template-editor">
      <div className="prompt-template-header">
        <strong>Prompt template</strong>
        <button className="primary" onClick={handleSave} disabled={saveState === 'saving'}>
          <Save size={14} /> {saveState === 'saving' ? 'Saving...' : 'Save'}
        </button>
      </div>
      <textarea
        rows={4}
        value={template}
        onChange={(e) => { setTemplate(e.target.value); setSaveState('idle'); }}
        placeholder="Hello {{name}}, your purpose of calling is {{reason_of_calling}}."
      />
      {usedVars.length > 0 && (
        <div className="prompt-template-vars">
          {usedVars.map((v) => (
            <span key={v} className={unknownVars.includes(v) ? 'var-chip unknown' : 'var-chip'}>
              {`{{${v}}}`}
            </span>
          ))}
        </div>
      )}
      {unknownVars.length > 0 && (
        <p className="csv-dropzone-error">
          Unknown column{unknownVars.length > 1 ? 's' : ''}: {unknownVars.join(', ')} — not present in the uploaded CSV.
        </p>
      )}

      {leads.length > 0 && (
        <div className="prompt-template-preview">
          <div className="prompt-template-preview-row">
            <Eye size={14} />
            <select value={previewLeadId} onChange={(e) => setPreviewLeadId(e.target.value)}>
              {leads.map((l) => (
                <option key={l._id} value={l._id}>{l.name || l.phone_number}</option>
              ))}
            </select>
          </div>
          {previewLead && <p className="prompt-template-preview-text">{interpolateTemplate(template, previewLead)}</p>}
        </div>
      )}
    </div>
  );
}
