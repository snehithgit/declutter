import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import Inventory from './Inventory';
import Duplicates from './Duplicates';
import Classification from './Classification';
import Review from './Review';
import Exports from './Exports';
import {parseRoute,routeHash} from './state.mjs';

type Health = { status: string; milestone: string; version: string; inference: {last_success: string|null};
  database: { schema_version: number; journal_mode: string }; worker: { state: string } };
type Settings = { data_dir: string; library_roots: string[];
  ollama: { url: string; model: string; concurrency: number; protocol: string }; ollama_checked: boolean };

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [route,setRoute] = useState(() => parseRoute(window.location.hash));
  const page = route.page;
  useEffect(() => {
    const sync = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener('hashchange',sync);
    return () => window.removeEventListener('hashchange',sync);
  },[]);
  const setDetail = (name:'review'|'export',detail:string|null) => {
    window.location.hash = routeHash(name,detail);
  };
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
      <a className="brand" href="#/overview" aria-label="PhotoSift home"><span className="mark">▧</span> PhotoSift</a>
      <div className="sidebar-label">YOUR WORKSPACE</div>
      <a className={`nav nav-button ${page === 'overview' ? 'active' : ''}`} href={routeHash('overview')}>◫ <span>Overview</span></a>
      <a className={`nav nav-button ${page === 'duplicates' ? 'active' : ''}`} href={routeHash('duplicates')}>▧ <span>Duplicates</span></a>
      <a className={`nav nav-button ${page === 'classification' ? 'active' : ''}`} href={routeHash('classification')}>✧ <span>AI classification</span></a>
      <a className={`nav nav-button ${page === 'review' ? 'active' : ''}`} href={routeHash('review')}>☑ <span>Review</span></a>
      <a className={`nav nav-button ${page === 'export' ? 'active' : ''}`} href={routeHash('export')}>▣ <span>Copy export</span></a>
      <div className="sidebar-label release-label">RELEASE 1</div>
      {steps.map((step, index) => <div key={step} className={`step ${index === 5 ? 'current' : ''}`}>
        <span className="step-number">{index <= 5 ? '✓' : String(index + 1).padStart(2, '0')}</span>
        <span>{step}</span>{index > 5 && <span className="later">Later</span>}
      </div>)}
      <div className="sidebar-foot"><span className="dot"/> Local processing<br/><small>Your library stays on your devices.</small></div>
    </aside>
    <main>
      <header><span>Workspace <span className="slash">/</span> {page === 'overview' ? 'Overview' : page === 'duplicates' ? 'Duplicates' : page === 'classification' ? 'AI classification' : page === 'review' ? 'Review' : 'Copy export'}</span><span className="badge">MILESTONE 08</span></header>
      <div className="mobile-view-switch" aria-label="Workspace view">
        <a className={page === 'overview' ? 'selected' : ''} href={routeHash('overview')}>Overview</a>
        <a className={page === 'duplicates' ? 'selected' : ''} href={routeHash('duplicates')}>Duplicates</a>
        <a className={page === 'classification' ? 'selected' : ''} href={routeHash('classification')}>AI</a>
        <a className={page === 'review' ? 'selected' : ''} href={routeHash('review')}>Review</a>
        <a className={page === 'export' ? 'selected' : ''} href={routeHash('export')}>Export</a>
      </div>
      {page === 'duplicates' ? <Duplicates/> : page === 'classification' ? <Classification model={settings?.ollama.model ?? ''} endpoint={settings?.ollama.url ?? ''}/> : page === 'review' ? <Review detailHash={route.detail} onDetailChange={value=>setDetail('review',value)}/> : page === 'export' ? <Exports detailId={route.detail} onDetailChange={value=>setDetail('export',value)}/> : <>
      <section className="intro"><div className="eyebrow">A LITTLE LESS CLUTTER. A LOT MORE MEANING.</div>
        <h1>Make room for the moments.</h1>
        <p>A thoughtful home for sorting your photo library. Start scanning your photo library safely, then browse filenames, metadata and thumbnails.</p>
      </section>
      {error && <div role="alert" className="error">{error} <button onClick={() => setRefresh(value => value + 1)}>Retry</button></div>}
      <section className="status-grid" aria-label="System status">
        <Status title="DATABASE" value={health ? 'Connected' : 'Connecting…'} note={health ? `Schema ${health.database.schema_version} · ${health.database.journal_mode.toUpperCase()}` : 'Waiting for the backend'} good={!!health && !error}/>
        <Status title="BACKGROUND WORKER" value={health?.worker.state === 'ready' ? 'Ready' : 'Offline'} note="Separate process · persistent state" good={health?.worker.state === 'ready' && !error}/>
        <Status title="AI INFERENCE" value={health?.inference.last_success ? 'Results available' : 'Configured'} note={health?.inference.last_success ? `Last result: ${health.inference.last_success}` : 'Local model · laptop must be online'} good={!!health?.inference.last_success}/>
      </section>
      <section className="foundation panel"><div className="foundation-icon">✳</div><div><span className="eyebrow">READ-ONLY INVENTORY</span><h2>Discover your photo library.</h2>
        <p>Inventory, duplicates, AI classification, human review and explicitly approved copy-only exports are available.</p>
        <div className="progress"><span/></div><div className="progress-caption"><b>Milestone 6 of 6</b><span>Approved copy-only export</span></div>
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
          <p className="muted">Protocol: {settings?.ollama.protocol === 'decision' ? 'D3 decision API' : 'Vision chat API'}</p>
          <p className="muted">{settings?.ollama.concurrency ?? 1} inference request at a time. M4 sends cached previews to Ollama only when a classification job is started.</p>
        </div>
      </section>
      </>}
      <footer><span>Originals stay untouched. You stay in control.</span><span>PhotoSift {health?.version ?? '0.8.0'}</span></footer>
    </main>
  </div>;
}

function Status({ title, value, note, good }: { title: string; value: string; note: string; good: boolean }) {
  return <div className="panel status"><div className="eyebrow">{title}</div><div className="status-value"><span className={`dot ${good ? '' : 'neutral'}`}/>{value}</div><p>{note}</p></div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
