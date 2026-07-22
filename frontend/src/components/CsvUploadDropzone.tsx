import React, { useCallback, useRef, useState } from 'react';
import { UploadCloud } from 'lucide-react';

export type CsvParseResult = {
  file: File;
  columns: string[];
  rows: string[][];
  rowCount: number;
};

// Simple comma-split parser — sufficient for MVP CSV leads (no embedded commas/quotes handling).
function parseCsv(text: string): { columns: string[]; rows: string[][] } {
  const lines = text.split(/\r\n|\n/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) return { columns: [], rows: [] };
  const columns = lines[0].split(',').map((c) => c.trim());
  const rows = lines.slice(1).map((line) => line.split(',').map((c) => c.trim()));
  return { columns, rows };
}

export function CsvUploadDropzone({ onParsed, requiredColumn = 'phone_number' }: {
  onParsed: (result: CsvParseResult | null) => void;
  requiredColumn?: string;
}) {
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parsed, setParsed] = useState<CsvParseResult | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback((file: File | undefined) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.csv')) {
      setError('Please upload a .csv file.');
      onParsed(null);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || '');
      const { columns, rows } = parseCsv(text);
      if (columns.length === 0) {
        setError('That file looks empty.');
        onParsed(null);
        return;
      }
      if (!columns.includes(requiredColumn)) {
        setError(`Missing required column "${requiredColumn}".`);
        onParsed(null);
        return;
      }
      setError(null);
      const result: CsvParseResult = { file, columns, rows, rowCount: rows.length };
      setParsed(result);
      onParsed(result);
    };
    reader.onerror = () => {
      setError('Could not read that file.');
      onParsed(null);
    };
    reader.readAsText(file);
  }, [onParsed, requiredColumn]);

  return (
    <div>
      <div
        className={`csv-dropzone${dragging ? ' dragging' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFile(e.dataTransfer.files?.[0]);
        }}
      >
        <UploadCloud size={28} className="csv-dropzone-icon" aria-hidden />
        <strong>{parsed ? parsed.file.name : 'Drag & drop a CSV, or click to browse'}</strong>
        <span className="csv-dropzone-hint">
          Must include a <code>{requiredColumn}</code> column. Extra columns become call variables.
        </span>
        <input
          ref={inputRef}
          type="file"
          accept=".csv"
          hidden
          onChange={(e) => handleFile(e.target.files?.[0])}
        />
      </div>

      {error && <p className="csv-dropzone-error">{error}</p>}

      {parsed && (
        <div style={{ marginTop: '0.75rem' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem', marginBottom: '0.6rem' }}>
            {parsed.columns.map((col) => (
              <span key={col} className={`csv-column-tag${col === requiredColumn ? ' required' : ''}`}>
                {col}
              </span>
            ))}
          </div>
          <div className="csv-preview-table-wrap">
            <table className="csv-preview-table">
              <thead>
                <tr>{parsed.columns.map((c) => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {parsed.rows.slice(0, 5).map((row, i) => (
                  <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="csv-dropzone-hint" style={{ marginTop: '0.4rem' }}>
            Showing {Math.min(5, parsed.rowCount)} of {parsed.rowCount} rows.
          </p>
        </div>
      )}
    </div>
  );
}
