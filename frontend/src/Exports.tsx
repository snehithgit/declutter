import {useEffect,useState} from 'react';
import './exports.css';

type Status={configured:boolean;destination:string|null;message:string};
type Run={id:string;state:string;destination:string;files:number;total_bytes:number;copied:number|null;failed:number|null;created_at:string};
type Operation={id:number;source:string;destination:string;expected_size:number;category:string;decision:string;date_source:string;state:string;error:string|null};
type Detail=Run&{operations:Operation[];limit:number;offset:number};
const count=(n:number)=>new Intl.NumberFormat().format(n);
const bytes=(n:number)=>n>=1073741824?(n/1073741824).toFixed(2)+' GB':n>=1048576?(n/1048576).toFixed(1)+' MB':n+' B';
async function api<T>(url:string,data?:object):Promise<T>{
 const response=await fetch(url,data===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
 if(!response.ok){const body=await response.json().catch(()=>({detail:'Request failed'})) as {detail?:string};
  throw new Error(body.detail||'Request failed');}
 return response.json() as Promise<T>;
}
export default function Exports(){
 const [status,setStatus]=useState<Status>({configured:false,destination:null,message:'Checking destination…'});
 const [runs,setRuns]=useState<Run[]>([]);
 const [selected,setSelected]=useState<string|null>(null);
 const [detail,setDetail]=useState<Detail|null>(null);
 const [offset,setOffset]=useState(0);
 const [includeClutter,setIncludeClutter]=useState(false);
 const [confirmation,setConfirmation]=useState('');
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');
 const [notice,setNotice]=useState('');
 const [revision,setRevision]=useState(0);
 useEffect(()=>{let live=true;async function load(){
  try{const [s,r]=await Promise.all([api<Status>('/api/exports/status'),api<Run[]>('/api/exports')]);
   if(live){setStatus(s);setRuns(r);}}
  catch(e){if(live)setError(e instanceof Error?e.message:'Failed to load exports');}}
  void load();const interval=setInterval(()=>void load(),4000);
  return()=>{live=false;clearInterval(interval);};
 },[revision]);
 useEffect(()=>{if(!selected){setDetail(null);return;}let live=true;
  void api<Detail>('/api/exports/'+selected+'?limit=50&offset='+offset)
   .then(r=>{if(live)setDetail(r);}).catch(e=>{if(live)setError(e instanceof Error?e.message:'Cannot load plan');});
  const interval=setInterval(()=>{void api<Detail>('/api/exports/'+selected+'?limit=50&offset='+offset)
   .then(r=>{if(live)setDetail(r);}).catch(()=>{});},4000);
  return()=>{live=false;clearInterval(interval);};
 },[selected,offset,revision]);
 async function plan(){
  setBusy(true);setError('');setNotice('');
  try{const result=await api<{id:string}>('/api/exports/plans',{include_clutter:includeClutter});
   setSelected(result.id);setOffset(0);setConfirmation('');setRevision(x=>x+1);
   setNotice('Plan saved. Review source and destination paths before approving.');}
  catch(e){setError(e instanceof Error?e.message:'Could not create plan');}finally{setBusy(false);}
 }
 async function action(name:string,phrase:string){
  if(!selected)return;
  setBusy(true);setError('');setNotice('');
  try{await api('/api/exports/'+selected+'/action',{action:name,confirmation:phrase});
   setConfirmation('');setRevision(x=>x+1);setNotice('Action accepted. The worker will update the journal.');}
  catch(e){setError(e instanceof Error?e.message:'Action failed');}finally{setBusy(false);}
 }
 const phrase=detail?.state==='planned'?'COPY APPROVED':
  detail?.state==='done'||detail?.state==='done_with_errors'?'ROLLBACK COPIES':'';
 return <section className="export-workspace">
  <div className="eyebrow">MILESTONE 06 · COPY-ONLY EXPORT</div><h1>Organize safely.</h1>
  <p>Preview and explicitly approve every copy. PhotoSift never moves or deletes your original photos.</p>
  <div className="panel export-config"><strong>{status.configured?'Destination ready':'Exporting is disabled'}</strong>
   <p>{status.message}</p>{status.destination&&<code>{status.destination}</code>}
   {!status.configured&&<p>Mount a separate writable export destination using the optional M6 Compose override.</p>}
  </div>
  <div className="panel export-create"><h2>Create an export plan</h2>
   <p>Reviewed Keep and Review Later photos only, one copy per unique SHA-256 image.</p>
   <label><input type="checkbox" checked={includeClutter} onChange={e=>setIncludeClutter(e.target.checked)}/>
    Include reviewed Clutter in its own folder</label>
   <button disabled={!status.configured||busy} onClick={()=>void plan()}>Generate plan — do not copy yet</button>
  </div>
  {error&&<p className="export-error" role="alert">{error}</p>}
  {notice&&<p className="export-notice" role="status">{notice}</p>}
  <div className="section-heading"><h2>Saved export runs</h2><span>{runs.length} recent</span></div>
  <div className="export-runs">{runs.map(r=><button key={r.id} className={'panel export-run '+(selected===r.id?'selected':'')}
    onClick={()=>{setSelected(r.id);setOffset(0);setConfirmation('');}}>
    <strong>{r.state.replaceAll('_',' ')}</strong><span>{count(r.files)} files · {bytes(r.total_bytes)}</span>
    <small>{r.created_at} · {count(r.copied||0)} copied · {count(r.failed||0)} failed</small>
  </button>)}</div>
  {runs.length===0&&<div className="panel export-empty">No export plans. Review photos first, then generate a plan.</div>}
  {selected&&<div className="panel export-detail">
   <div className="export-heading"><h2>Saved plan &amp; journal</h2><button onClick={()=>{setSelected(null);setDetail(null);}}>Close</button></div>
   {!detail?<p>Loading plan…</p>:<>
    <p><b>State:</b> {detail.state.replaceAll('_',' ')} · {count(detail.files)} files · {bytes(detail.total_bytes)}</p>
    <p><b>Destination:</b></p><code>{detail.destination}</code>
    <p>Every source and destination below is frozen in this plan. A changed source file fails verification instead of being copied.</p>
    <div className="export-operations">{detail.operations.map(op=><div key={op.id} className="export-operation">
     <strong>{op.state.replaceAll('_',' ')}</strong>
     <span>{op.category.replaceAll('_',' ')} · {op.decision.replaceAll('_',' ')} · {bytes(op.expected_size)} · Date: {op.date_source||'mtime (unreliable)'}</span>
     <small>SOURCE</small><code>{op.source}</code><small>DESTINATION</small><code>{op.destination}</code>
     {op.error&&<p className="export-error">{op.error}</p>}
    </div>)}</div>
    <div className="export-pager"><button disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>Previous</button>
     <span>{detail.files?(offset+1)+'–'+Math.min(offset+50,detail.files)+' of '+detail.files:'No files'}</span>
     <button disabled={offset+50>=detail.files} onClick={()=>setOffset(offset+50)}>Next</button></div>
    {phrase&&<div className="export-approval">
     <p>{detail.state==='planned'?'Inspect this plan before copying.':'Rollback removes only verified outputs created by this export.'}</p>
     <label>Type <b>{phrase}</b> to confirm<input value={confirmation} onChange={e=>setConfirmation(e.target.value)}
      placeholder={phrase}/></label>
     <button disabled={busy||confirmation!==phrase} onClick={()=>void action(
      detail.state==='planned'?'approve':detail.state==='done'?'rollback':'rollback_errors',phrase)}>
      {detail.state==='planned'?'Approve and copy':'Rollback verified output copies'}</button>
    </div>}
    {detail.state==='planned'&&<button disabled={busy} onClick={()=>void action('cancel','CANCEL EXPORT')}>Discard plan</button>}
    {detail.state==='done_with_errors'&&<button disabled={busy} onClick={()=>{if(window.confirm('Retry failed files only?'))void action('retry','RETRY FAILED');}}>Retry failed files</button>}
    {['done','done_with_errors','rolled_back','rollback_partial'].includes(detail.state)&&
     <p>Export folder contains manifest.csv and manifest.json with source and destination records.</p>}
   </>}
  </div>}
  <p className="export-safety">Source libraries stay read-only. Rollback affects only output files that this run created and verified.</p>
 </section>;
}
