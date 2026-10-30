import { useEffect, useState } from 'react';
import './inventory.css';

type Root = { id: number; path: string; files: number; last_scan_at: string | null; reachability: string; last_error: string | null };
type Totals = { files: number; bytes: number; missing: number; unreadable: number; unsupported: number; unique_contents: number };
type Summary = { totals: Totals; roots: Root[]; formats: { format: string; count: number }[] };
type Job = { id: string; root_id: number; state: string; total: number; completed: number; pause_reason: string | null; last_error: string | null };
type Photo = { id: number; relative_path: string; size: number; format: string; width: number | null; height: number | null;
  capture_time: string | null; date_source: string | null; camera_make: string | null; camera_model: string | null;
  missing: number; analysis_supported: number; error: string | null; thumbnail_url: string | null; preview_url: string | null };
type Page = { total: number; items: Photo[] };
const EMPTY: Summary = { totals: { files: 0, bytes: 0, missing: 0, unreadable: 0, unsupported: 0, unique_contents: 0 }, roots: [], formats: [] };
const count = (n: number) => new Intl.NumberFormat().format(n);
const size = (b: number) => b > 1073741824 ? (b / 1073741824).toFixed(1) + ' GB' : b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : (b / 1024).toFixed(1) + ' KB';
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const result: { detail?: string } = await response.json().catch(() => ({}));
    throw new Error(result.detail ?? 'Request failed (' + response.status + ')');
  }
  return response.json() as Promise<T>;
}

export default function Inventory() {
  const [summary, setSummary] = useState<Summary>(EMPTY);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [page, setPage] = useState<Page>({ total: 0, items: [] });
  const [root, setRoot] = useState('');
  const [format, setFormat] = useState('');
  const [missing, setMissing] = useState('all');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Photo | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const timer = window.setTimeout(() => { setOffset(0); setQuery(search); }, 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    let alive = true;
    async function load() {
      const params = new URLSearchParams({ limit: '60', offset: String(offset) });
      if (root) params.set('root_id', root);
      if (format) params.set('format', format);
      if (query) params.set('search', query);
      if (missing !== 'all') params.set('missing', missing);
      try {
        const [nextSummary, nextJobs, nextPage] = await Promise.all([
          request<Summary>('/api/inventory'), request<Job[]>('/api/jobs'), request<Page>('/api/files?' + params)
        ]);
        if (alive) { setSummary(nextSummary); setJobs(nextJobs); setPage(nextPage); setMessage(''); }
      } catch (error) { if (alive) setMessage(error instanceof Error ? error.message : 'Cannot load inventory'); }
    }
    void load();
    const timer = window.setInterval(() => void load(), 4000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [root, format, query, missing, offset, refresh]);
  useEffect(() => {
    if (!selected) return;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setSelected(null); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [selected]);

  async function startScan() {
    setBusy(true);
    try {
      await request('/api/scans', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(root ? { root_id: Number(root) } : {}) });
      setRefresh(v => v + 1);
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Scan could not start'); }
    finally { setBusy(false); }
  }
  async function action(id: string, operation: 'pause' | 'resume' | 'cancel') {
    setBusy(true);
    try {
      await request('/api/jobs/' + id + '/action', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: operation }) });
      setRefresh(v => v + 1);
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Action failed'); }
    finally { setBusy(false); }
  }
  const active = jobs.some(j => ['queued','running','paused'].includes(j.state) && (!root || j.root_id === Number(root)));
  const filtered = jobs.filter(j => !root || j.root_id === Number(root));
  return <section className="inventory" aria-label="Photo inventory">
    <div className="section-heading inventory-heading"><div><h2>Photo inventory</h2><p>Read-only scanning. Your originals never change.</p></div>
      <button className="primary-action" onClick={() => void startScan()} disabled={busy || active || summary.roots.length === 0}>
        {busy ? 'Working…' : 'Scan library'}</button></div>
    {message && <div className="inventory-error" role="alert">{message}<button onClick={() => setRefresh(v => v + 1)}>Retry</button></div>}
    <div className="inventory-stats">
      <div className="panel"><span>FILES FOUND</span><strong>{count(summary.totals.files)}</strong></div>
      <div className="panel"><span>TOTAL SIZE</span><strong>{size(summary.totals.bytes)}</strong></div>
      <div className="panel"><span>UNIQUE CONTENT</span><strong>{count(summary.totals.unique_contents)}</strong></div>
      <div className="panel"><span>NEEDS ATTENTION</span><strong>{count(summary.totals.missing + summary.totals.unreadable + summary.totals.unsupported)}</strong></div>
    </div>
    <div className="inventory-panel panel"><h3>Library and scan status</h3>
      {summary.roots.length === 0 && <p>No photo library configured. Add a root in config.toml and restart.</p>}
      {summary.roots.map(r => <div key={r.id} className="inventory-root"><div><b>{r.path}</b>
        <small>{count(r.files)} files · {r.last_scan_at ? 'Last completed ' + r.last_scan_at : 'Not scanned yet'} · {r.reachability}</small>
        {r.last_error && <small className="bad-text">{r.last_error}</small>}</div></div>)}
      {filtered.slice(0,5).map(job => <div key={job.id} className="scan-job"><div><b>Scan {job.root_id} · {job.state}</b>
        <small>{count(job.completed)} processed · {count(job.total)} discovered</small>
        {(job.pause_reason || job.last_error) && <small className="bad-text">{job.pause_reason || job.last_error}</small>}</div>
        <div className="scan-actions">
          {['running','queued'].includes(job.state) && <button disabled={busy} onClick={() => void action(job.id, 'pause')}>Pause</button>}
          {['paused','failed'].includes(job.state) && <button disabled={busy} onClick={() => void action(job.id, 'resume')}>Resume</button>}
          {['running','queued','paused','failed'].includes(job.state) && <button disabled={busy} onClick={() => void action(job.id, 'cancel')}>Cancel</button>}
        </div></div>)}
    </div>
    <div className="section-heading"><h2>Browse files</h2><span>{count(page.total)} matches</span></div>
    <div className="inventory-filters">
      <input aria-label="Search filename" placeholder="Search filenames and folders" value={search} onChange={e => setSearch(e.target.value)}/>
      <select aria-label="Library root" value={root} onChange={e => { setRoot(e.target.value); setOffset(0); }}>
        <option value="">All libraries</option>{summary.roots.map(r => <option key={r.id} value={r.id}>{r.path}</option>)}</select>
      <select aria-label="File format" value={format} onChange={e => { setFormat(e.target.value); setOffset(0); }}>
        <option value="">All formats</option>{summary.formats.map(f => <option key={f.format} value={f.format}>{f.format} ({f.count})</option>)}</select>
      <select aria-label="Missing file filter" value={missing} onChange={e => { setMissing(e.target.value); setOffset(0); }}>
        <option value="all">All statuses</option><option value="false">Present</option><option value="true">Missing</option></select>
    </div>
    {page.items.length === 0 ? <div className="panel empty-library">{summary.totals.files ? 'No files match these filters.' : 'Your library is empty. Select Scan library to begin inventory.'}</div> :
      <div className="inventory-grid">{page.items.map(photo => <button className="photo-card" key={photo.id} onClick={() => setSelected(photo)}>
        <div className="photo-image">{photo.thumbnail_url && !photo.missing ? <img src={photo.thumbnail_url} loading="lazy" alt=""/> :
          <span>{photo.missing ? 'Missing' : photo.analysis_supported ? 'No preview' : photo.format || 'File'}</span>}</div>
        <div className="photo-info"><strong title={photo.relative_path}>{photo.relative_path.split('/').pop()}</strong><small>{photo.format} · {size(photo.size)}</small></div>
      </button>)}</div>}
    <div className="inventory-pagination">
      <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 60))}>Previous</button>
      <span>{page.total === 0 ? '0 files' : String(offset + 1) + '–' + String(Math.min(offset + 60, page.total)) + ' of ' + count(page.total)}</span>
      <button disabled={offset + 60 >= page.total} onClick={() => setOffset(offset + 60)}>Next</button>
    </div>
    {selected && <div className="preview-overlay" role="presentation" onClick={() => setSelected(null)}>
      <div className="preview-panel" role="dialog" aria-modal="true" aria-label="File details" onClick={e => e.stopPropagation()}>
        <button className="preview-close" aria-label="Close preview" onClick={() => setSelected(null)}>×</button>
        {selected.preview_url && !selected.missing ? <img className="preview-image" src={selected.preview_url} alt={selected.relative_path}/> :
          <div className="preview-empty">Preview unavailable</div>}
        <h3>{selected.relative_path}</h3>
        <p>{selected.format} · {size(selected.size)} · {selected.width ?? '?'} × {selected.height ?? '?'}</p>
        <p>Captured: {selected.capture_time ?? 'Unknown'} ({selected.date_source ?? 'unknown'})</p>
        <p>Camera: {[selected.camera_make, selected.camera_model].filter(Boolean).join(' ') || 'Unknown'}</p>
        {selected.error && <p className="bad-text">{selected.error}</p>}
        <p className="preview-safety">Read-only inventory. No file operations are available.</p>
      </div></div>}
  </section>;
}
