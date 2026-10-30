import { useEffect, useState } from 'react';
import './duplicates.css';

type Stats = { groups: number; copies: number; extra_copies: number; potential_reclaim_bytes: number };
type Group = { content_hash: string; copy_count: number; extra_copies: number; size_bytes: number;
  potential_reclaim_bytes: number; thumbnail_url: string | null; sample_path: string; sample_format: string | null };
type GroupPage = { total: number; items: Group[] };
type Copy = { id: number; root_path: string; relative_path: string; size: number; format: string | null;
  width: number | null; height: number | null; missing: number; root_enabled: number; capture_time: string | null; error: string | null };
type Copies = { content_hash: string; copy_count: number; missing_count: number; inactive_count: number; size_bytes: number;
  preview_url: string | null; total: number; items: Copy[] };
const number = (n: number) => new Intl.NumberFormat().format(n);
const bytes = (n: number) => n >= 1073741824 ? (n / 1073741824).toFixed(2) + ' GB' :
  n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n >= 1024 ? (n / 1024).toFixed(1) + ' KB' : n + ' B';
const empty: Stats = { groups: 0, copies: 0, extra_copies: 0, potential_reclaim_bytes: 0 };
const groupPageSize = 24;
const copyPageSize = 50;
const fullPath = (copy: Copy) => copy.root_path +
  (copy.root_path.endsWith('/') || copy.root_path.endsWith('\\') ? '' : '/') + copy.relative_path;

export default function Duplicates() {
  const [stats, setStats] = useState<Stats>(empty);
  const [page, setPage] = useState<GroupPage>({ total: 0, items: [] });
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState<'space' | 'copies' | 'size'>('space');
  const [selected, setSelected] = useState<string | null>(null);
  const [details, setDetails] = useState<Copies | null>(null);
  const [copyOffset, setCopyOffset] = useState(0);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const [totalResponse, groupsResponse] = await Promise.all([
          fetch('/api/duplicates/summary'),
          fetch('/api/duplicates?limit=' + groupPageSize + '&offset=' + offset + '&sort=' + sort),
        ]);
        if (!totalResponse.ok || !groupsResponse.ok) throw new Error('Cannot load duplicate groups.');
        const [total, groups] = await Promise.all([totalResponse.json(), groupsResponse.json()]);
        if (live) { setStats(total as Stats); setPage(groups as GroupPage); setError(''); }
      } catch (reason) {
        if (live) setError(reason instanceof Error ? reason.message : 'Cannot load duplicates.');
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 20000);
    return () => { live = false; window.clearInterval(timer); };
  }, [offset, sort, refresh]);

  useEffect(() => {
    if (!selected) { setDetails(null); return; }
    let live = true;
    async function load() {
      try {
        const response = await fetch('/api/duplicates/' + selected + '/files?limit=' + copyPageSize + '&offset=' + copyOffset);
        if (!response.ok) throw new Error(response.status === 404 ?
          'This group is no longer duplicated after a rescan.' : 'Cannot load file locations.');
        const result = await response.json() as Copies;
        if (live) { setDetails(result); setDetailError(''); }
      } catch (reason) {
        if (live) { setDetails(null); setDetailError(reason instanceof Error ? reason.message : 'Cannot load copies.'); }
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 20000);
    return () => { live = false; window.clearInterval(timer); };
  }, [selected, copyOffset, refresh]);

  useEffect(() => {
    if (!selected) return;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setSelected(null); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [selected]);

  function open(digest: string) {
    setSelected(digest);
    setDetails(null);
    setDetailError('');
    setCopyOffset(0);
  }

  return <section className="duplicates" aria-label="Exact duplicate files">
    <div className="duplicates-title"><div>
      <div className="eyebrow">MILESTONE 03 · EXACT FILE MATCHES</div>
      <h1>Find identical files.</h1>
      <p>Groups use SHA-256 hashes collected by the inventory scanner — no AI inference or file changes.</p>
    </div><button className="duplicate-refresh" onClick={() => setRefresh(value => value + 1)}>Refresh groups</button></div>

    <div className="duplicates-counters">
      <div className="panel"><span>IDENTICAL GROUPS</span><strong>{number(stats.groups)}</strong></div>
      <div className="panel"><span>MATCHING FILES</span><strong>{number(stats.copies)}</strong></div>
      <div className="panel"><span>EXTRA COPIES</span><strong>{number(stats.extra_copies)}</strong></div>
      <div className="panel"><span>POTENTIAL EXTRA SPACE</span><strong>{bytes(stats.potential_reclaim_bytes)}</strong></div>
    </div>
    <div className="duplicate-note">
      These are exact byte-for-byte matches, not visually similar photos. Results reflect the latest inventory records.
      Potential extra space is only an estimate. Files are never moved or deleted.
    </div>
    {error && <p className="duplicate-error" role="alert">{error}</p>}
    <div className="duplicate-toolbar"><h2>Duplicate groups</h2><label>Sort by <select value={sort}
      onChange={event => { setSort(event.target.value as typeof sort); setOffset(0); }}>
      <option value="space">Potential space</option><option value="copies">Most copies</option><option value="size">Largest files</option>
    </select></label></div>
    {page.total === 0 ? <div className="panel duplicate-empty">No exact duplicates found yet. Run an inventory scan, or scan additional folders.</div> :
      <div className="duplicate-grid">{page.items.map(group => <button type="button" className="duplicate-group panel"
        key={group.content_hash} onClick={() => open(group.content_hash)}>
        <div className="duplicate-thumb">{group.thumbnail_url ?
          <img src={group.thumbnail_url} loading="lazy" alt=""/> : <span>{group.sample_format || 'FILE'}</span>}</div>
        <div className="duplicate-group-info"><strong title={group.sample_path}>{group.sample_path.split('/').pop()}</strong>
          <small>{number(group.copy_count)} identical copies · {bytes(group.size_bytes)} each</small>
          <small>Potential extra space: {bytes(group.potential_reclaim_bytes)}</small>
          <span className="duplicate-detail-hint">View all locations</span></div>
      </button>)}</div>}
    <div className="duplicate-pager"><button disabled={offset === 0}
      onClick={() => setOffset(Math.max(0, offset - groupPageSize))}>Previous</button>
      <span>{page.total === 0 ? '0 groups' : (offset + 1) + '–' +
        Math.min(offset + groupPageSize, page.total) + ' of ' + number(page.total) + ' groups'}</span>
      <button disabled={offset + groupPageSize >= page.total}
        onClick={() => setOffset(offset + groupPageSize)}>Next</button></div>

    {selected && <div className="duplicate-shade" role="presentation" onClick={() => setSelected(null)}>
      <div className="duplicate-dialog" role="dialog" aria-modal="true" aria-label="Duplicate file locations"
        onClick={event => event.stopPropagation()}>
        <div className="duplicate-dialog-head"><div><h2>Identical file locations</h2>
          <p>Paths are inventory records. No original file operations are possible here.</p></div>
          <button aria-label="Close duplicate details" onClick={() => setSelected(null)}>×</button></div>
        {detailError && <div role="alert" className="duplicate-error">{detailError}</div>}
        {details && <>
          <div className="duplicate-preview">{details.preview_url ?
            <img src={details.preview_url} alt="Representative duplicate"/> : <span>Preview unavailable</span>}</div>
          <div className="duplicate-detail-stats">{number(details.copy_count)} present copies ·
            {' '}{number(details.inactive_count)} missing / inactive records · {bytes(details.size_bytes)} each</div>
          <div className="duplicate-hash" title={details.content_hash}>SHA-256: {details.content_hash}</div>
          <div className="duplicate-copy-list">{details.items.map(copy => <div className="duplicate-copy" key={copy.id}>
            <div className="duplicate-copy-top"><strong>{copy.missing ? 'Previously seen · Missing' : !copy.root_enabled ? 'Library disabled' : 'Present at last scan'}</strong>
              <span>{bytes(copy.size)}</span></div>
            <div className="duplicate-path">{fullPath(copy)}</div>
            <small>{copy.format || 'Unknown format'}{copy.width && copy.height ?
              ' · ' + copy.width + ' × ' + copy.height : ''}{copy.capture_time ? ' · ' + copy.capture_time : ''}</small>
            {copy.error && <small className="duplicate-error">{copy.error}</small>}
          </div>)}</div>
          <div className="duplicate-pager"><button disabled={copyOffset === 0}
            onClick={() => setCopyOffset(Math.max(0, copyOffset - copyPageSize))}>Previous</button>
            <span>{(copyOffset + 1) + '–' + Math.min(copyOffset + copyPageSize, details.total) +
              ' of ' + number(details.total) + ' locations'}</span>
            <button disabled={copyOffset + copyPageSize >= details.total}
              onClick={() => setCopyOffset(copyOffset + copyPageSize)}>Next</button></div>
        </>}
      </div></div>}
  </section>;
}
