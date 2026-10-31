import {useEffect,useRef,useState} from 'react';
import {VirtuosoGrid} from 'react-virtuoso';
import './review.css';
import {createLatestGate,fetchJson,makeScope,sameScope} from './state.mjs';
import type {Scope} from './state.mjs';

type Decision='undecided'|'keep'|'clutter'|'review_later';
type Category='personal_photo'|'screenshot'|'meme_graphic'|'document'|'other';
type Filters={
 bucket:string;category:string;review_status:string;uncertain:string;important:string;root_id:string;
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
type Detail=Photo&{copies:Copy[];copy_total:number;history:Audit[]};
type Page={total:number;items:Photo[]};
type Summary={suggested_personal:number;suggested_documents:number;suggested_food:number;suggested_memes:number;suggested_screenshots:number;suggested_other:number;suggested_unclassified:number;total:number;keep:number;clutter:number;review_later:number;undecided:number;classified:number};
type Root={id:number;path:string;enabled:number};
const categories:Category[]=['personal_photo','screenshot','meme_graphic','document','other'];
const initial:Filters={bucket:'',category:'',review_status:'',uncertain:'',important:'',root_id:'',
 folder:'',search:'',date_from:'',date_to:'',duplicates_only:'',blurry:'',low_res:''};
const empty:Summary={suggested_personal:0,suggested_documents:0,suggested_food:0,suggested_memes:0,suggested_screenshots:0,suggested_other:0,suggested_unclassified:0,total:0,keep:0,clutter:0,review_later:0,undecided:0,classified:0};
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
async function request<T>(url:string,init?:RequestInit):Promise<T>{return fetchJson<T>(url,init);}
const post=(value:object):RequestInit=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
const editable=(t:EventTarget|null)=>t instanceof HTMLElement &&
 (['INPUT','TEXTAREA','SELECT'].includes(t.tagName)||t.isContentEditable);

export default function Review({detailHash,onDetailChange}:{detailHash:string|null;onDetailChange:(hash:string|null)=>void}){
 const selected=detailHash;
 const [filters,setFilters]=useState<Filters>(initial);
 const [searchDraft,setSearchDraft]=useState('');
 const [offset,setOffset]=useState(0);
 const [page,setPage]=useState<Page>({total:0,items:[]});
 const [stats,setStats]=useState<Summary>(empty);
 const [roots,setRoots]=useState<Root[]>([]);
 const [checked,setChecked]=useState<Set<string>>(new Set());
 const [allScope,setAllScope]=useState<Scope|null>(null);
 const [scopeBusy,setScopeBusy]=useState(false);

 const [detail,setDetail]=useState<Detail|null>(null);
 const [moreCopies,setMoreCopies]=useState<Copy[]>([]);
 const [copiesBusy,setCopiesBusy]=useState(false);
 const [category,setCategory]=useState('');
 const [error,setError]=useState('');
 const [notice,setNotice]=useState('');
 const [busy,setBusy]=useState(false);
 const [refresh,setRefresh]=useState(0);
 const lastIndex=useRef<number|null>(null);
 const filtersRef=useRef(filters);
 const selectedRef=useRef(selected);selectedRef.current=selected;
 const scopeRef=useRef(allScope);scopeRef.current=allScope;
 const selectionGate=useRef(createLatestGate());
 useEffect(()=>()=>selectionGate.current.cancel(),[]);

 function openBucket(bucket:string){
  const next={...initial,bucket,review_status:'undecided'};
  filtersRef.current=next;selectionGate.current.cancel();setScopeBusy(false);
  setFilters(next);setSearchDraft('');setOffset(0);setChecked(new Set());setAllScope(null);lastIndex.current=null;
  setNotice('Showing undecided images in this group across all libraries. Select all matching to act on the entire group.');
 }
 function change(key:keyof Filters,value:string){
  const next={...filtersRef.current,[key]:value};filtersRef.current=next;
  selectionGate.current.cancel();setScopeBusy(false);setFilters(next);setOffset(0);setChecked(new Set());setAllScope(null);lastIndex.current=null;
 }
 useEffect(()=>{
  if(searchDraft===filters.search)return;
  const timer=setTimeout(()=>change('search',searchDraft),300);
  return()=>clearTimeout(timer);
 },[searchDraft,filters.search]);
 useEffect(()=>{
  const gate=createLatestGate();
  async function load(){
   const ticket=gate.next();
   const query=new URLSearchParams({offset:String(offset),limit:'120'});
   Object.entries(match(filters)).forEach(([key,value])=>query.set(key,String(value)));
   try{
    const [items,summary,libraries]=await Promise.all([
     request<Page>('/api/reviews?'+query,{signal:ticket.signal}),
     request<Summary>('/api/reviews/summary',{signal:ticket.signal}),
     request<{roots:Root[]}>('/api/inventory',{signal:ticket.signal})]);
    if(ticket.isCurrent()){
     setPage(items);setStats(summary);setRoots(libraries.roots);setError('');
     if(scopeRef.current && scopeRef.current.total!==items.total){
      setAllScope(null);setNotice('Matching result count changed. Select all matching again.');
     }
    }
   }catch(e){if(ticket.isCurrent())setError(e instanceof Error?e.message:'Cannot load review grid');}
  }
  void load();const timer=setInterval(()=>void load(),10000);
  return()=>{clearInterval(timer);gate.cancel();};
 },[filters,offset,refresh]);
 useEffect(()=>{
  const gate=createLatestGate();setMoreCopies([]);setCopiesBusy(false);
  if(!selected){setDetail(null);return()=>gate.cancel();}
  setDetail(previous=>previous?.content_hash===selected?previous:null);
  async function load(){
   const ticket=gate.next();
   try{
    const next=await request<Detail>('/api/reviews/'+selected,{signal:ticket.signal});
    if(ticket.isCurrent())setDetail(next);
   }catch(e){if(ticket.isCurrent())setError(e instanceof Error?e.message:'Cannot load image detail');}
  }
  void load();return()=>gate.cancel();
 },[selected,refresh]);

 async function loadMoreCopies(){
  if(!detail||copiesBusy)return;
  setCopiesBusy(true);
  try{
   const next=await request<{items:Copy[]}>('/api/reviews/'+detail.content_hash+
    '/copies?limit=100&offset='+(detail.copies.length+moreCopies.length));
   if(selectedRef.current===detail.content_hash)setMoreCopies(previous=>[...previous,...next.items]);
  }catch(exc){if(detail && selectedRef.current===detail.content_hash)
   setError(exc instanceof Error?exc.message:'Cannot load more file locations');}
  finally{if(detail && selectedRef.current===detail.content_hash)setCopiesBusy(false);}
 }
 function toggle(digest:string,index:number,shift:boolean){
  const next=new Set(checked);
  if(allScope){setAllScope(null);next.clear();}
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
  if(next)onDetailChange(next.content_hash);
 }
 async function selectAllMatching(){
  setScopeBusy(true);setError('');
  const selectedFilters=match(filtersRef.current);
  const snapshotKey=JSON.stringify(selectedFilters);
  const ticket=selectionGate.current.next();
  const query=new URLSearchParams();
  Object.entries(selectedFilters).forEach(([key,value])=>query.set(key,String(value)));
  try{
   const result=await request<{total:number;digest:string}>('/api/reviews/selection?'+query,{signal:ticket.signal});
   if(!ticket.isCurrent()||JSON.stringify(match(filtersRef.current))!==snapshotKey)return;
   setChecked(new Set());lastIndex.current=null;
   if(!result.total){setAllScope(null);setNotice('No matching images.');return;}
   setAllScope(makeScope(selectedFilters,result.total,result.digest));
   setNotice('Frozen selection of '+number(result.total)+' matching images. Changing filters clears it.');
  }catch(e){if(ticket.isCurrent())setError(e instanceof Error?e.message:'Could not freeze selection');}
  finally{if(ticket.isCurrent())setScopeBusy(false);}
 }
 async function update(change:{decision?:Decision;category_override?:Category;clear_category?:boolean},one?:string){
  const scope=allScope,hashes=one?[one]:[...checked];
  if(!one&&searchDraft!==filtersRef.current.search){
   setAllScope(null);setChecked(new Set());setError('Search is changing. Select images again.');return;
  }
  if(!one&&!scope&&!hashes.length){setError('Select at least one image.');return;}
  if(!one&&scope&&!sameScope(scope,match(filtersRef.current))){
   setAllScope(null);setError('Filters changed. Select all matching again.');return;
  }
  const total=one?1:scope?scope.total:hashes.length;
  if(!one&&!window.confirm('Apply changes to '+number(total)+' '+
      (scope?'frozen matching':'selected')+' images? Originals remain untouched.'))return;
  setBusy(true);setError('');setNotice('');
  try{
   const payload={...change,...(!one&&scope?
     {filters:scope.filters,expected_scope_digest:scope.digest}:{hashes})};
   const result=await request<{matched:number;changed:number}>('/api/reviews/bulk',post(payload));
   setNotice(number(result.changed)+' review records updated ('+number(result.matched)+' matched).');
   if(!one){setAllScope(null);setChecked(new Set());lastIndex.current=null;}
   setRefresh(v=>v+1);
  }catch(e){setError(e instanceof Error?e.message:'Review update failed');if(scope)setAllScope(null);}
  finally{setBusy(false);}
 }
 useEffect(()=>{
  if(!selected)return;
  function keyboard(event:KeyboardEvent){
   if(editable(event.target)||event.altKey||event.ctrlKey||event.metaKey||event.repeat)return;
   const key=event.key.toLowerCase();
   if(key==='escape'){event.preventDefault();onDetailChange(null);return;}
   if(key==='arrowleft'||key==='arrowright'){event.preventDefault();move(key==='arrowright'?1:-1);return;}
   const decision:Record<string,Decision>={k:'keep',c:'clutter',l:'review_later',u:'undecided'};
   if(!busy&&decision[key]){event.preventDefault();void update({decision:decision[key]},selected!);}
  }
  window.addEventListener('keydown',keyboard);return()=>window.removeEventListener('keydown',keyboard);
 },[selected,page.items,busy,checked,allScope,filters]);
 const count=allScope?allScope.total:checked.size;
 const pendingSearch=searchDraft!==filters.search;
 const visibleDetail=detail?.content_hash===selected?detail:null;
 const dropdown=(label:string,key:keyof Filters,options:[string,string][])=>(
  <label key={key}>{label}<select value={filters[key]} onChange={e=>change(key,e.target.value)}>
   {options.map(([value,name])=><option value={value} key={value}>{name}</option>)}</select></label>);
 const binary=(key:keyof Filters,caption:string)=>dropdown(caption,key,
  [['','Any'],['true','Yes'],['false','No']]);
 return <section className="review-space" aria-label="Human photo review">
  <div className="eyebrow">MILESTONE 05 · HUMAN REVIEW</div>
  <h1>Review your memories.</h1>
  <p>AI automatically groups your library. Inspect a sample, select all matching, then Keep or Clutter the whole group.</p>
  <div className="review-buckets" aria-label="Automatic sorting groups">
   {([['personal','Personal',stats.suggested_personal,'People, family, portraits and other personal photos.'],
      ['documents','Documents',stats.suggested_documents,'Paperwork, receipts, tables and records.'],
      ['food','Food',stats.suggested_food,'Food images without detected people; family meals stay Personal.'],
      ['memes','Memes / graphics',stats.suggested_memes,'Memes, greetings and graphics.'],
      ['screenshots','Screenshots',stats.suggested_screenshots,'Screen captures, which may include useful information.'],
      ['other','Other',stats.suggested_other,'Objects and images outside these groups.'],
      ['unclassified','Unclassified',stats.suggested_unclassified,'Images still awaiting AI classification.']] as const).map(([key,title,total,description])=>
    <button className={'panel review-bucket '+(filters.bucket===key?'active':'')} key={key}
      aria-pressed={filters.bucket===key} onClick={()=>openBucket(key)}>
     <strong>{title}</strong><b>{number(total||0)}</b><small>{description}</small>
    </button>)}
  </div>
  <p className="review-group-help">Counts show undecided images across all libraries. Grouping uses existing results automatically, including low-confidence results. A group is a content type, not a junk verdict; use confidence and importance filters before bulk decisions.
   Bulk decisions affect all matching images, including other pages. Clutter stays separate in the app;
   approved copy exports can place it in a Clutter folder. Originals stay untouched.</p>
  <div className="review-counters">{(['total','undecided','keep','clutter','review_later','classified'] as const)
   .map(key=><div className="panel" key={key}><small>{text(key)}</small><strong>{number(stats[key])}</strong></div>)}</div>
  <div className="panel review-toolbar">
   <div className="review-filters">
    <label>Find filename or folder<input value={searchDraft} placeholder="Search paths" onChange={e=>{
    setSearchDraft(e.target.value);selectionGate.current.cancel();setScopeBusy(false);
    setAllScope(null);setChecked(new Set());
   }}/></label>
    {dropdown('AI group','bucket',[['','All groups'],['personal','Personal'],['documents','Documents'],['food','Food'],['memes','Memes / graphics'],['screenshots','Screenshots'],['other','Other'],['unclassified','Unclassified']])}
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
    <button disabled={busy||scopeBusy||pendingSearch||!page.items.length} onClick={()=>{setChecked(new Set(page.items.map(p=>p.content_hash)));setAllScope(null);}}>Select page</button>
    <button disabled={busy||scopeBusy||pendingSearch||!page.total} onClick={()=>void selectAllMatching()}>{scopeBusy?'Selecting…':'Select all matching ('+number(page.total)+')'}</button>
    <button onClick={()=>{selectionGate.current.cancel();setScopeBusy(false);setChecked(new Set());setAllScope(null);}}>Clear selection</button>
    <button onClick={()=>{filtersRef.current=initial;selectionGate.current.cancel();setScopeBusy(false);setFilters(initial);setSearchDraft('');setOffset(0);setAllScope(null);setChecked(new Set());}}>Reset filters</button>
    <a className="review-export" href="/api/reviews/audit.csv" download>Export corrections CSV</a>
   </div>
  </div>
  {count>0&&<div className="panel review-bulk"><strong>{allScope?'All matching '+number(count)+' · frozen scope':number(count)+' from current page selected'}</strong>
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
    itemContent={(index,item)=><article className={'panel review-tile '+(checked.has(item.content_hash)||Boolean(allScope)?'checked':'')} key={item.content_hash}>
     <button className="review-open" onClick={()=>onDetailChange(item.content_hash)}>
      <div className="review-thumbnail">{item.thumbnail_url?<img src={item.thumbnail_url} alt="" loading="lazy"/>:'No preview'}
       {item.copy_count>1&&<span className="review-copy-count">{item.copy_count} copies</span>}</div>
      <div className="review-tile-info"><strong>{item.sample_path.split('/').pop()}</strong>
       <small>{text(item.effective_category||'unclassified')} · {item.confidence===null?'No AI result':Math.round(item.confidence*100)+'%'}</small>
       <span className={'review-decision review-'+item.decision}>{text(item.decision)}</span></div>
     </button>
     <button className="review-check" aria-label={'Select '+item.sample_path}
      aria-pressed={checked.has(item.content_hash)||Boolean(allScope)} onClick={e=>toggle(item.content_hash,index,e.shiftKey)}>
      {checked.has(item.content_hash)||Boolean(allScope)?'✓':'+'}</button>
    </article>}/>}
  <div className="review-pager"><button disabled={offset===0} onClick={()=>{setOffset(Math.max(0,offset-120));setChecked(new Set());setAllScope(null);}}>Previous</button>
   <span>{page.total?number(offset+1)+'–'+number(Math.min(offset+120,page.total))+' of '+number(page.total):'0 images'}</span>
   <button disabled={offset+120>=page.total} onClick={()=>{setOffset(offset+120);setChecked(new Set());setAllScope(null);}}>Next</button></div>
  {selected&&<div className="review-shade" onClick={()=>onDetailChange(null)}>
   <div className="review-dialog" role="dialog" aria-modal="true" aria-label="Review image details" onClick={e=>e.stopPropagation()}>
    <div className="review-dialog-head"><h2>Review image</h2><button aria-label="Close details" onClick={()=>onDetailChange(null)}>×</button></div>
    {!visibleDetail?<p>Loading image details…</p>:<>
     <div className="review-preview">{visibleDetail.preview_url?<img src={visibleDetail.preview_url} alt={visibleDetail.sample_path}/>:'Preview unavailable'}</div>
     <div className="review-navigation"><button onClick={()=>move(-1)}>← Previous</button>
      <strong title={visibleDetail.sample_path}>{visibleDetail.sample_path.split('/').pop()}</strong>
      <button onClick={()=>move(1)}>Next →</button></div>
     <div className="review-detail-actions">
      {([['keep','K · Keep'],['clutter','C · Clutter'],['review_later','L · Later'],['undecided','U · Reset']] as [Decision,string][])
       .map(([value,label])=><button key={value} disabled={busy} onClick={()=>void update({decision:value},visibleDetail.content_hash)}>{label}</button>)}
     </div>
     <label className="review-correct">Correct category<select disabled={busy}
      value={visibleDetail.category_override||'__ai__'} onChange={e=>void update(e.target.value==='__ai__'?
       {clear_category:true}:{category_override:e.target.value as Category},visibleDetail.content_hash)}>
      <option value="__ai__">Use AI suggestion ({text(visibleDetail.ai_category||'unclassified')})</option>
      {categories.map(c=><option key={c} value={c}>{text(c)}</option>)}</select></label>
     <div className="review-metadata">
      <p><b>Decision:</b> {text(visibleDetail.decision)} · <b>Category:</b> {text(visibleDetail.effective_category||'unclassified')}</p>
      <p><b>AI:</b> {visibleDetail.confidence===null?'Not classified':Math.round(visibleDetail.confidence*100)+'% confidence'}
       {visibleDetail.uncertain?' · Uncertain':''} · {visibleDetail.possibly_important?'Possibly important':'Not flagged'}</p>
      <p><b>Explanation:</b> {visibleDetail.reason||'No AI explanation'} {visibleDetail.importance_reason||''}</p>
      <p><b>Captured:</b> {visibleDetail.sample_capture_time||'Unknown'} · <b>Size:</b> {size(visibleDetail.size_bytes)}</p>
      <p><b>Camera:</b> {[visibleDetail.camera_make,visibleDetail.camera_model].filter(Boolean).join(' ')||'Unknown'}
       {' '}· <b>Resolution:</b> {visibleDetail.width??'?'} × {visibleDetail.height??'?'}</p>
      <p><b>Flags:</b> {visibleDetail.blurry?'Possibly blurry · ':''}{visibleDetail.low_resolution?'Low resolution':'No low-resolution flag'}</p>
      <p><b>Model:</b> {visibleDetail.model_tag||'Not classified'}</p>
     </div>
     <h3>File locations ({visibleDetail.copy_total})</h3>
     <div className="review-locations">{[...visibleDetail.copies,...moreCopies].map(c=><div key={c.id}>
      <small>{c.missing?'Missing':!c.root_enabled?'Disabled library':'Present at last scan'}</small>
      <code>{c.root_path}/{c.relative_path}</code></div>)}</div>
     {visibleDetail.copies.length+moreCopies.length<visibleDetail.copy_total&&<button className="review-load-copies" disabled={copiesBusy}
      onClick={()=>void loadMoreCopies()}>{copiesBusy?'Loading…':'Load more file locations'}</button>}
     <h3>Review history (latest 40)</h3>
     <div className="review-history">{visibleDetail.history.length===0?<p>No manual changes yet.</p>:
      visibleDetail.history.map(entry=><p key={entry.id}>{entry.created_at} · {text(entry.before.decision)}
       {' → '}{text(entry.after.decision)}
       {entry.before.category_override!==entry.after.category_override?' · Category updated':''}</p>)}</div>
     <small className="review-help">Keyboard: K Keep · C Clutter · L Later · U Reset · ←/→ Navigate · Esc Close</small>
    </>}
   </div></div>}
  <p className="review-safety">Only PhotoSift review records change. Original photographs are never edited or deleted. Exports require explicit M6 approval.</p>
 </section>;
}
