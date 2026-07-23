import React, { useMemo, useRef, useState } from 'react';
import { Upload, Phone, User, Database, AlertTriangle, FileSpreadsheet, X } from 'lucide-react';

// Minimal, dependency-free CSV parser. Handles quoted fields, escaped quotes ("") and
// both \n and \r\n line endings. Good enough for the upload preview; server-side ingestion
// will do the authoritative parse later.
function parseCSV(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += c;
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ',') {
      row.push(field); field = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); field = '';
      rows.push(row); row = [];
    } else {
      field += c;
    }
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows.filter(r => r.some(c => c.trim() !== ''));
}

function detectColumn(headers: string[], patterns: RegExp): number {
  const idx = headers.findIndex(h => patterns.test(h.trim()));
  return idx;
}

const PREVIEW_ROWS = 8;

export function CampaignsV2View() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState('');
  const [headers, setHeaders] = useState<string[]>([]);
  const [rows, setRows] = useState<string[][]>([]);
  const [phoneCol, setPhoneCol] = useState<number>(-1);
  const [nameCol, setNameCol] = useState<number>(-1);
  const [error, setError] = useState('');

  function reset() {
    setFileName(''); setHeaders([]); setRows([]); setPhoneCol(-1); setNameCol(-1); setError('');
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function handleFile(file: File) {
    setError('');
    if (!/\.csv$/i.test(file.name)) { setError('Please choose a .csv file.'); return; }
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = parseCSV(String(reader.result || ''));
        if (parsed.length < 2) { setError('CSV needs a header row and at least one data row.'); return; }
        const hdr = parsed[0].map(h => h.trim());
        setHeaders(hdr);
        setRows(parsed.slice(1));
        setFileName(file.name);
        setPhoneCol(detectColumn(hdr, /phone|mobile|number|contact|msisdn/i));
        setNameCol(detectColumn(hdr, /^name$|full.?name|customer|first.?name/i));
      } catch {
        setError('Could not parse this CSV.');
      }
    };
    reader.onerror = () => setError('Could not read the file.');
    reader.readAsText(file);
  }

  const dynamicFields = useMemo(
    () => headers.map((h, i) => ({ h, i })).filter(({ i }) => i !== phoneCol && i !== nameCol),
    [headers, phoneCol, nameCol],
  );

  return (
    <section className="builder-layout">
      <div className="form-section">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <FileSpreadsheet size={18} />
          <div>
            <h2 style={{ margin: 0 }}>Campaigns v2 — Lead list upload</h2>
            <small>Upload a CSV of leads. It should include a phone number and name column; every other
              column is treated as a dynamic field usable in the bot ({'{{field}}'}).</small>
          </div>
        </div>
      </div>

      {/* Upload */}
      <div className="form-section">
        <label className="full">Lead CSV</label>
        <div
          onDragOver={e => { e.preventDefault(); }}
          onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) handleFile(f); }}
          style={{
            marginTop: '0.5rem', border: '1.5px dashed var(--border)', borderRadius: 8,
            padding: '1.4rem', textAlign: 'center', cursor: 'pointer', background: 'var(--surface-2)',
          }}
          onClick={() => fileInputRef.current?.click()}
        >
          <Upload size={22} style={{ opacity: 0.7 }} />
          <div style={{ marginTop: '0.4rem', fontSize: '0.9rem' }}>
            {fileName ? <strong>{fileName}</strong> : 'Click to choose or drop a .csv file here'}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,text/csv"
            style={{ display: 'none' }}
            onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
          />
        </div>
        {fileName && (
          <button type="button" className="ghost" style={{ marginTop: '0.6rem' }} onClick={reset}>
            <X size={14} /> Clear
          </button>
        )}
        {error && (
          <div style={{ marginTop: '0.6rem', color: 'var(--danger, #c00)', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <AlertTriangle size={14} /> {error}
          </div>
        )}
      </div>

      {headers.length > 0 && (
        <>
          {/* Column mapping */}
          <div className="form-section">
            <h2 style={{ marginTop: 0 }}>Column mapping</h2>
            <small>{rows.length} row(s) · {headers.length} column(s) detected.</small>
            <div style={{ display: 'flex', gap: '0.7rem', flexWrap: 'wrap', marginTop: '0.6rem' }}>
              <label style={{ flex: '1 1 220px' }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}><Phone size={13} /> Phone number column</span>
                <select value={phoneCol} onChange={e => setPhoneCol(Number(e.target.value))}>
                  <option value={-1}>— none —</option>
                  {headers.map((h, i) => <option key={i} value={i}>{h || `column ${i + 1}`}</option>)}
                </select>
              </label>
              <label style={{ flex: '1 1 220px' }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}><User size={13} /> Name column</span>
                <select value={nameCol} onChange={e => setNameCol(Number(e.target.value))}>
                  <option value={-1}>— none —</option>
                  {headers.map((h, i) => <option key={i} value={i}>{h || `column ${i + 1}`}</option>)}
                </select>
              </label>
            </div>
            {phoneCol === -1 && <div style={{ marginTop: '0.5rem', color: 'var(--warning, #b58100)', fontSize: '0.82rem' }}>No phone column detected — pick one above.</div>}
          </div>

          {/* Dynamic fields */}
          <div className="form-section">
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <Database size={15} /><h2 style={{ margin: 0 }}>Dynamic fields</h2>
            </div>
            <small>These extra columns become variables available to the bot. Use them in prompts as {'{{field}}'}.</small>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.6rem' }}>
              {dynamicFields.length === 0 && <span style={{ color: 'var(--muted)', fontStyle: 'italic' }}>None — every column is mapped to phone/name.</span>}
              {dynamicFields.map(({ h, i }) => (
                <span key={i} style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 4, padding: '2px 8px', fontSize: '0.8rem', fontFamily: 'monospace' }}>
                  {`{{${(h || `column_${i + 1}`).trim().replace(/\s+/g, '_')}}}`}
                </span>
              ))}
            </div>
          </div>

          {/* Preview */}
          <div className="form-section">
            <h2 style={{ marginTop: 0 }}>Preview <small style={{ fontWeight: 400 }}>(first {Math.min(PREVIEW_ROWS, rows.length)} of {rows.length})</small></h2>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ borderCollapse: 'collapse', fontSize: '0.82rem', width: '100%' }}>
                <thead>
                  <tr>
                    {headers.map((h, i) => (
                      <th key={i} style={{ textAlign: 'left', padding: '4px 8px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }}>
                        {h || `column ${i + 1}`}
                        {i === phoneCol && <span style={{ color: 'var(--accent, #4a7)', marginLeft: 4 }}>· phone</span>}
                        {i === nameCol && <span style={{ color: 'var(--accent, #4a7)', marginLeft: 4 }}>· name</span>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, PREVIEW_ROWS).map((r, ri) => (
                    <tr key={ri}>
                      {headers.map((_, ci) => (
                        <td key={ci} style={{ padding: '4px 8px', borderBottom: '1px solid var(--surface-2)', whiteSpace: 'nowrap' }}>{r[ci] ?? ''}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="form-section" style={{ background: 'var(--surface-2)', fontSize: '0.82rem', color: 'var(--muted)' }}>
            UI only for now — nothing is uploaded or saved yet. Wiring this to a backend campaign
            ingestion endpoint is the next step.
          </div>
        </>
      )}
    </section>
  );
}
