import { useEffect, useState } from 'react';
import './classification.css';

type Job = { id:string; state:string; total:number; completed:number; pause_reason:string|null;
  counts:Record<string,number>; parameters:{model:string;prompt_version:string;force:boolean;root_path?:string;folder?:string};
  rate_per_minute:number; eta_seconds:number|null; created_at:string; };
type Prediction = { id:number; content_hash:string; category:string; subcategory:string; confidence:number;
  reason:string; possibly_important:number; uncertain:number; thumbnail_url:string|null; duration_ms:number; };
type FolderPage = {images:number;pending:number;total:number;children:{name:string;folder:string;images:number}[]};
type Options = {roots:{id:number;path:string}[];prompt_version:string;concurrency:number};
const fmt=(n:number)=>new Intl.NumberFormat().format(n);
async function api<T>(url:string, init?:RequestInit):Promise<T> {
  const response=await fetch(url,init);
  if(!response.ok){ const body=await response.json().catch(()=>({detail:'Request failed'})) as {detail?:string};
    throw new Error(body.detail || 'Request failed'); }
  return response.json() as Promise<T>;
}
const POST=(data:object):RequestInit=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});

export default function Classification({model,endpoint}:{model:string;endpoint:string}) {
  const [jobs,setJobs]=useState<Job[]>([]);
  const [results,setResults]=useState<Prediction[]>([]);
  const [force,setForce]=useState(false);
  const [count,setCount]=useState(''); const [busy,setBusy]=useState(false);
  const [error,setError]=useState(''); const [refresh,setRefresh]=useState(0);
  useEffect(()=>{
    let live=true;
    async function load(){
      try{const [j,r]=await Promise.all([
        api<Job[]>('/api/classification/jobs'),api<Prediction[]>('/api/classification/results?limit=48')]);
        if(live){setJobs(j);setResults(r);}
      }catch(reason){if(live)setError(reason instanceof Error?reason.message:'Could not load classification');}
    }
    void load(); const timer=setInterval(()=>void load(),4000);
    return ()=>{live=false;clearInterval(timer);};
  },[refresh]);
  const [options,setOptions]=useState<Options>();
  const [root,setRoot]=useState(''); const [folder,setFolder]=useState('');
  const [offset,setOffset]=useState(0); const [folders,setFolders]=useState<FolderPage>();
  useEffect(()=>{let live=true; void api<Options>('/api/classification/options').then(v=>{if(live)setOptions(v);})
    .catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[]);
  useEffect(()=>{
    setFolders(undefined); if(!root)return;
    const controller=new AbortController();
    void api<FolderPage>('/api/classification/folders?'+new URLSearchParams({root_id:root,folder,offset:String(offset)}),
      {signal:controller.signal}).then(v=>{if(!controller.signal.aborted)setFolders(v);})
      .catch(e=>{if(!controller.signal.aborted)setError(String(e));});
    return()=>controller.abort();
  },[root,folder,offset,refresh]);
  const active=jobs.some(j=>['running','queued','paused'].includes(j.state));
  async function start(){
    setBusy(true);setError('');
    try{
      const parsed=count.trim()?Number(count):undefined;
      if(parsed!==undefined&&(!Number.isInteger(parsed)||parsed<1||parsed>100000))
        throw new Error('Image limit must be between 1 and 100,000');
      await api('/api/classification/jobs',POST({force,root_id:Number(root),folder,...(parsed?{limit:parsed}:{})}));
      setRefresh(v=>v+1);
    }catch(reason){setError(reason instanceof Error?reason.message:'Could not queue job');}
    finally{setBusy(false);}
  }
  async function change(id:string,action:'pause'|'resume'|'cancel'){
    setBusy(true);setError('');
    try{await api('/api/classification/jobs/'+encodeURIComponent(id)+'/action',POST({action}));
      setRefresh(v=>v+1);}
    catch(reason){setError(reason instanceof Error?reason.message:'Action failed');}
    finally{setBusy(false);}
  }
  return <section className="ai-workspace">
    <div className="ai-head"><div className="eyebrow">MILESTONE 04 · LOCAL AI</div>
      <h1>Understand your photos.</h1>
      <p>One analysis per unique image. Only resized cached previews are sent to your laptop's Ollama.</p></div>
    <div className="panel ai-settings">
      <div><small>OLLAMA MODEL</small><strong>{model}</strong></div>
      <div><small>LOCAL ENDPOINT</small><strong>{endpoint}</strong></div>
      <div><small>PROMPT VERSION</small><strong>{options?.prompt_version} · {options?.concurrency} concurrent requests</strong></div>
    </div>
    <div className="panel ai-create">
      <h2>Start classification</h2>
      <p>First scan your library in Overview. Existing results for the same model and prompt are skipped.</p>
      <div className="ai-folder-picker">
        <label>Library <select value={root} onChange={e=>{setFolders(undefined);setRoot(e.target.value);setFolder('');setOffset(0);}}>
          <option value="">Select a library</option>
          {options?.roots.map(r=><option key={r.id} value={r.id}>{r.path}</option>)}
        </select></label>
        {root&&<><p><strong>Selected folder: /{folder}</strong> (includes subfolders)</p>
          {folder&&<button onClick={()=>{setFolders(undefined);setFolder(folder.split('/').slice(0,-1).join('/'));setOffset(0);}}>Parent folder</button>}
          {!folders?<p>Loading folders...</p>:<>
            <p>{fmt(folders.images)} unique images; {fmt(folders.pending)} awaiting classification</p>
            <div className="ai-folders">{folders.children.map(child=><button key={child.folder}
              onClick={()=>{setFolders(undefined);setFolder(child.folder);setOffset(0);}}>{child.name} ({fmt(child.images)})</button>)}</div>
            {folders.total===0&&<small>No indexed subfolders here.</small>}
            <div>{offset>0&&<button onClick={()=>{setFolders(undefined);setOffset(v=>Math.max(0,v-100));}}>Previous folders</button>}
              {offset+100<folders.total&&<button onClick={()=>{setFolders(undefined);setOffset(v=>v+100);}}>Next folders</button>}</div>
          </>}
        </>}
        <p>Exact duplicates share one AI result, including copies in other folders.</p>
      </div>
      <div className="ai-controls">
        <label>Image limit (optional)
          <input type="number" min="1" max="100000" placeholder="All eligible images"
            value={count} onChange={event=>setCount(event.target.value)} /></label>
        <label className="ai-force"><input type="checkbox" checked={force}
          onChange={event=>setForce(event.target.checked)}/> Reprocess existing results</label>
        <button type="button" disabled={busy||active||!root||!folders||!(force?folders.images:folders.pending)} onClick={()=>void start()}>Start AI classification</button>
      </div>
      <small>AI suggestions are never automatic decisions. Originals remain read-only.</small>
    </div>
    {error&&<div className="ai-error" role="alert">{error}</div>}
    <div className="section-heading"><h2>Classification jobs</h2><span>{jobs.length} recent jobs</span></div>
    {jobs.length===0&&<div className="panel ai-empty">No classification jobs yet. Scan a library first, then start here.</div>}
    {jobs.map(job=><div className="panel ai-job" key={job.id}>
      <div className="ai-job-top"><strong>{job.state.toUpperCase()} · {fmt(job.completed)} / {fmt(job.total)} processed</strong>
        <span>{job.rate_per_minute} items/min · {job.eta_seconds==null?'ETA unavailable':Math.ceil(job.eta_seconds/60)+' min remaining'}</span></div>
      <p>Folder: {job.parameters.root_path || "All libraries"}{job.parameters.folder?" / "+job.parameters.folder:""} (includes subfolders)</p>
      <div className="ai-progress"><span style={{width:job.total?Math.min(100,job.completed/job.total*100)+'%':'100%'}}/></div>
      <div className="ai-job-bottom"><small>
        Success: {fmt(job.counts.done||0)} · Failed: {fmt(job.counts.failed_permanent||0)}
        {' '}· Retry pending: {fmt(job.counts.failed_retryable||0)}
      </small><div className="ai-buttons">
        {['running','queued'].includes(job.state)&&<button disabled={busy} onClick={()=>void change(job.id,'pause')}>Pause</button>}
        {['paused','failed'].includes(job.state)&&<button disabled={busy} onClick={()=>void change(job.id,'resume')}>Resume</button>}
        {['running','queued','paused','failed'].includes(job.state)&&<button disabled={busy}
          onClick={()=>{if(window.confirm('Cancel this classification job? Completed results will be kept.'))void change(job.id,'cancel');}}>Cancel</button>}
      </div></div>
      {job.pause_reason&&<p className="ai-error">Paused: {job.pause_reason}. Check laptop Ollama and choose Resume.</p>}
    </div>)}
    <div className="section-heading"><h2>Recent AI suggestions</h2><span>{results.length} most recent</span></div>
    {results.length===0&&<div className="panel ai-empty">Validated AI results will appear here as they finish.</div>}
    <div className="ai-results">{results.map(result=><div className="panel ai-result" key={result.id}>
      <div className="ai-image">{result.thumbnail_url?<img src={result.thumbnail_url} loading="lazy" alt=""/>:'Image'}</div>
      <div className="ai-result-info"><strong>{result.subcategory || result.category.replaceAll('_',' ')}</strong>
        <span>{result.category.replaceAll('_',' ')} · {Math.round(result.confidence*100)}% confidence</span>
        <p>{result.reason}</p>
        <small>{result.possibly_important?'Potentially important · ':''}{result.uncertain?'Needs review':'AI suggestion'}</small></div>
    </div>)}</div>
    <p className="ai-disclaimer">Classification is advisory. Review suggestions before choosing Keep or Clutter and approving a copy-only export.</p>
  </section>;
}
