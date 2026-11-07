'use strict';
const T = JSON.parse(document.getElementById('translations').textContent);
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="app-token"]').content;
const lang = document.documentElement.lang;
const number = n => new Intl.NumberFormat(lang).format(n || 0);
function bytes(n) { let i=0; const units=lang==='ru'?['Б','КБ','МБ','ГБ','ТБ']:['B','KB','MB','GB','TB']; n=Number(n)||0; while(n>=1000&&i<4){n/=1000;i++;}return new Intl.NumberFormat(lang,{maximumFractionDigits:i?1:0}).format(n)+' '+units[i]; }
function message(code){ return T[code] || T.application_failed; }
function notice(code){
  if($('help').open){$('setup-alert-text').textContent=message(code);$('setup-alert').hidden=false;$('setup-pages').scrollTop=0;}
  else{$('notice-text').textContent=message(code);$('notice').hidden=false;}
}
async function api(path, data, timeout=30000) {
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),timeout);
  try{
    const headers={'X-App-Token':token}; const options={headers,signal:controller.signal};
    if(data!==undefined){ options.method='POST'; if(data instanceof FormData) options.body=data; else{headers['Content-Type']='application/json';options.body=JSON.stringify(data);} }
    const response=await fetch(path,options); let value;
    try{value=await response.json();}catch{throw new Error('application_failed');}
    if(!response.ok) throw new Error(value.error || 'application_failed');
    return value;
  }catch(e){if(e.name==='AbortError')throw new Error('request_timeout');if(e instanceof TypeError)throw new Error('service_unreachable');throw e;}finally{clearTimeout(timer);}
}
async function action(fn){try{await fn();}catch(e){notice(e.message);}}
let folder='root',folderName='Google Drive',history=[],files=[],nextPage='',listingSequence=0;
let state=null, wasConnected=false, pickerPending=false, pollTimer=null, shuttingDown=false;
let lastAuthError='',lastPollError='';
const icon = isFolder => isFolder?'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z"/></svg>':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M6 3h8l4 4v14H6Z"/><path d="M14 3v5h4M9 12h6m-6 4h6"/></svg>';
function renderFiles(){
  const list=$('file-list');list.replaceChildren();const filter=$('filter').value.toLocaleLowerCase(lang);
  for(const file of files){
    if(!file.name.toLocaleLowerCase(lang).includes(filter))continue;
    const isFolder=file.mimeType==='application/vnd.google-apps.folder';
    const unsupported=!isFolder&&file.mimeType.startsWith('application/vnd.google-apps.');
    const row=document.createElement('div');row.className='file-row';
    const symbol=document.createElement('span');symbol.className='file-icon';symbol.innerHTML=icon(isFolder);symbol.setAttribute('aria-hidden','true');
    const info=document.createElement('div');info.className='file-info';
    const name=document.createElement(isFolder?'button':'div');name.className='file-name';name.textContent=file.name;
    if(isFolder)name.addEventListener('click',()=>action(async()=>{history.push({id:folder,name:folderName});folder=file.id;folderName=file.name;await loadFolder();}));
    const size=document.createElement('div');size.className='file-size';size.textContent=isFolder?T.folder:unsupported?T.unsupported:bytes(file.size);
    info.append(name,size);row.append(symbol,info);
    if(!isFolder&&!unsupported){const download=document.createElement('button');download.dataset.download='1';download.disabled=Boolean(state?.active);download.textContent='↓';download.title=T.download_file;download.setAttribute('aria-label',T.download_file+' '+file.name);download.addEventListener('click',()=>action(()=>startJob(file.id,file.name)));row.append(download);}
    list.append(row);
  }
  if(!list.children.length){const empty=document.createElement('div');empty.className='empty';empty.textContent=T.empty_folder;list.append(empty);}
  $('more').hidden=!nextPage;$('listing-status').textContent=number(files.length)+' '+T.listing_count;
}
async function loadFolder(page=''){
  const sequence=++listingSequence;$('folder-name').textContent=folderName;$('back').disabled=!history.length;
  $('listing-status').textContent=T.loading;$('refresh').disabled=true;$('more').disabled=true;
  try{const result=await api('/api/files?'+new URLSearchParams({folder,page}));if(sequence!==listingSequence)return;files=result.files||[];nextPage=result.nextPageToken||'';renderFiles();}
  finally{if(sequence===listingSequence){$('refresh').disabled=false;$('more').disabled=false;}}
}
async function startJob(source=folder,name=folderName){
  const root=$('destination').value.trim();if(!root){notice('choose_folder');$('destination').focus();return;}
  await api('/api/jobs',{source,name,root,workers:Number($('workers').value)});localStorage.setItem('diskdownloader.destination',root);await update();
}
const jobNodes=new Map();
function renderJobs(jobs){
  if(!jobs.length){$('jobs').replaceChildren();const empty=document.createElement('div');empty.className='empty';empty.textContent=T.no_tasks;$('jobs').append(empty);return;}
  if(!jobNodes.size)$('jobs').replaceChildren();
  const keep=new Set(jobs.map(j=>j.id));for(const[id,node]of jobNodes){if(!keep.has(id)){node.remove();jobNodes.delete(id);}}
  for(const job of jobs){
    let node=jobNodes.get(job.id);
    if(!node){node=document.createElement('article');node.className='job';node.innerHTML='<div class="job-title"><div><h3 class="job-name"></h3><div class="job-path"></div></div><span class="job-status"></span></div><progress class="job-progress" max="100"></progress><div class="job-stats"></div><p class="job-error"></p><div class="job-actions"></div>';jobNodes.set(job.id,node);$('jobs').append(node);}
    node.querySelector('.job-name').textContent=job.name;node.querySelector('.job-path').textContent=job.root;
    const active=['running','pausing','stopping'].includes(job.status);
    node.querySelector('.job-status').textContent=active?message(job.phase)+' · '+message(job.status):message(job.status);
    const progress=node.querySelector('progress');if(job.phase==='scan'&&active)progress.removeAttribute('value');else progress.value=job.total?job.complete/job.total*100:0;
    let stats=number(job.complete)+' / '+number(job.total)+' '+T.complete_files+' · '+bytes(job.bytes)+' '+T.on_device;
    if(job.failed)stats+=' · '+number(job.failed)+' '+T.failed_files;if(job.unsupported)stats+=' · '+number(job.unsupported)+' '+T.unsupported_files;
    if(active)stats+=' · '+number(job.folders)+' '+T.scanned_folders+' · '+number(state.session.files_checked)+' '+T.checked_files+' · '+bytes(state.session.network_bytes)+' '+T.session_received;
    node.querySelector('.job-stats').textContent=stats;node.querySelector('.job-error').textContent=job.error?message(job.error):'';
    if(node.dataset.status!==job.status){const actions=node.querySelector('.job-actions');actions.replaceChildren();
      const add=(name,cmd)=>{const button=document.createElement('button');button.textContent=T[name];button.addEventListener('click',()=>action(async()=>{await api('/api/jobs/'+job.id+'/'+cmd,{});await update();}));actions.append(button);};
      if(job.status==='running'){add('pause','pause');add('stop','stop');}else if(!['pausing','stopping'].includes(job.status))add('resume','resume');
      const report=document.createElement('a');report.className='button secondary';report.href='/api/jobs/'+encodeURIComponent(job.id)+'/report.csv';report.textContent=T.report;actions.append(report);node.dataset.status=job.status;
    }
  }
}
function meter(id,value,limit){$(id).value=Math.min(100,limit>0?value/limit*100:0);}
let limitsProject=null;
async function update(){
  state=await api('/api/state',undefined,15000);const auth=state.auth;
  $('connection-status').textContent=(auth.pending?T.signin_pending:auth.connected?T.connected:auth.configured?T.configured:T.not_connected)+(auth.project?' · '+auth.project:'');
  $('credential-status').textContent=auth.configured?(auth.persistent?T.credentials_saved:T.credentials_memory):'';
  $('connect').disabled=!auth.configured||auth.pending||Boolean(state.active);$('connect').hidden=auth.connected&&!auth.error;
  $('disconnect').hidden=!auth.configured;$('disconnect').disabled=Boolean(state.active);$('download').disabled=!auth.connected||auth.pending||Boolean(auth.error)||Boolean(state.active);
  $('choose-client').disabled=Boolean(state.active)||auth.pending;
  document.querySelectorAll('[data-download]').forEach(button=>{button.disabled=Boolean(state.active)||!auth.connected||auth.pending||Boolean(auth.error);});
  if(auth.error&&auth.error!==lastAuthError)notice(auth.error);lastAuthError=auth.error||'';
  if(auth.connected&&!wasConnected)action(()=>loadFolder());
  if(!auth.connected&&wasConnected){listingSequence++;folder='root';folderName='Google Drive';history=[];files=[];nextPage='';$('folder-name').textContent=folderName;$('back').disabled=true;renderFiles();}
  wasConnected=auth.connected;
  syncGuide(auth);
  if(auth.configured)guideIntroduced=true;
  if(!auth.configured&&!guideIntroduced)openGuide();
  if(pickerPending&&!state.picker.pending){pickerPending=false;$('pick-folder').disabled=false;if(state.picker.path)$('destination').value=state.picker.path;if(state.picker.error)notice(state.picker.error);}
  renderJobs(state.jobs);const q=state.quota,l=q.limits;
  $('minute-units').textContent=number(q.recent.units);$('day-units').textContent=number(q.day.units);$('day-bytes').textContent=bytes(q.day.bytes);$('limited').textContent=number(q.day.limited);
  $('minute-limit').textContent=T.reference+': '+number(Math.min(l.project_minute,l.user_minute));$('day-limit').textContent=T.reference+': '+number(l.day_units);$('bytes-limit').textContent=T.reference+': '+bytes(l.day_bytes);
  meter('minute-meter',q.recent.units,Math.min(l.project_minute,l.user_minute));meter('day-meter',q.day.units,l.day_units);meter('bytes-meter',q.day.bytes,l.day_bytes);$('console').href=state.console;
  if(limitsProject!==q.project){for(const[k,v]of Object.entries(l))$('limits-form').elements[k].value=v;limitsProject=q.project;}
}
async function poll(){if(shuttingDown)return;try{await update();lastPollError='';}catch(e){if(e.message!==lastPollError)notice(e.message);lastPollError=e.message;}finally{clearTimeout(pollTimer);pollTimer=setTimeout(poll,document.hidden?30000:state?.active||state?.auth.pending||pickerPending?2000:15000);}}
function guidePreference(key){try{return localStorage.getItem('diskdownloader.setup-'+key);}catch{return null;}}
function saveGuidePreference(key,value){try{localStorage.setItem('diskdownloader.setup-'+key,value);}catch{}}
const guidePages=[...document.querySelectorAll('.setup-step')];
const guideJumps=[...document.querySelectorAll('.setup-jump')];
let guideStep=0,guideIntroduced=guidePreference('seen')==='1',guideReturnFocus=null;
function showGuideStep(index,focus=true){
  guideStep=Math.max(0,Math.min(guidePages.length-1,Number.isFinite(index)?Math.trunc(index):0));
  guidePages.forEach((page,i)=>{page.hidden=i!==guideStep;});
  guideJumps.forEach((button,i)=>{if(i===guideStep)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');});
  $('setup-select').value=String(guideStep);
  $('setup-counter').textContent=T.guide_counter.replace('{current}',number(guideStep+1)).replace('{total}',number(guidePages.length));
  $('setup-progress').value=guideStep+1;
  $('setup-previous').disabled=guideStep===0;
  $('setup-next').textContent=guideStep===guidePages.length-1?T.guide_finish:T.guide_next+' →';
  $('setup-alert').hidden=true;
  $('setup-pages').scrollTop=0;
  saveGuidePreference('step',String(guideStep));
  if(focus&&$('help').open)guidePages[guideStep].querySelector('h3').focus({preventScroll:true});
}
function openGuide(){
  guideIntroduced=true;saveGuidePreference('seen','1');
  showGuideStep(Number(guidePreference('step')??guideStep),false);
  if(!$('help').open){$('help').showModal();document.body.classList.add('modal-open');}
  guidePages[guideStep].querySelector('h3').focus({preventScroll:true});
}
function syncGuide(auth){
  const importing=document.querySelector('[data-setup-action="import"]');
  const connecting=document.querySelector('[data-setup-action="connect"]');
  importing.disabled=Boolean(state.active)||auth.pending;
  connecting.disabled=!auth.configured||auth.pending||Boolean(state.active)||Boolean(auth.connected&&!auth.error);
  document.querySelector('[data-setup-status="import"]').textContent=auth.configured?T.guide_import_ready.replace('{project}',auth.project):T.guide_import_waiting;
  document.querySelector('[data-setup-status="connect"]').textContent=auth.error?message(auth.error):auth.pending?T.signin_pending:auth.connected?T.connected:auth.configured?T.configured:T.not_connected;
}
$('help-toggle').onclick=openGuide;
$('help-close').onclick=()=>{$('help').close();};
$('help').addEventListener('close',()=>{document.body.classList.remove('modal-open');(guideReturnFocus||$('help-toggle')).focus({preventScroll:true});guideReturnFocus=null;});
$('help').addEventListener('keydown',event=>{
  if(event.key!=='Tab')return;
  const controls=[...$('help').querySelectorAll('button,a[href],input,select,summary,[tabindex]')].filter(element=>!element.disabled&&element.tabIndex>=0&&element.getClientRects().length);
  const first=controls[0],last=controls[controls.length-1];
  if(event.shiftKey&&(document.activeElement===first||!controls.includes(document.activeElement))){event.preventDefault();last.focus();}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
});
$('help').addEventListener('click',event=>{const box=$('help').getBoundingClientRect();if(event.target===$('help')&&(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom))$('help').close();});
guideJumps.forEach(button=>{button.onclick=()=>showGuideStep(Number(button.dataset.step));});
$('setup-select').onchange=()=>showGuideStep(Number($('setup-select').value));
$('setup-previous').onclick=()=>showGuideStep(guideStep-1);
$('setup-next').onclick=()=>{if(guideStep===guidePages.length-1)$('help').close();else showGuideStep(guideStep+1);};
$('setup-restart').onclick=()=>showGuideStep(0);
document.querySelector('[data-setup-action="import"]').onclick=()=>{$('client-file').click();};
document.querySelector('[data-setup-action="connect"]').onclick=()=>action(connectDrive);
document.querySelector('[data-setup-action="destination"]').onclick=()=>{guideReturnFocus=$('destination');$('help').close();};
$('setup-alert-close').onclick=()=>{$('setup-alert').hidden=true;};
$('notice-close').onclick=()=>{$('notice').hidden=true;};
$('language').onchange=()=>action(async()=>{await api('/api/settings',{language:$('language').value});location.reload();});
$('choose-client').onclick=()=>{$('client-file').click();};
$('client-file').onchange=()=>action(async()=>{if(!$('client-file').files[0])return;const data=new FormData();data.append('file',$('client-file').files[0]);await api('/api/client',data);notice('file_imported');await update();});
async function connectDrive(){await api('/api/connect',{});await update();clearTimeout(pollTimer);pollTimer=setTimeout(poll,2000);}
$('connect').onclick=()=>action(connectDrive);
$('disconnect').onclick=()=>action(async()=>{await api('/api/disconnect',{});await update();});
$('refresh').onclick=()=>action(()=>loadFolder());$('more').onclick=()=>action(()=>loadFolder(nextPage));$('filter').oninput=renderFiles;
$('back').onclick=()=>action(async()=>{const previous=history.pop();if(previous){folder=previous.id;folderName=previous.name;await loadFolder();}});
$('download').onclick=()=>action(()=>startJob());
$('pick-folder').onclick=()=>action(async()=>{await api('/api/pick-folder',{});pickerPending=true;$('pick-folder').disabled=true;clearTimeout(pollTimer);pollTimer=setTimeout(poll,400);});
$('limits-form').onsubmit=e=>{e.preventDefault();action(async()=>{const limits=Object.fromEntries([...new FormData(e.target)].map(([k,v])=>[k,Number(v)]));await api('/api/settings',{limits});notice('limits_saved');await update();});};
$('shutdown').onclick=()=>action(async()=>{await api('/api/shutdown',{});shuttingDown=true;clearTimeout(pollTimer);notice('shutdown_notice');});
try{$('destination').value=localStorage.getItem('diskdownloader.destination')||'';}catch{}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!shuttingDown){clearTimeout(pollTimer);pollTimer=setTimeout(poll,200);}});
poll();
