import React, { useEffect, useState, useCallback } from 'react';
import { ChevronRight, Pause, Play, Upload, Users } from 'lucide-react';
import { api, type Campaign, type CampaignLead, type CampaignProgress } from '../api';
import { StatusPill } from '../components/StatusPill';
import { SkeletonTableBody } from '../components/SkeletonTableBody';
import { EmptyState } from '../components/EmptyState';
import { SummaryCard } from '../components/SummaryCard';
import { ProgressBar } from '../components/ProgressBar';
import { Dialog } from '../components/Dialog';
import { CsvUploadDropzone, type CsvParseResult } from '../components/CsvUploadDropzone';
import { PromptTemplateEditor } from '../components/PromptTemplateEditor';
import { BudgetCostWidget } from '../components/BudgetCostWidget';
import { CallDetailDrawer } from '../components/CallDetailDrawer';

const POLL_INTERVAL_MS = 3000;

export function CampaignLeadsPanel({ campaign, onBack }: {
  campaign: Campaign;
  onBack: () => void;
}) {
  const [leads, setLeads] = useState<CampaignLead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [csvResult, setCsvResult] = useState<CsvParseResult | null>(null);
  const [uploadState, setUploadState] = useState<'idle' | 'running' | 'failed'>('idle');
  const [status, setStatus] = useState(campaign.status || 'draft');
  const [jobProgress, setJobProgress] = useState<CampaignProgress | null>(null);
  const [campaignActionState, setCampaignActionState] = useState<'idle' | 'running'>('idle');
  const [openCallId, setOpenCallId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [result, progress] = await Promise.all([
        api.getCampaignLeads(campaign.campaign_key),
        api.getCampaignProgress(campaign.campaign_key).catch(() => null),
      ]);
      setLeads(result);
      setJobProgress(progress);
      setError(null);
    } catch (err) {
      setError('Could not load leads yet — this endpoint may not be live in this environment.');
    } finally {
      setLoading(false);
    }
  }, [campaign.campaign_key]);

  useEffect(() => {
    setLoading(true);
    refresh();
    if (status !== 'active') return;
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refresh, status]);

  async function handleUpload() {
    if (!csvResult) return;
    setUploadState('running');
    try {
      await api.uploadCampaignLeads(campaign.campaign_key, csvResult.file);
      setUploadState('idle');
      setShowUpload(false);
      setCsvResult(null);
      refresh();
    } catch (err) {
      setUploadState('failed');
    }
  }

  async function handleStart() {
    setCampaignActionState('running');
    try {
      await api.startCampaign(campaign.campaign_key);
      setStatus('active');
      await refresh();
    } finally {
      setCampaignActionState('idle');
    }
  }

  async function handlePause() {
    setCampaignActionState('running');
    try {
      await api.setCampaignStatus(campaign.campaign_key, 'paused');
      setStatus('paused');
    } finally {
      setCampaignActionState('idle');
    }
  }

  async function handleResume() {
    setCampaignActionState('running');
    try {
      await api.setCampaignStatus(campaign.campaign_key, 'active');
      setStatus('active');
      await refresh();
    } finally {
      setCampaignActionState('idle');
    }
  }

  const total = leads.length;
  const completed = leads.filter((l) => l.status === 'completed').length;
  const failed = leads.filter((l) => l.status === 'failed').length;
  const dialing = leads.filter((l) => l.status === 'dialing').length;
  const dialed = completed + failed;

  return (
    <section className="content-grid">
      <div className="strategy-topbar">
        <div className="strategy-breadcrumb">
          <button onClick={onBack}><ChevronRight className="rotate-180" size={16} /> Campaigns</button>
          <ChevronRight size={14} />
          <strong>{campaign.name}</strong>
          <ChevronRight size={14} />
          <span>Leads</span>
        </div>
        <div className="campaign-run-controls">
          {status !== 'active' ? (
            <button
              className="primary"
              onClick={status === 'paused' ? handleResume : handleStart}
              disabled={campaignActionState === 'running' || !leads.length}
            >
              <Play size={14} /> {status === 'paused' ? 'Resume' : 'Start'} campaign
            </button>
          ) : (
            <button onClick={handlePause} disabled={campaignActionState === 'running'}>
              <Pause size={14} /> Pause
            </button>
          )}
          <button className="primary" onClick={() => setShowUpload(true)}>
            <Upload size={14} /> Upload CSV
          </button>
        </div>
      </div>

      {error && <p className="csv-dropzone-error">{error}</p>}

      <PromptTemplateEditor campaign={campaign} leads={leads} />
      <BudgetCostWidget />

      <div className="page-summary">
        <SummaryCard icon={<Users size={18} />} color="blue" label="Total leads" value={String(total)} sub="in this campaign" />
        <SummaryCard icon={<Users size={18} />} color="green" label="Completed" value={String(jobProgress?.completed ?? completed)} sub={`${jobProgress?.in_progress ?? dialing} dialing now`} />
        <SummaryCard icon={<Users size={18} />} color="red" label="Failed" value={String(jobProgress?.failed ?? failed)} sub="no answer / error" />
      </div>

      <div className="panel compact">
        <ProgressBar
          value={(jobProgress?.completed ?? completed) + (jobProgress?.failed ?? failed)}
          max={jobProgress?.total || total || 1}
          label={status === 'paused' ? 'Paused' : `${(jobProgress?.completed ?? completed) + (jobProgress?.failed ?? failed)} / ${jobProgress?.total ?? total} dialed`}
        />
      </div>

      <div className="table-panel">
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th>Phone</th><th>Name</th><th>Status</th><th>Cost (est.)</th></tr>
            </thead>
            <tbody>
              {loading ? (
                <SkeletonTableBody cols={4} rows={4} />
              ) : !leads.length ? (
                <tr><td colSpan={4}>
                  <EmptyState
                    icon={<Users size={32} />}
                    heading="No leads uploaded yet"
                    description="Upload a CSV with a phone_number column to start this campaign."
                    action={{ label: 'Upload CSV', onClick: () => setShowUpload(true) }}
                  />
                </td></tr>
              ) : leads.map((lead) => (
                <tr
                  key={lead._id}
                  className={lead.call_id ? 'clickable-row' : undefined}
                  onClick={() => lead.call_id && setOpenCallId(lead.call_id)}
                >
                  <td>{lead.phone_number}</td>
                  <td>{lead.name || '-'}</td>
                  <td><StatusPill value={lead.status} /></td>
                  <td>{lead.estimated_cost != null ? `$${lead.estimated_cost.toFixed(3)}` : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {showUpload && (
        <Dialog
          title="Upload campaign leads"
          icon={<Upload size={17} />}
          onClose={() => { setShowUpload(false); setCsvResult(null); }}
          closeOnBackdrop={uploadState !== 'running'}
          closeOnEscape={uploadState !== 'running'}
          footer={
            <>
              <button onClick={() => { setShowUpload(false); setCsvResult(null); }} disabled={uploadState === 'running'}>Cancel</button>
              <button
                className={uploadState === 'failed' ? 'fallback-button' : 'primary'}
                onClick={handleUpload}
                disabled={uploadState === 'running' || !csvResult}
              >
                {uploadState === 'running' ? 'Uploading...' : uploadState === 'failed' ? 'Retry upload' : 'Upload'}
              </button>
            </>
          }
        >
          <CsvUploadDropzone onParsed={setCsvResult} />
        </Dialog>
      )}

      {openCallId && (
        <CallDetailDrawer campaignKey={campaign.campaign_key} callId={openCallId} onClose={() => setOpenCallId(null)} />
      )}
    </section>
  );
}
