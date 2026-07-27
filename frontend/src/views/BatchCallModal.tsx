import React, { useEffect, useState } from 'react';
import { Megaphone, Sparkles } from 'lucide-react';
import { api } from '../api';
import type { PhoneNumber } from '../api';
import { Dialog } from '../components/Dialog';
import { CsvUploadDropzone, type CsvParseResult } from '../components/CsvUploadDropzone';

// Known-good defaults for TSPL's dev endpoint (confirmed working via a direct push —
// verified 2026-07-24 with service_id 300 returning {"code": 201, "msg": "success"}).
// Only offered for service IDs whose phone number is tagged environment="dev" — this is
// dev-only config, not something to suggest for a preprod/prod service_id.
const DEV_DIALER_DEFAULTS = { channelName: 'DVN Missed Call', channelId: '43', bd: '0', serviceSource: 'lq_staging' };

/** "Create a batch call" — one flow that replaces separately creating a campaign, assigning
 * a bot, configuring the dialer, uploading leads, and starting/scheduling it. The bot is
 * derived from the chosen "service_id" (one phone number = one bot, via the existing static
 * SIP dispatch-rule lookup) rather than picked directly — see plans/quizzical-dazzling-wand.md. */
export function BatchCallModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [phoneNumbers, setPhoneNumbers] = useState<PhoneNumber[]>([]);
  const [loadingNumbers, setLoadingNumbers] = useState(true);

  const [name, setName] = useState('');
  const [serviceId, setServiceId] = useState('');

  const [channelName, setChannelName] = useState('');
  const [channelId, setChannelId] = useState('');
  const [bd, setBd] = useState('');
  const [serviceSource, setServiceSource] = useState('');
  const [pageType, setPageType] = useState('gallery_image');
  const [country, setCountry] = useState('IN');
  const [language, setLanguage] = useState('en');

  const [csv, setCsv] = useState<CsvParseResult | null>(null);

  const [sendMode, setSendMode] = useState<'now' | 'schedule'>('now');
  const [scheduledAt, setScheduledAt] = useState('');

  const [submitting, setSubmitting] = useState<'idle' | 'draft' | 'send' | 'failed'>('idle');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.phoneNumbers()
      .then((list) => { if (!cancelled) setPhoneNumbers(list); })
      .finally(() => { if (!cancelled) setLoadingNumbers(false); });
    return () => { cancelled = true; };
  }, []);

  const selectedNumber = phoneNumbers.find((p) => p.service_id === serviceId);
  const campaignKey = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  const showDevDefaultsButton = !!serviceId && selectedNumber?.environment === 'dev';

  function loadDevDefaults() {
    setChannelName(DEV_DIALER_DEFAULTS.channelName);
    setChannelId(DEV_DIALER_DEFAULTS.channelId);
    setBd(DEV_DIALER_DEFAULTS.bd);
    setServiceSource(DEV_DIALER_DEFAULTS.serviceSource);
  }

  const canSubmit =
    name.trim().length > 0 &&
    !!serviceId &&
    !!selectedNumber?.assigned_bot_id &&
    channelName.trim().length > 0 &&
    channelId.trim().length > 0 &&
    bd.trim().length > 0 &&
    serviceSource.trim().length > 0;

  async function persistCampaign() {
    if (!selectedNumber?.assigned_bot_id) throw new Error('Selected service ID has no bot assigned');

    await api.saveCampaignStrategy(campaignKey, name.trim(), {
      enabled: true,
      outcome_rules: [],
      call_windows: [],
      attempt_sequence: [],
      max_attempts_total: 5,
      max_attempts_per_day: 2,
      lead_expiry_days: 30,
      priority: 'normal',
    });
    await api.assignCampaignBot(campaignKey, selectedNumber.assigned_bot_id);
    await api.saveDialerConfig(campaignKey, {
      channel_name: channelName.trim(),
      channel_id: Number(channelId),
      bd: Number(bd),
      service_id: serviceId,
      service_source: serviceSource.trim(),
      page_type: pageType.trim() || 'gallery_image',
      country: country.trim() || 'IN',
      language: language.trim() || 'en',
    });
    if (csv) {
      await api.uploadCampaignLeads(campaignKey, csv.file);
    }
  }

  async function handleSaveDraft() {
    setSubmitting('draft');
    setError(null);
    try {
      await persistCampaign();
      onCreated();
      onClose();
    } catch (err) {
      setSubmitting('failed');
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleSend() {
    setSubmitting('send');
    setError(null);
    try {
      await persistCampaign();
      if (sendMode === 'now') {
        await api.scheduleCampaign(campaignKey, { send_now: true });
      } else {
        if (!scheduledAt) throw new Error('Pick a date and time to schedule this batch call');
        // datetime-local has no timezone — always interpreted as Asia/Kolkata (no picker shown).
        await api.scheduleCampaign(campaignKey, { send_now: false, scheduled_at: scheduledAt });
      }
      onCreated();
      onClose();
    } catch (err) {
      setSubmitting('failed');
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const busy = submitting === 'draft' || submitting === 'send';

  return (
    <Dialog
      title="Create a batch call"
      icon={<Megaphone size={17} />}
      onClose={onClose}
      closeOnBackdrop={!busy}
      closeOnEscape={!busy}
      maxWidth={640}
      footer={
        <>
          <button onClick={onClose} disabled={busy}>Cancel</button>
          <button onClick={handleSaveDraft} disabled={!canSubmit || busy}>
            {submitting === 'draft' ? 'Saving...' : 'Save as draft'}
          </button>
          <button className="primary" onClick={handleSend} disabled={!canSubmit || busy}>
            {submitting === 'send' ? 'Sending...' : 'Send'}
          </button>
        </>
      }
    >
      <div className="form-grid">
        <label className="full">
          Batch Call Name
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Bangalore Q1 Leads" />
        </label>

        <label className="full">
          From Service ID
          <select value={serviceId} onChange={(e) => setServiceId(e.target.value)} disabled={loadingNumbers}>
            <option value="">{loadingNumbers ? 'Loading...' : '— select a service ID —'}</option>
            {phoneNumbers.filter((p) => p.service_id).map((p) => (
              <option key={p._id} value={p.service_id}>
                {p.service_id} — {p.number}{p.name ? ` (${p.name})` : ''}
              </option>
            ))}
          </select>
          {serviceId && !selectedNumber?.assigned_bot_id && (
            <span className="csv-dropzone-error">
              This service ID has no bot assigned — bind one from Phone Numbers first.
            </span>
          )}
        </label>

        <fieldset className="full" style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '0.75rem 1rem' }}>
          <legend style={{ fontSize: '0.85rem', fontWeight: 600 }}>Dialer settings (required — sent to TSPL on every call)</legend>
          {showDevDefaultsButton && (
            <button type="button" onClick={loadDevDefaults} style={{ marginBottom: '0.6rem' }}>
              <Sparkles size={14} /> Load default values for dev
            </button>
          )}
          <div className="form-grid">
            <label>
              Channel name
              <input value={channelName} onChange={(e) => setChannelName(e.target.value)} placeholder="DVN Missed Call" />
            </label>
            <label>
              Channel ID
              <input value={channelId} onChange={(e) => setChannelId(e.target.value)} placeholder="43" inputMode="numeric" />
            </label>
            <label>
              BD
              <input value={bd} onChange={(e) => setBd(e.target.value)} placeholder="0" inputMode="numeric" />
            </label>
            <label>
              Service source
              <input value={serviceSource} onChange={(e) => setServiceSource(e.target.value)} placeholder="lq_staging" />
            </label>
            <label>
              Page type <small>(optional)</small>
              <input value={pageType} onChange={(e) => setPageType(e.target.value)} />
            </label>
            <label>
              Country <small>(optional)</small>
              <input value={country} onChange={(e) => setCountry(e.target.value)} />
            </label>
            <label>
              Language <small>(optional)</small>
              <input value={language} onChange={(e) => setLanguage(e.target.value)} />
            </label>
          </div>
        </fieldset>

        <label className="full">
          <span style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            Upload Recipients
            <button type="button" onClick={() => api.downloadLeadsTemplate()}>Download the template</button>
          </span>
          <CsvUploadDropzone onParsed={setCsv} requiredColumn={['jduid', 'phone_number']} />
          <span className="csv-dropzone-hint">Each row needs a <code>jduid</code> or a <code>phone_number</code>.</span>
        </label>

        <label className="full">
          When to send the calls
          <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.35rem' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
              <input type="radio" checked={sendMode === 'now'} onChange={() => setSendMode('now')} /> Send Now
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
              <input type="radio" checked={sendMode === 'schedule'} onChange={() => setSendMode('schedule')} /> Schedule
            </label>
          </div>
        </label>

        {sendMode === 'schedule' && (
          <label className="full">
            Date &amp; time
            <input
              type="datetime-local"
              value={scheduledAt}
              onChange={(e) => setScheduledAt(e.target.value)}
            />
            <span className="csv-dropzone-hint">Always Asia/Kolkata (IST) — this system only runs campaigns in that timezone.</span>
          </label>
        )}

        {error && <p className="csv-dropzone-error full">{error}</p>}
      </div>
    </Dialog>
  );
}
