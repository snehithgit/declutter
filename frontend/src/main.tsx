import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import Inventory from './Inventory';
import Duplicates from './Duplicates';
import Classification from './Classification';

type Health = { status: string; milestone: string; version: string;
  database: { schema_version: number; journal_mode: string }; worker: { state: string } };
type Settings = { data_dir: string; library_roots: string[];
  ollama: { url: string; model: string; concurrency: number }; ollama_checked: boolean };

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [page, setPage] = useState<'overview' | 'duplicates' | 'classification'>('overview');
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const responses = await Promise.all(['/api/health', '/api/settings'].map(url => fetch(url, { signal: controller.signal })));
        if (responses.some(response => !response.ok)) throw new Error('PhotoSift could not load its status.');
        const [nextHealth, nextSettings] = await Promise.all(responses.map(response => response.json()));
        setHealth(nextHealth); setSettings(nextSettings); setError('');
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Connection failed.');
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [refresh]);
  const steps = ['Foundation', 'Inventory', 'Duplicates', 'AI classification', 'Review', 'Copy export'];
  return <div className="app">
    <aside>
      <a className="brand" href="/" aria-label="PhotoSift home"><span className="mark">▧</span> PhotoSift</a>
      <div className="sidebar-label">YOUR WORKSPACE</div>
      <button type="button" className={`nav nav-button ${page === 'overview' ? 'active' : ''}`} onClick={() => setPage('overview')}>◫ <span>Overview</span></button>
      <button type="button" className={`nav nav-button ${page === 'duplicates' ? 'active' : ''}`} onClick={() => setPage('duplicates')}>▧ <span>Duplicates</span></button>
      <button type="button" className={`nav nav-button ${page === 'classification' ? 'active' : ''}`} onClick={() => setPage('classification')}>✧ <span>AI classification</span></button>
      <div className="sidebar-label release-label">RELEASE 1</div>
      {steps.map((step, index) => <div key={step} className={`step ${index === 3 ? 'current' : ''}`}>
        <span className="step-number">{index <= 3 ? '✓' : String(index + 1).padStart(2, '0')}</span>
        <span>{step}</span>{index > 3 && <span className="later">Later</span>}
      </div>)}
      <div className="sidebar-foot"><span className="dot"/> Local processing<br/><small>Your library stays on your devices.</small></div>
    </aside>
    <main>
      <header><span>Workspace <span className="slash">/</span> {page === 'overview' ? 'Overview' : page === 'duplicates' ? 'Duplicates' : 'AI classification'}</span><span className="badge">MILESTONE 04</span></header>
      <div className="mobile-view-switch" aria-label="Workspace view">
        <button type="button" className={page === 'overview' ? 'selected' : ''} onClick={() => setPage('overview')}>Overview</button>
        <button type="button" className={page === 'duplicates' ? 'selected' : ''} onClick={() => setPage('duplicates')}>Duplicates</button>
        <button type="button" className={page === 'classification' ? 'selected' : ''} onClick={() => setPage('classification')}>AI</button>
      </div>
      {page === 'duplicates' ? <Duplicates/> : page === 'classification' ? <Classification model={settings?.ollama.model ?? ''} endpoint={settings?.ollama.url ?? ''}/> : <>
      <section className="intro"><div className="eyebrow">A LITTLE LESS CLUTTER. A LOT MORE MEANING.</div>
        <h1>Make room for the moments.</h1>
        <p>A thoughtful home for sorting your photo library. Start scanning your photo library safely, then browse filenames, metadata and thumbnails.</p>
      </section>
      {error && <div role="alert" className="error">{error} <button onClick={() => setRefresh(value => value + 1)}>Retry</button></div>}
      <section className="status-grid" aria-label="System status">
        <Status title="DATABASE" value={health ? 'Connected' : 'Connecting…'} note={health ? `Schema ${health.database.schema_version} · ${health.database.journal_mode.toUpperCase()}` : 'Waiting for the backend'} good={!!health && !error}/>
        <Status title="BACKGROUND WORKER" value={health?.worker.state === 'ready' ? 'Ready' : 'Offline'} note="Separate process · persistent state" good={health?.worker.state === 'ready' && !error}/>
        <Status title="AI INFERENCE" value="Not checked yet" note="Ollama only contacted during classification" good={false}/>
      </section>
      <section className="foundation panel"><div className="foundation-icon">✳</div><div><span className="eyebrow">READ-ONLY INVENTORY</span><h2>Discover your photo library.</h2>
        <p>Inventory, duplicates and local AI classification are available. Review decisions and exports remain disabled.</p>
        <div className="progress"><span/></div><div className="progress-caption"><b>Milestone 4 of 6</b><span>Resumable AI classification</span></div>
      </div></section>
      <Inventory/>
      <div className="section-heading"><h2>Workspace configuration</h2><span>Read-only overview</span></div>
      <section className="config-grid">
        <div className="panel config-panel"><div className="card-heading"><span>◧</span><h3>Photo library</h3></div>
          {settings?.library_roots.length ? settings.library_roots.map(root => <code key={root}>{root}</code>) : <p>No library roots configured.</p>}
          <p className="muted">This root is available for read-only inventory scanning.</p>
          <div className="divider"/><h4>Database & cache</h4><code>{settings?.data_dir ?? 'Loading…'}</code><p className="muted">Stored locally on the ZimaOS app host.</p>
        </div>
        <div className="panel config-panel"><div className="card-heading"><span>⌘</span><h3>Local AI connection</h3></div>
          <h4>Model</h4><code>{settings?.ollama.model ?? 'Loading…'}</code>
          <h4>Ollama endpoint</h4><code>{settings?.ollama.url ?? 'Loading…'}</code>
          <p className="muted">{settings?.ollama.concurrency ?? 1} inference request at a time. M4 sends cached previews to Ollama only when a classification job is started.</p>
        </div>
      </section>
      </>}
      <footer><span>Originals stay untouched. You stay in control.</span><span>PhotoSift {health?.version ?? '0.4.0'}</span></footer>
    </main>
  </div>;
}

function Status({ title, value, note, good }: { title: string; value: string; note: string; good: boolean }) {
  return <div className="panel status"><div className="eyebrow">{title}</div><div className="status-value"><span className={`dot ${good ? '' : 'neutral'}`}/>{value}</div><p>{note}</p></div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
