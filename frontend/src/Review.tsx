import {useEffect,useRef,useState} from 'react';
import {VirtuosoGrid} from 'react-virtuoso';
import './review.css';

type Decision='undecided'|'keep'|'clutter'|'review_later';
type Category='personal_photo'|'screenshot'|'meme_graphic'|'document'|'other';
type Filters={
 category:string;review_status:string;uncertain:string;important:string;root_id:string;
 folder:string;search:string;date_from:string;date_to:string;duplicates_only:string;blurry:string;low_res:string
};
type Photo={
 content_hash:string;copy_count:number;sample_path:string;sample_capture_time:string|null;
 thumbnail_url:string|null;preview_url:string|null;decision:Decision;effective_category:Category|null;
 category_override:Category|null;ai_category:Category|null;confidence:number|null;reason:string|null;
 uncertain:number|null;possibly_important:number|null;importance_reason:string|null;
 camera_make:string|null;camera_model:string|null;width:number|null;height:number|null;
 size_bytes:number;low_resolution:number|null;blurry:boolean;model_tag:string|null
};
type Copy={id:number;root_path:string;relative_path:string;missing:number;root_enabled:number;size:number};
type Audit={id:number;created_at:string;before:{decision:Decision;category_override:Category|null};
 after:{decision:Decision;category_override:Category|null}};
type Detail=Photo&{copies:Copy[];history:Audit[]};
type Page={total:number;items:Photo[]};
type Summary={total:number;keep:number;clutter:number;review_later:number;undecided:number;classified:number};
type Root={id:number;path:string;enabled:number};
const categories:Category[]=['personal_photo','screenshot','meme_graphic','document','other'];
const initial:Filters={category:'',review_status:'',uncertain:'',important:'',root_id:'',
 folder:'',search:'',date_from:'',date_to:'',duplicates_only:'',blurry:'',low_res:''};
const empty:Summary={total:0,keep:0,clutter:0,review_later:0,undecided:0,classified:0};
const text=(v:string)=>v.replaceAll('_',' ').replace(/^\w/,s=>s.toUpperCase());
const number=(v:number)=>new Intl.NumberFormat().format(v);
const size=(v:number)=>v>=1048576?(v/1048576).toFixed(1)+' MB':v>=1024?(v/1024).toFixed(1)+' KB':v+' B';
function match(filters:Filters):Record<string,string|boolean|number>{
 const result:Record<string,string|boolean|number>={};
 for(const [key,value] of Object.entries(filters)){
  if(!value)continue;
  result[key]=['uncertain','important','duplicates_only','blurry','low_res'].includes(key)?
   value==='true':key==='root_id'?Number(value):value;
 }return result;
}
async function request<T>(url:string,options?:RequestInit):Promise<T>{
 const response=await fetch(url,options);
 if(!response.ok){
  const body=await response.json().catch(()=>({detail:'Request failed'})) as {detail?:unknown};
  throw new Error(typeof body.detail==='string'?body.detail:'Request failed ('+response.status+')');
 }
 return response.json() as Promise<T>;
}
const post=(value:object):RequestInit=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
const editable=(t:EventTarget|null)=>t instanceof HTMLElement &&
 (['INPUT','TEXTAREA','SELECT'].includes(t.tagName)||t.isContentEditable);

export default function Review(){
 const [filters,setFilters]=useState<Filters>(initial);
 const [searchDraft,setSearchDraft]=useState('');
 const [offset,setOffset]=useState(0);
 const [page,setPage]=useState<Page>({total:0,items:[]});
 const [stats,setStats]=useState<Summary>(empty);
 const [roots,setRoots]=useState<Root[]>([]);
 const [checked,setChecked]=useState<Set<string>>(new Set());
 const [allMatching,setAllMatching]=useState(false);
 const [selected,setSelected]=useState<string|null>(null);
 const [detail,setDetail]=useState<Detail|null>(null);
 const [category,setCategory]=useState('');
 const [error,setError]=useState('');
 const [notice,setNotice]=useState('');
 const [busy,setBusy]=useState(false);
 const [refresh,setRefresh]=useState(0);
 const lastIndex=useRef<number|null>(null);

 function change(key:keyof Filters,value:string){
  setFilters(old=>({...old,[key]:value}));setOffset(0);setChecked(new Set());
  setAllMatching(false);setSelected(null);lastIndex.current=null;
 }
 useEffect(()=>{const timer=setTimeout(()=>change('search',searchDraft),300);return()=>clearTimeout(timer);},[searchDraft]);
 useEffect(()=>{
  let alive=true;
  async function load(){
   const query=new URLSearchParams({offset:String(offset),limit:'120'});
   Object.entries(match(filters)).forEach(([key,value])=>query.set(key,String(value)));
   try{
    const [items,summary,libraries]=await Promise.all([
     request<Page>('/api/reviews?'+query),request<Summary>('/api/reviews/summary'),
     request<{roots:Root[]}>('/api/inventory')]);
    if(alive){setPage(items);setStats(summary);setRoots(libraries.roots);setError('');}
   }catch(exc){if(alive)setError(exc instanceof Error?exc.message:'Could not load review grid');}
  }
  void load();const timer=setInterval(()=>void load(),10000);
  return()=>{alive=false;clearInterval(timer);};
 },[filters,offset,refresh]);
 useEffect(()=>{
  if(!selected){setDetail(null);return;}
  let alive=true;
  setDetail(null);
  void request<Detail>('/api/reviews/'+selected).then(d=>{if(alive)setDetail(d);})
   .catch(exc=>{if(alive)setError(exc instanceof Error?exc.message:'Could not open image');});
  return()=>{alive=false;};
 },[selected,refresh]);

 function toggle(digest:string,index:number,shift:boolean){
  const next=new Set(checked);
  if(allMatching){setAllMatching(false);next.clear();}
  if(shift&&lastIndex.current!==null){
   const add=!next.has(digest);
   for(let i=Math.min(index,lastIndex.current);i<=Math.max(index,lastIndex.current);i++){
    const h=page.items[i]?.content_hash;if(h){if(add)next.add(h);else next.delete(h);}
   }
  }else{if(next.has(digest))next.delete(digest);else next.add(digest);}
  lastIndex.current=index;setChecked(next);
 }
 function move(direction:number){
  const index=page.items.findIndex(x=>x.content_hash===selected);
  const next=page.items[index+direction];
  if(next)setSelected(next.content_hash);
 }
 async function update(change:{decision?:Decision;category_override?:Category;clear_category?:boolean},one?:string){
  const hashes=one?[one]:[...checked];
  if(!one&&!allMatching&&!hashes.length){setError('Select at least one image.');return;}
  const affected=one?1:allMatching?page.total:hashes.length;
  if(!one&&!window.confirm('Apply changes to '+number(affected)+' selected images? Original files stay untouched.'))return;
  setBusy(true);setError('');setNotice('');
  try{
   const payload={...change,...(!one&&allMatching?{filters:match(filters)}:{hashes})};
   const result=await request<{matched:number;changed:number}>('/api/reviews/bulk',post(payload));
   setNotice(number(result.changed)+' review records updated ('+number(result.matched)+' matched).');
   if(!one){setAllMatching(false);setChecked(new Set());lastIndex.current=null;}
   setRefresh(v=>v+1);
  }catch(exc){setError(exc instanceof Error?exc.message:'Review update failed');}
  finally{setBusy(false);}
 }
 useEffect(()=>{
  if(!selected)return;
  function keyboard(event:KeyboardEvent){
   if(editable(event.target)||event.altKey||event.ctrlKey||event.metaKey||event.repeat)return;
   const key=event.key.toLowerCase();
   if(key==='escape'){event.preventDefault();setSelected(null);return;}
   if(key==='arrowleft'||key==='arrowright'){event.preventDefault();move(key==='arrowright'?1:-1);return;}
   const decision:Record<string,Decision>={k:'keep',c:'clutter',l:'review_later',u:'undecided'};
   if(!busy&&decision[key]){event.preventDefault();void update({decision:decision[key]},selected);}
  }
  window.addEventListener('keydown',keyboard);return()=>window.removeEventListener('keydown',keyboard);
 },[selected,page.items,busy,checked,allMatching,filters]);
 const count=allMatching?page.total:checked.size;
 const dropdown=(label:string,key:keyof Filters,options:[string,string][])=>(
  <label key={key}>{label}<select value={filters[key]} onChange={e=>change(key,e.target.value)}>
   {options.map(([value,name])=><option value={value} key={value}>{name}</option>)}</select></label>);
 const binary=(key:keyof Filters,caption:string)=>dropdown(caption,key,
  [['','Any'],['true','Yes'],['false','No']]);
 return <section className="review-space" aria-label="Human photo review">
  <div className="eyebrow">MILESTONE 05 · HUMAN REVIEW</div>
  <h1>Review your memories.</h1>
  <p>AI suggestions are not decisions. You decide, and all identical copies share the same review.</p>
  <div className="review-counters">{(['total','undecided','keep','clutter','review_later','classified'] as const)
   .map(key=><div className="panel" key={key}><small>{text(key)}</small><strong>{number(stats[key])}</strong></div>)}</div>
  <div className="panel review-toolbar">
   <div className="review-filters">
    <label>Find filename or folder<input value={searchDraft} placeholder="Search paths" onChange={e=>setSearchDraft(e.target.value)}/></label>
    {dropdown('Category','category',[['','All categories'],['unclassified','Unclassified'],...categories.map(c=>[c,text(c)] as [string,string])])}
    {dropdown('Decision','review_status',[['','All'],['undecided','Undecided'],['keep','Keep'],['clutter','Clutter'],['review_later','Review later']])}
    {dropdown('AI confidence','uncertain',[['','Any'],['true','Uncertain'],['false','Confident']])}
    {dropdown('Importance','important',[['','Any'],['true','Possibly important'],['false','Not flagged']])}
    {dropdown('Library','root_id',[['','All libraries'],...roots.map(r=>[String(r.id),r.path] as [string,string])])}
    <label>Folder contains<input value={filters.folder} onChange={e=>change('folder',e.target.value)}/></label>
    <label>From<input type="date" value={filters.date_from} onChange={e=>change('date_from',e.target.value)}/></label>
    <label>To<input type="date" value={filters.date_to} onChange={e=>change('date_to',e.target.value)}/></label>
    {binary('duplicates_only','Exact duplicates')}
    {binary('blurry','Possibly blurry')}
    {binary('low_res','Low resolution')}
   </div>
   <div className="review-select">
    <span>{number(page.total)} matching images</span>
    <button onClick={()=>{setChecked(new Set(page.items.map(p=>p.content_hash)));setAllMatching(false);}}>Select page</button>
    <button onClick={()=>{setChecked(new Set());setAllMatching(true);}}>Select all matching ({number(page.total)})</button>
    <button onClick={()=>{setChecked(new Set());setAllMatching(false);}}>Clear selection</button>
    <button onClick={()=>{setFilters(initial);setSearchDraft('');setOffset(0);setAllMatching(false);setChecked(new Set());}}>Reset filters</button>
   </div>
  </div>
  {count>0&&<div className="panel review-bulk"><strong>{number(count)} selected</strong>
   <div className="review-actions">
    <button disabled={busy} onClick={()=>void update({decision:'keep'})}>Keep</button>
    <button disabled={busy} onClick={()=>void update({decision:'clutter'})}>Clutter</button>
    <button disabled={busy} onClick={()=>void update({decision:'review_later'})}>Review later</button>
    <button disabled={busy} onClick={()=>void update({decision:'undecided'})}>Undecided</button>
    <select aria-label="Correct category" value={category} onChange={e=>setCategory(e.target.value)}>
     <option value="">Correct category…</option>{categories.map(c=><option value={c} key={c}>{text(c)}</option>)}
     <option value="__clear__">Use AI category</option>
    </select>
    <button disabled={busy||!category} onClick={()=>void update(category==='__clear__'?{clear_category:true}:{category_override:category as Category})}>Apply category</button>
   </div></div>}
  {error&&<p className="review-error" role="alert">{error}</p>}
  {notice&&<p className="review-notice" role="status">{notice}</p>}
  {page.total===0?<div className="panel review-empty">No photos match. Scan your library or change filters.</div>:
   <VirtuosoGrid style={{height:'68vh',minHeight:320}} className="review-scroller"
    data={page.items} listClassName="review-grid" itemClassName="review-grid-item"
    itemContent={(index,item)=><article className={'panel review-tile '+(checked.has(item.content_hash)||allMatching?'checked':'')} key={item.content_hash}>
     <button className="review-open" onClick={()=>setSelected(item.content_hash)}>
      <div className="review-thumbnail">{item.thumbnail_url?<img src={item.thumbnail_url} alt="" loading="lazy"/>:'No preview'}
       {item.copy_count>1&&<span className="review-copy-count">{item.copy_count} copies</span>}</div>
      <div className="review-tile-info"><strong>{item.sample_path.split('/').pop()}</strong>
       <small>{text(item.effective_category||'unclassified')} · {item.confidence===null?'No AI result':Math.round(item.confidence*100)+'%'}</small>
       <span className={'review-decision review-'+item.decision}>{text(item.decision)}</span></div>
     </button>
     <button className="review-check" aria-label={'Select '+item.sample_path}
      aria-pressed={checked.has(item.content_hash)||allMatching} onClick={e=>toggle(item.content_hash,index,e.shiftKey)}>
      {checked.has(item.content_hash)||allMatching?'✓':'+'}</button>
    </article>}/>}
  <div className="review-pager"><button disabled={offset===0} onClick={()=>{setOffset(Math.max(0,offset-120));setChecked(new Set());setAllMatching(false);}}>Previous</button>
   <span>{page.total?number(offset+1)+'–'+number(Math.min(offset+120,page.total))+' of '+number(page.total):'0 images'}</span>
   <button disabled={offset+120>=page.total} onClick={()=>{setOffset(offset+120);setChecked(new Set());setAllMatching(false);}}>Next</button></div>
  {selected&&<div className="review-shade" onClick={()=>setSelected(null)}>
   <div className="review-dialog" role="dialog" aria-modal="true" aria-label="Review image details" onClick={e=>e.stopPropagation()}>
    <div className="review-dialog-head"><h2>Review image</h2><button aria-label="Close details" onClick={()=>setSelected(null)}>×</button></div>
    {!detail?<p>Loading image details…</p>:<>
     <div className="review-preview">{detail.preview_url?<img src={detail.preview_url} alt={detail.sample_path}/>:'Preview unavailable'}</div>
     <div className="review-navigation"><button onClick={()=>move(-1)}>← Previous</button>
      <strong title={detail.sample_path}>{detail.sample_path.split('/').pop()}</strong>
      <button onClick={()=>move(1)}>Next →</button></div>
     <div className="review-detail-actions">
      {([['keep','K · Keep'],['clutter','C · Clutter'],['review_later','L · Later'],['undecided','U · Reset']] as [Decision,string][])
       .map(([value,label])=><button key={value} disabled={busy} onClick={()=>void update({decision:value},detail.content_hash)}>{label}</button>)}
     </div>
     <label className="review-correct">Correct category<select disabled={busy}
      value={detail.category_override||'__ai__'} onChange={e=>void update(e.target.value==='__ai__'?
       {clear_category:true}:{category_override:e.target.value as Category},detail.content_hash)}>
      <option value="__ai__">Use AI suggestion ({text(detail.ai_category||'unclassified')})</option>
      {categories.map(c=><option key={c} value={c}>{text(c)}</option>)}</select></label>
     <div className="review-metadata">
      <p><b>Decision:</b> {text(detail.decision)} · <b>Category:</b> {text(detail.effective_category||'unclassified')}</p>
      <p><b>AI:</b> {detail.confidence===null?'Not classified':Math.round(detail.confidence*100)+'% confidence'}
       {detail.uncertain?' · Uncertain':''} · {detail.possibly_important?'Possibly important':'Not flagged'}</p>
      <p><b>Explanation:</b> {detail.reason||'No AI explanation'} {detail.importance_reason||''}</p>
      <p><b>Captured:</b> {detail.sample_capture_time||'Unknown'} · <b>Size:</b> {size(detail.size_bytes)}</p>
      <p><b>Camera:</b> {[detail.camera_make,detail.camera_model].filter(Boolean).join(' ')||'Unknown'}
       {' '}· <b>Resolution:</b> {detail.width??'?'} × {detail.height??'?'}</p>
      <p><b>Flags:</b> {detail.blurry?'Possibly blurry · ':''}{detail.low_resolution?'Low resolution':'No low-resolution flag'}</p>
      <p><b>Model:</b> {detail.model_tag||'Not classified'}</p>
     </div>
     <h3>File locations (up to 300)</h3>
     <div className="review-locations">{detail.copies.map(c=><div key={c.id}>
      <small>{c.missing?'Missing':!c.root_enabled?'Disabled library':'Present at last scan'}</small>
      <code>{c.root_path}/{c.relative_path}</code></div>)}</div>
     <h3>Review history (latest 40)</h3>
     <div className="review-history">{detail.history.length===0?<p>No manual changes yet.</p>:
      detail.history.map(entry=><p key={entry.id}>{entry.created_at} · {text(entry.before.decision)}
       {' → '}{text(entry.after.decision)}
       {entry.before.category_override!==entry.after.category_override?' · Category updated':''}</p>)}</div>
     <small className="review-help">Keyboard: K Keep · C Clutter · L Later · U Reset · ←/→ Navigate · Esc Close</small>
    </>}
   </div></div>}
  <p className="review-safety">Only PhotoSift review records change. Original photographs are never edited or deleted. Export is disabled until M6.</p>
 </section>;
}
