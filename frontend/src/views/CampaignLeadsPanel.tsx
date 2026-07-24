import React, { useEffect, useState, useCallback } from 'react';
import { AlertTriangle, ChevronRight, Download, PhoneOutgoing, Pause, Play, Sparkles, Upload, Users } from 'lucide-react';
import { api, type Campaign, type CampaignLead, type CampaignOutcomes, type CampaignProgress, type DialerConfig, type PhoneNumber } from '../api';
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

const EMPTY_DIALER_CONFIG: DialerConfig = {
  channel_name: '',
  channel_id: null,
  bd: null,
  service_id: '',
  service_source: '',
  page_type: 'gallery_image',
  country: 'IN',
  language: 'en',
};

// Known-good defaults for TSPL's dev endpoint (confirmed working against service_id 300 —
// lq5properdialersettings). channel_name/channel_id/bd are the fields TSPL's dev config
// validation actually checks; a placeholder channel_name like "D" gets rejected as
// "config validation failed".
const DEV_DIALER_DEFAULTS: Pick<DialerConfig, 'channel_name' | 'channel_id' | 'bd'> = {
  channel_name: 'DVN Missed Call',
  channel_id: 43,
  bd: 0,
};

/** Fields TSPL's outbound-dialer push payload needs that don't vary per lead — set once
 * per campaign rather than re-typed into every CSV row (see dialer_client.py). */
function DialerConfigPanel({ campaign }: { campaign: Campaign }) {
  const [config, setConfig] = useState<DialerConfig>(campaign.dialer_config || EMPTY_DIALER_CONFIG);
  const [saveState, setSaveState] = useState<'idle' | 'running' | 'failed' | 'saved'>('idle');
  const [phoneNumbers, setPhoneNumbers] = useState<PhoneNumber[]>([]);

  useEffect(() => {
    setConfig(campaign.dialer_config || EMPTY_DIALER_CONFIG);
  }, [campaign.campaign_key, campaign.dialer_config]);

  useEffect(() => {
    api.phoneNumbers().then(setPhoneNumbers).catch(() => setPhoneNumbers([]));
  }, []);

  function update<K extends keyof DialerConfig>(key: K, value: DialerConfig[K]) {
    setConfig((prev) => ({ ...prev, [key]: value }));
    setSaveState('idle');
  }

  // Only offer the dev-defaults shortcut when the bound service_id resolves to a phone
  // number explicitly tagged environment="dev" — this is dev-only config, not something to
  // suggest for a preprod/prod service_id.
  const matchedNumber = phoneNumbers.find((p) => p.service_id === config.service_id);
  const showDevDefaultsButton = matchedNumber?.environment === 'dev';

  function loadDevDefaults() {
    setConfig((prev) => ({ ...prev, ...DEV_DIALER_DEFAULTS }));
    setSaveState('idle');
  }

  async function handleSave() {
    setSaveState('running');
    try {
      await api.saveDialerConfig(campaign.campaign_key, config);
      setSaveState('saved');
    } catch {
      setSaveState('failed');
    }
  }

  return (
    <div className="panel compact">
      <div className="strategy-topbar" style={{ marginBottom: '0.5rem' }}>
        <strong><PhoneOutgoing size={15} style={{ verticalAlign: 'middle', marginRight: 6 }} />Dialer config</strong>
        <button
          className={saveState === 'failed' ? 'fallback-button' : 'primary'}
          onClick={handleSave}
          disabled={saveState === 'running'}
        >
          {saveState === 'running' ? 'Saving...' : saveState === 'failed' ? 'Retry save' : saveState === 'saved' ? 'Saved' : 'Save'}
        </button>
      </div>
      <p className="muted">
        Sent with every lead pushed to TSPL's outbound-dialer API — these don't vary per lead,
        unlike jduid/buyer_city/searched_keyword (set via the CSV).
      </p>
      {showDevDefaultsButton && (
        <button type="button" onClick={loadDevDefaults} style={{ marginBottom: '0.6rem' }}>
          <Sparkles size={14} /> Load default values for dev
        </button>
      )}
      <div className="form-grid">
        <label>Channel name
          <input value={config.channel_name} onChange={(e) => update('channel_name', e.target.value)} placeholder="DVN Missed Call" />
        </label>
        <label>Channel ID
          <input
            type="number"
            value={config.channel_id ?? ''}
            onChange={(e) => update('channel_id', e.target.value === '' ? null : Number(e.target.value))}
          />
        </label>
        <label>BD
          <input
            type="number"
            value={config.bd ?? ''}
            onChange={(e) => update('bd', e.target.value === '' ? null : Number(e.target.value))}
          />
        </label>
        <label>Service ID
          <input value={config.service_id} onChange={(e) => update('service_id', e.target.value)} placeholder="302" />
        </label>
        <label>Service source
          <input value={config.service_source} onChange={(e) => update('service_source', e.target.value)} placeholder="lq_staging" />
        </label>
      </div>
    </div>
  );
}

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
  const [outcomes, setOutcomes] = useState<CampaignOutcomes | null>(null);
  const [campaignActionState, setCampaignActionState] = useState<'idle' | 'running'>('idle');
  const [openCallId, setOpenCallId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [result, progress, campaignOutcomes] = await Promise.all([
        api.getCampaignLeads(campaign.campaign_key),
        api.getCampaignProgress(campaign.campaign_key).catch(() => null),
        api.getCampaignOutcomes(campaign.campaign_key).catch(() => null),
      ]);
      setLeads(result);
      setJobProgress(progress);
      setOutcomes(campaignOutcomes);
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
  const rejectedCount = leads.filter((l) => l.status === 'rejected').length;
  const pushFailedCount = leads.filter((l) => l.status === 'push_failed').length;

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
          <button onClick={() => api.downloadCampaignResults(campaign.campaign_key)} disabled={!leads.length}>
            <Download size={14} /> Download results
          </button>
        </div>
      </div>

      {error && <p className="csv-dropzone-error">{error}</p>}

      {(rejectedCount > 0 || pushFailedCount > 0) && (
        <div className="callout callout-warning">
          <AlertTriangle size={16} />
          <div>
            {rejectedCount > 0 && <span>{rejectedCount} lead{rejectedCount === 1 ? '' : 's'} rejected at upload</span>}
            {rejectedCount > 0 && pushFailedCount > 0 && <span> · </span>}
            {pushFailedCount > 0 && <span>{pushFailedCount} call{pushFailedCount === 1 ? '' : 's'} failed to push to the dialer</span>}
            <span> — download results for details.</span>
          </div>
        </div>
      )}

      <PromptTemplateEditor campaign={campaign} leads={leads} />
      <DialerConfigPanel campaign={campaign} />
      <BudgetCostWidget />

      <div className="page-summary">
        <SummaryCard icon={<Users size={18} />} color="blue" label="Total leads" value={String(total)} sub="in this campaign" />
        <SummaryCard icon={<Users size={18} />} color="green" label="Completed" value={String(jobProgress?.completed ?? completed)} sub={`${jobProgress?.dialing ?? dialing} dialing now`} />
        <SummaryCard icon={<Users size={18} />} color="red" label="Failed" value={String(jobProgress?.failed ?? failed)} sub="no answer / error" />
      </div>

      <div className="panel compact">
        <ProgressBar
          value={(jobProgress?.completed ?? completed) + (jobProgress?.failed ?? failed)}
          max={jobProgress?.total || total || 1}
          label={status === 'paused' ? 'Paused' : `${(jobProgress?.completed ?? completed) + (jobProgress?.failed ?? failed)} / ${jobProgress?.total ?? total} dialed`}
        />
      </div>

      {outcomes && outcomes.total_with_calls > 0 && (
        <div className="panel compact">
          <strong>Call outcomes</strong>
          <p className="muted">
            {outcomes.total_analyzed} / {outcomes.total_with_calls} calls analyzed
          </p>
          <ul className="outcome-breakdown">
            {Object.entries(outcomes.counts)
              .sort(([, a], [, b]) => b - a)
              .map(([label, count]) => (
                <li key={label}>
                  <StatusPill value={label} /> <span>{count}</span>
                </li>
              ))}
          </ul>
        </div>
      )}

      <div className="table-panel">
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th>Contact</th><th>Name</th><th>Status</th><th>Cost (est.)</th></tr>
            </thead>
            <tbody>
              {loading ? (
                <SkeletonTableBody cols={4} rows={4} />
              ) : !leads.length ? (
                <tr><td colSpan={4}>
                  <EmptyState
                    icon={<Users size={32} />}
                    heading="No leads uploaded yet"
                    description="Upload a CSV with a phone_number or jduid column to start this campaign."
                    action={{ label: 'Upload CSV', onClick: () => setShowUpload(true) }}
                  />
                </td></tr>
              ) : leads.map((lead) => (
                <tr
                  key={lead._id}
                  className={lead.call_id ? 'clickable-row' : undefined}
                  onClick={() => lead.call_id && setOpenCallId(lead.call_id)}
                >
                  <td>{lead.phone_number || (lead.jduid ? `jduid: ${lead.jduid}` : '-')}</td>
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
          <CsvUploadDropzone onParsed={setCsvResult} requiredColumn={['phone_number', 'jduid']} />
        </Dialog>
      )}

      {openCallId && (
        <CallDetailDrawer campaignKey={campaign.campaign_key} callId={openCallId} onClose={() => setOpenCallId(null)} />
      )}
    </section>
  );
}
