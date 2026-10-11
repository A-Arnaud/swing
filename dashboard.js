'use strict';
const $ = id => document.getElementById(id);
const labels = {running:'En cours',stopped:'À l’arrêt',paused:'En pause',auto_paused:'Pause automatique',accepted_in_snapshot:'Validé',objective_verified:'Consignes vérifiées',rejected:'Non retenu',interrupted:'Interrompu'};
let activeProject = localStorage.getItem('swing-project') || '', projectVersion = 0;
let state = null, currentView = 'activity', instructionDirty = false, busy = false, fetching = false, firstLoad = true;
let lastPhase = '', lastCalls = null, lastCycle = '', lastCycleState = '', historySignature = '', toastTimer;
const number = n => Number(n || 0).toLocaleString('fr-FR');
const timeLabel = t => new Date(t * 1000).toLocaleTimeString('fr-FR', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
function element(tag, className, text) {const el = document.createElement(tag); if(className) el.className = className; if(text != null) el.textContent = text; return el;}
function toast(message) {$('toast').textContent = message; $('toast').classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').classList.remove('show'), 3600);}
async function api(route, body) {
  const options = body === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  if(activeProject && route!=='/api/projects' && !route.startsWith('/api/github/'))route+=(route.includes('?')?'&':'?')+'project='+encodeURIComponent(activeProject);
  const response = await fetch(route, options); const result = await response.json();
  if(!response.ok) throw new Error(result.status || result.error || 'request_failed');
  return result;
}
function showWorkspace() {$('project-picker').classList.add('hidden');$('workspace').classList.remove('hidden');localStorage.setItem('swing-screen','workspace');window.scrollTo(0,0);}
function showPicker() {$('workspace').classList.add('hidden');$('project-picker').classList.remove('hidden');localStorage.setItem('swing-screen','home');window.scrollTo(0,0);loadProjects();}
$('open-project').addEventListener('click',()=>openConnectedProject(''));
$('back-projects').addEventListener('click',showPicker);
$('project-home').addEventListener('click',showPicker);
if(localStorage.getItem('swing-screen') === 'workspace') showWorkspace();
function selectView(view) {
  currentView = view;
  document.querySelectorAll('[data-view]').forEach(button => {const active=button.dataset.view===view; button.classList.toggle('active',active); if(button.role==='tab'){button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}});
  ['activity','changes','files','history'].forEach(name => $('view-'+name).classList.toggle('hidden',name!==view));
  if(view === 'changes') loadChanges();
  if(view === 'files') loadRepository();
}
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click',() => selectView(button.dataset.view)));
document.querySelector('.panel-tabs').addEventListener('keydown',event => {
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  event.preventDefault(); const views=['activity','changes','files','history'];let index=views.indexOf(currentView);
  index=event.key==='Home'?0:event.key==='End'?3:(index+(event.key==='ArrowRight'?1:3))%4;
  selectView(views[index]);$('tab-'+views[index]).focus();
});
$('toggle-stats').addEventListener('click',() => {
  const open=$('toggle-stats').getAttribute('aria-expanded')!=='true';
  $('toggle-stats').setAttribute('aria-expanded',String(open));$('stats-panel').classList.toggle('open',open);$('stats-panel').inert=!open;
});
function log(message,kind='',timestamp=Date.now()/1000) {
  const line=element('div','log-line '+kind);line.append(element('time','',timeLabel(timestamp)),element('span','',message));
  const output=$('terminal-log'); const nearBottom=output.scrollHeight-output.scrollTop-output.clientHeight<50;
  output.append(line);while(output.children.length>55)output.firstChild.remove();
  if(nearBottom)output.scrollTop=output.scrollHeight;
}
function instructionsChanged() {instructionDirty=true;$('instruction-count').textContent=number($('instructions').value.length)+' / 4 000';$('instruction-dirty').textContent='Non enregistré';}
$('instructions').addEventListener('input',instructionsChanged);
$('save-instructions').addEventListener('click',async () => {
  const button=$('save-instructions');const value=$('instructions').value;button.disabled=true;
  try {await api('/api/instructions',{instructions:value});instructionDirty=$('instructions').value!==value;
    $('instruction-dirty').textContent=instructionDirty?'Non enregistré':'';toast('Consignes enregistrées.');log('Consignes mises à jour pour la prochaine tâche.');await refresh();
  } catch {toast('Les consignes n’ont pas été enregistrées. Réessayez.');}
  finally{button.disabled=false;}
});
function renderReceipt(s) {
  if(instructionDirty)return;
  const receipt=s.instruction_delivery;
  const sent=!!receipt?.fingerprint && receipt.fingerprint===s.instructions_fingerprint;
  $('receipt-dot').classList.toggle('delivered',sent);
  $('instruction-state').textContent=!s.ralph_instructions?'Aucune consigne supplémentaire.':sent
    ? 'Transmises à Codex à '+timeLabel(receipt.sent_at)+' · '+(receipt.stage==='implementation'?'développement':receipt.stage==='objective'?'objectif':receipt.stage==='understanding'?'analyse du projet':'recherche')
    : 'Enregistrées · en attente de la prochaine tâche.';
}
function renderHistory(cycles) {
  const signature=JSON.stringify(cycles.map(c=>[c.id,c.status,c.reason]));
  if(signature===historySignature)return;historySignature=signature;
  const rows=cycles.map(c=>{
    const row=element('article','event '+c.status);const body=element('div','event-body');
    body.append(element('div','event-title',c.proposal?.problem || (c.proposal_source==='objective'?'Bilan des consignes':'Recherche de pistes')));
    const notes=[labels[c.status]||c.status,c.proposal_source==='backlog'?'Piste conservée':null,
      c.implementation_seconds!=null?'Code : '+Math.round(c.implementation_seconds)+' s':null,
      c.discovery_seconds!=null?'Recherche : '+Math.round(c.discovery_seconds)+' s':null,
      c.reason,c.started_at?new Date(c.started_at*1000).toLocaleString('fr-FR'):null].filter(Boolean);
    body.append(element('div','event-note',notes.join(' · ')));row.append(element('span','event-mark'),body);return row;
  });
  $('events').replaceChildren(...(rows.length?rows:[element('p','empty-state','La première itération apparaîtra ici.')]));
}
function duration(cycle) {
  const seconds=cycle?.status==='running'?Date.now()/1000-cycle.started_at:cycle?.elapsed_seconds;
  if(seconds==null)return '—';const n=Math.max(0,Math.floor(seconds));
  return Math.floor(n/60)+' min '+String(n%60).padStart(2,'0')+' s';
}
function render(s) {
  state=s;syncProjectTheme(s.project);
  $('open-app').classList.toggle('hidden',!!activeProject);
  $('open-report').href='/report'+(activeProject?'?project='+encodeURIComponent(activeProject):'');const control=s.control||{}, cycle=s.cycles?.[0], starting=!!s.start_pending && control.mode==='stopped';
  const running=control.mode==='running';const stopped=control.mode==='stopped';
  $('connection').textContent='Connecté';
  $('mode').textContent=starting?'Démarrage…':labels[control.mode]||'En attente';
  $('dot').className='status-dot '+(starting?'running':control.mode);
  $('stop').textContent=starting?'Vérification…':stopped?'Reprendre ↗':'Arrêter □';
  $('stop').classList.toggle('stopping',!stopped && !starting);$('stop').disabled=busy||starting;
  if(!activeProject){$('project-state').textContent=labels[control.mode]||'Connecté';$('project-last').textContent=cycle?.status==='accepted_in_snapshot'?'Dernière modification validée':'Voir l’avancement';}
  $('terminal-state').textContent=running?'boucle active':'boucle arrêtée';
  $('task-label').textContent=running?'EN CE MOMENT':'DERNIÈRE ACTIVITÉ';
  $('elapsed').textContent=duration(cycle);
  let phase=s.phase||'Prêt à commencer.';
  if(stopped) phase=control.reason==='chatgpt_quota_exhausted'?'Quota atteint · Ralph arrêté':s.start_error?'Démarrage bloqué : '+s.start_error:'Ralph arrêté';
  if(stopped && control.reason==='no_new_validated_hypothesis')phase='Aucune tâche justifiée · Ralph arrêté';
  if(stopped && control.reason==='work_waiting_for_approval')phase='Une tâche attend votre validation';
  $('phase').textContent=phase;
  const objective=s.objective, criteria=objective?.criteria||[];
  const pursuing=objective && objective.state!=='completed';
  const queue=(s.proposals||[]).filter(p=>p.status==='queued' && (!pursuing || p.payload.objective_fingerprint===objective.fingerprint));
  $('next-task').textContent=running && cycle?.proposal?'Tâche ciblée · '+(cycle.proposal.files||[]).map(x=>x.split('/').pop()).join(', '):queue.length?queue.length+' tâches en attente':cycle?.status==='accepted_in_snapshot'?'Dernière avancée : '+(cycle.changed_files||[]).map(x=>x.split('/').pop()).join(', '):'';
  $('objective-progress').classList.toggle('hidden',!objective);
  $('objective-label').textContent=objective?({planning:'Préparer les consignes',active:'Priorité aux consignes',blocked:'Objectif bloqué',completed:'Consignes vérifiées · améliorations libres'}[objective.state]||'Consignes'):'';
  $('objective-count').textContent=criteria.length?criteria.filter(c=>c.status==='met').length+' / '+criteria.length:'';
  $('objective-criteria').replaceChildren(...criteria.map(c=>element('li','',({met:'✓ ',unmet:'○ ',unknown:'? '}[c.status]||'')+c.criterion)));
  const reasons={objective_needs_evidence_or_scope:'Il manque une preuve ou une autorisation dans le périmètre du projet.',objective_waiting_for_approval_or_scope:'Une tâche attend une approbation ou un périmètre adapté.',objective_transport_unavailable:'Le transport configuré ne prend pas en charge les objectifs.',independent_validation_failed:'Les contrôles indépendants échouent. L’objectif reste ouvert.',instructions_changed_before_task:'Les consignes ont changé. Le plan sera renouvelé.',manual_interrupt:'Travail interrompu. Le plan est conservé.',chatgpt_quota_exhausted:'Quota atteint. Le plan est conservé.'};
  $('objective-reason').textContent=reasons[objective?.reason]||'';
  const profile=s.project_profile, understanding=s.project_understanding;
  $('project-understanding').classList.toggle('hidden',!profile);
  $('understanding-label').textContent=understanding?'Projet analysé':profile?'Analyse à la première itération':'';
  $('understanding-purpose').textContent=understanding?.purpose||[...(profile?.languages||[]),...(profile?.frameworks||[])].join(' · ');
  $('understanding-architecture').textContent=understanding?.architecture||'';
  $('understanding-checks').textContent=understanding?.testing||(profile?.validation_level==='structural'?'Contrôles de structure · tests de comportement à créer':'Tests et contrôles détectés automatiquement');
  const steps=['discover','implement','verify','apply'];let step=-1;
  if(running){step=/travaille/i.test(s.phase)?1:/indépendants|contrôle des modifications/i.test(s.phase)?2:/application|sauvegarde/i.test(s.phase)?3:0;}
  document.querySelectorAll('[data-step]').forEach(el=>{const index=steps.indexOf(el.dataset.step);el.classList.toggle('active',index===step);el.classList.toggle('done',step>index);});
  const windows=s.chatgpt_quota?.windows||[];
  $('quota').textContent=windows.length?windows.map(w=>w.used_percent+' %').join(' / '):'Non lu';
  $('quota-bar').value=windows[0]?.used_percent||0;
  $('quota-detail').textContent=windows.length?'Principal / secondaire · instantané avant appel':'Vérifié avant chaque appel au modèle';
  $('calls').textContent=number(s.model_calls)+' appels';
  $('tokens').textContent=number(s.token_usage?.measured_total)+' tokens mesurés'+(s.token_usage?.unknown_calls?' · '+s.token_usage.unknown_calls+' appels non mesurés':'');
  const usage=s.last_model_usage;
  $('usage-detail').textContent=usage?'Dernier appel : '+number(usage.input_tokens)+' entrée / '+number(usage.output_tokens)+' sortie'+(usage.cached_input_tokens!=null?' · '+number(usage.cached_input_tokens)+' entrée en cache':''):'Le détail des prochains appels sera enregistré.';
  $('approvals').textContent=(s.proposals||[]).filter(p=>p.status==='awaiting_approval').length+' en attente';
  $('streak').textContent=(s.no_progress_cycles||0)+' cycles sans progrès consécutifs';
  $('project-memory').textContent=s.memory_status?s.memory_status.indexed_files+' fichiers repérés':'Préparation';
  $('context-size').textContent=s.instruction_delivery?.prompt_chars?number(s.instruction_delivery.prompt_chars)+' caractères envoyés au dernier appel':'Contexte ciblé selon la tâche';
  $('model').textContent=(s.model||'—')+' · '+(s.reasoning_effort||'—');
  const name=s.project?.name||'Projet';
  for(const id of ['workspace-project','sidebar-project'])$(id).textContent=name;
  if(!activeProject){$('project-name').textContent=name;$('project-state').textContent=labels[control.mode]||'Connecté';}
  $('project-title').textContent=name;
  $('terminal-project').textContent=name.toLowerCase().replaceAll(' ','-');document.title='SWING · '+name;
  if(!instructionDirty && document.activeElement!==$('instructions')){$('instructions').value=s.ralph_instructions||'';$('instruction-count').textContent=number($('instructions').value.length)+' / 4 000';}
  renderReceipt(s);renderHistory(s.cycles||[]);$('updated').textContent=new Date().toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});
  if(firstLoad){
    (s.cycles||[]).filter(c=>c.status!=='running').slice(0,3).reverse().forEach(c=>log((labels[c.status]||c.status)+' · '+(c.proposal?.problem||c.reason||'Cycle terminé'),c.status==='accepted_in_snapshot'?'success':'',c.started_at));
    log(stopped?'Ralph arrêté.':s.phase||'Lecture de l’activité.');firstLoad=false;
  } else {
    if(s.phase!==lastPhase && s.phase)log(s.phase);
    if(lastCalls!==null && s.model_calls>lastCalls)log('Appel Codex engagé · quota vérifié.');
    if(cycle?.id===lastCycle && cycle.status!==lastCycleState)log((labels[cycle.status]||cycle.status)+' · '+(cycle.reason||'Contrôles terminés.'),cycle.status==='accepted_in_snapshot'?'success':'');
  }
  lastPhase=s.phase;lastCalls=s.model_calls;lastCycle=cycle?.id;lastCycleState=cycle?.status;
}
async function refresh(){
  if(fetching)return;fetching=true;
  const version=projectVersion;
  try{const result=await api('/api/status');if(version===projectVersion)render(result);}
  catch{$('connection').textContent='Reconnexion…';$('stop').disabled=true;}
  finally{fetching=false;}
}
$('stop').addEventListener('click',async()=>{
  if(busy||!state)return;busy=true;$('stop').disabled=true;
  try{const start=state.control?.mode==='stopped';const result=await api(start?'/api/start':'/api/stop',{});
    toast(start?'Reprise demandée. Vérification du quota.':'Arrêt demandé. Le cycle va s’interrompre.');log(start?'Reprise demandée.':'Arrêt demandé depuis SWING.');await refresh();
  }catch{toast('Commande non exécutée. Vérifiez la connexion.');}
  finally{busy=false;await refresh();}
});
let loadingCode=false,lastDiff='';
async function loadChanges(){
  if(loadingCode)return;loadingCode=true;
  const version=projectVersion;
  try{const data=await api('/api/changes');if(version!==projectVersion)return;const signature=JSON.stringify(data);if(signature===lastDiff)return;lastDiff=signature;
    $('diff-label').textContent=data.state==='working'?'Modifications en cours':'Dernière modification appliquée';
    $('changes-dot').style.opacity=data.files?.some(f=>f.diff)?'1':'0';
    const rows=(data.files||[]).map(file=>{
      const details=element('details');details.open=true;details.append(element('summary','',file.name));
      const pre=element('pre');(file.diff||'Aucune modification visible pour le moment.').split('\n').forEach(line=>pre.append(element('span','code-line '+(line.startsWith('+')?'add':line.startsWith('-')?'remove':line.startsWith('@@')?'hunk':''),line)));
      if(file.truncated)pre.append(element('span','code-line','… aperçu limité à 400 lignes.'));details.append(pre);return details;
    });
    $('code-diff').replaceChildren(...(rows.length?rows:[element('p','empty-state','Les modifications apparaîtront ici pendant le développement.')]));
  }catch{$('code-diff').replaceChildren(element('p','empty-state','Le code n’est pas disponible. Réessayez dans un instant.'));}
  finally{loadingCode=false;}
}
$('refresh-code').addEventListener('click',loadChanges);
function openProjectDialog(project={}) {
  $('draft-id').value=project.id||'';$('draft-name').value=project.name||'';$('draft-idea').value=project.idea||'';$('draft-instructions').value=project.instructions||'';
  $('project-dialog-title').textContent=project.id?'Modifier le projet':'Nouveau projet';
  $('draft-feedback').textContent='';$('project-dialog').showModal();
}

$('new-project-card').addEventListener('click',()=>openProjectDialog());

document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>$(button.dataset.close).close()));
document.querySelectorAll('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}}));
$('project-form').addEventListener('submit',async event=>{
  event.preventDefault();$('save-project').disabled=true;
  try{await api('/api/projects',{id:$('draft-id').value||undefined,name:$('draft-name').value,idea:$('draft-idea').value,instructions:$('draft-instructions').value});
    $('project-dialog').close();toast('Projet enregistré.');await loadProjects();
  }catch{$('draft-feedback').textContent='Enregistrement impossible. Votre texte est conservé ici.';}
  finally{$('save-project').disabled=false;}
});
async function loadProjects(){
  try{const data=await api('/api/projects');document.querySelectorAll('.draft-card').forEach(x=>x.remove());
    for(const project of data.projects||[]){
      const card=element('button','project-card draft-card');const top=element('div','project-card-top');
      top.append(element('span','project-monogram',project.name.slice(0,2).toUpperCase()),element('span','card-tag',project.state==='connected'?'GitHub':'Brief'));
      card.append(top,element('strong','',project.name),element('p','',project.idea.slice(0,100)+(project.idea.length>100?'…':'')),element('span','card-link',project.state==='connected'?'Ouvrir ↗':'Modifier ↗'));
      card.addEventListener('click',()=>project.state==='connected'?openConnectedProject(project.id):openProjectDialog(project));$('project-grid').insertBefore(card,$('new-project-card'));
    }
    const connected=(data.projects||[]).filter(p=>p.state==='connected').length, briefs=(data.projects||[]).length-connected;
    $('project-count').textContent=(1+connected)+' projets'+(briefs?' · '+briefs+' briefs':'');
  }catch{/* The connected project remains available if draft storage is unavailable. */}
}
const letters={
 S:[' █████ ','██     ','██     ',' █████ ','     ██','     ██',' █████ '],
 W:['██   ██','██   ██','██   ██','██ █ ██','██ █ ██','██ █ ██',' ██ ██ '],
 I:['███████','   █   ','   █   ','   █   ','   █   ','   █   ','███████'],
 N:['██   ██','███  ██','███  ██','██ █ ██','██  ███','██  ███','██   ██'],
 G:[' █████ ','██     ','██     ','██ ████','██   ██','██   ██',' █████ ']
};
$('ascii-logo').textContent=Array.from({length:7},(_,row)=>[...'SWING'].map(ch=>letters[ch][row]).join('  ')).join('\n');
const svg=$('swing-art');
for(let ring=0;ring<52;ring++){
  const phi=ring/52*Math.PI*2;let points=[];
  for(let j=0;j<=220;j++){
    const t=j/220*Math.PI*2, radial=160+42*Math.cos(phi+t*1.5);
    const x=300+radial*Math.cos(t)+45*Math.sin(t*2), y=245+radial*.66*Math.sin(t)+41*Math.sin(phi+t*1.5);
    points.push((j?'L':'M')+x.toFixed(2)+','+y.toFixed(2));
  }
  const path=document.createElementNS('http://www.w3.org/2000/svg','path');
  path.setAttribute('d',points.join(' '));path.setAttribute('stroke',ring%7===0?'#e9c2cf':'#ad939c');path.setAttribute('stroke-width','.65');path.setAttribute('opacity',String(.22+.55*ring/52));svg.append(path);
}
refresh();loadProjects();
setInterval(()=>{if(!document.hidden)refresh();},2000);
setInterval(()=>{if(!document.hidden && currentView==='changes' && !$('workspace').classList.contains('hidden'))loadChanges();},4000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});

// Appearance is local to this project's workspace, never the project picker.
let themeKey = '', projectTheme = {mode:'dark',color:'#a33361'};
const palettes = {
  dark:{bg:'#151215',surface:'#1b171c',raised:'#251f26',line:'#3b333c',text:'#efeaf0',muted:'#b2a6b5',success:'#b1c6a3',error:'#e1a4af'},
  midnight:{bg:'#09090b',surface:'#101013',raised:'#1b1b20',line:'#303037',text:'#ededf2',muted:'#acacba',success:'#b1c6a3',error:'#e1a4af'},
  light:{bg:'#f5f3f4',surface:'#ffffff',raised:'#ede8ed',line:'#d2c9d2',text:'#262129',muted:'#665a6b',success:'#306633',error:'#9a334a'}
};
function validColor(color){return /^#[0-9a-f]{6}$/i.test(color);}
function themeContrast(hex){
  const rgb=hex.slice(1).match(/../g).map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
  const lum=rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;
  return lum>.179?'#000000':'#ffffff';
}
function applyProjectTheme(){
  const workspace=$('workspace'), palette=palettes[projectTheme.mode];
  for(const [key,value] of Object.entries(palette))workspace.style.setProperty('--project-'+key,value);
  workspace.style.setProperty('--project-accent',projectTheme.color);
  workspace.style.setProperty('--project-on-accent',themeContrast(projectTheme.color));
  workspace.style.colorScheme=projectTheme.mode==='light'?'light':'dark';
  $('theme-color').value=projectTheme.color;$('theme-hex').value=projectTheme.color;
  $('theme-hex').removeAttribute('aria-invalid');
  document.querySelectorAll('[name="project-theme"]').forEach(input=>input.checked=input.value===projectTheme.mode);
  document.querySelectorAll('[data-color]').forEach(button=>{button.style.backgroundColor=button.dataset.color;button.setAttribute('aria-pressed',String(button.dataset.color===projectTheme.color));});
}
function syncProjectTheme(project){
  if(!project)return;
  const key='swing-appearance:'+ (project.path||project.name);
  if(key===themeKey)return;
  themeKey=key;let saved={};
  try{saved=JSON.parse(localStorage.getItem(key)||'{}')||{};}catch{}
  projectTheme={mode:Object.hasOwn(palettes,saved.mode)?saved.mode:'dark',color:validColor(saved.color||'')?saved.color:'#a33361'};
  applyProjectTheme();
}
function saveProjectTheme(){
  applyProjectTheme();
  try{if(!themeKey)throw new Error('not_ready');localStorage.setItem(themeKey,JSON.stringify(projectTheme));$('theme-feedback').textContent='Enregistré dans ce navigateur';}
  catch{$('theme-feedback').textContent='Aperçu uniquement · stockage indisponible';}
}
$('theme-button').addEventListener('click',()=>{$('theme-dialog').showModal();});
document.querySelectorAll('[name="project-theme"]').forEach(input=>input.addEventListener('change',()=>{projectTheme.mode=input.value;saveProjectTheme();}));
$('theme-color').addEventListener('input',()=>{projectTheme.color=$('theme-color').value;saveProjectTheme();});
$('theme-hex').addEventListener('input',()=>{
  const value=$('theme-hex').value.trim();
  if(!validColor(value)){$('theme-hex').setAttribute('aria-invalid','true');$('theme-feedback').textContent='Format : #A33361';return;}
  projectTheme.color=value.toLowerCase();saveProjectTheme();
});
document.querySelectorAll('[data-color]').forEach(button=>button.addEventListener('click',()=>{projectTheme.color=button.dataset.color;saveProjectTheme();}));
$('theme-reset').addEventListener('click',()=>{projectTheme={mode:'dark',color:'#a33361'};saveProjectTheme();});
applyProjectTheme();

async function openConnectedProject(id){
  activeProject=id;localStorage.setItem('swing-project',id);projectVersion++;
  repoDirectory='';repoFile='';repoBranch='';repoGeneration++;repoSignature='';$('repo-entries').replaceChildren();$('repo-content').replaceChildren();
  state=null;instructionDirty=false;firstLoad=true;lastPhase='';lastCalls=null;lastCycle='';historySignature='';lastDiff='';
  $('terminal-log').replaceChildren();$('events').replaceChildren();$('code-diff').replaceChildren();
  $('instructions').value='';$('instruction-dirty').textContent='';$('phase').textContent='Lecture du projet…';$('stop').disabled=true;
  showWorkspace();selectView('activity');
  // Wait for an old request to settle; its version guard prevents stale rendering.
  while(fetching)await new Promise(resolve=>setTimeout(resolve,50));
  await refresh();
}
$('import-github').addEventListener('click',()=>{$('github-feedback').textContent='';$('github-dialog').showModal();refreshGitHubAccount();});
$('github-form').addEventListener('submit',async event=>{
  event.preventDefault();$('github-submit').disabled=true;$('github-feedback').textContent='Clonage et préparation…';
  try{
    const project=await api('/api/github/import',{url:$('github-url').value,validation:$('github-validation').value,instructions:$('github-instructions').value});
    $('github-dialog').close();toast('Dépôt importé.');await loadProjects();await openConnectedProject(project.id);
    try{await api('/api/start',{});toast('Ralph démarre par l’analyse du projet.');await refresh();}
    catch{toast('Projet importé. Démarrage indisponible : utilisez Reprendre.');}
  }catch(error){
    const reasons={github_https_url_required:'Utilisez une URL https://github.com/compte/projet.',github_clone_failed_check_access:'Clonage impossible. Vérifiez le dépôt et votre accès GitHub.',validation_executable_required:'Outil de test absent du poste. Installez-le ou indiquez un autre exécutable.',npm_runtime_required:'Node et npm doivent être installés.',validation_command_required:'Commande non détectée. Indiquez la commande de test de ce projet.',no_supported_editable_source:'Aucun fichier source modifiable reconnu.',repository_contains_credentials:'Des identifiants sont présents dans le dépôt. Retirez-les avant l’import.'};
    $('github-feedback').textContent=reasons[error.message]||'Import impossible : '+error.message;
  }finally{$('github-submit').disabled=false;}
});

let githubRepos=[],githubNext=1,githubLoading=false,githubAccountPending=false,githubLogin=null,githubAccountVersion=0;
function renderGitHubRepositories(){
  const query=$('github-search').value.toLowerCase().trim();
  const rows=githubRepos.filter(repo=>repo.name.toLowerCase().includes(query)).map(repo=>{
    const button=element('button','github-repository');button.type='button';
    button.append(element('strong','',repo.name),element('small','',(repo.private?'Privé':'Public')+(repo.language?' · '+repo.language:'')));
    button.addEventListener('click',()=>{$('github-url').value=repo.url;$('github-validation').value='';$('github-feedback').textContent=repo.name+' sélectionné';});
    return button;
  });
  $('github-repositories').replaceChildren(...(rows.length?rows:[element('p','empty-state',query?'Aucun dépôt chargé ne correspond.':'Aucun dépôt chargé.')]));
  $('github-more').classList.toggle('hidden',!githubNext);
}
async function loadGitHubRepositories(reset=false){
  if(githubLoading)return;githubLoading=true;
  const version=githubAccountVersion;
  if(reset){githubRepos=[];githubNext=1;}
  try{
    const data=await api('/api/github/repositories?page='+(githubNext||1));
    if(version!==githubAccountVersion)return;
    const known=new Set(githubRepos.map(r=>r.url));githubRepos.push(...data.repositories.filter(r=>!known.has(r.url)));githubNext=data.next_page;
    renderGitHubRepositories();
  }catch{$('github-feedback').textContent='Impossible de lire les dépôts. Vérifiez la connexion GitHub.';}
  finally{githubLoading=false;}
}
async function refreshGitHubAccount(){
  if(githubAccountPending)return;githubAccountPending=true;
  try{
    const account=await api('/api/github/account');
    if(account.connected && account.login!==githubLogin){githubLogin=account.login;githubRepos=[];githubNext=1;githubAccountVersion++;}
    $('github-account-state').textContent=account.connecting?'Terminez la connexion dans le navigateur.':account.connected?'Connecté : '+account.login:'Compte non connecté';
    $('github-connect').textContent=account.connected?'Reconnecter':'Connecter GitHub';$('github-connect').disabled=account.connecting;
    $('github-browser').classList.toggle('hidden',!account.connected || account.connecting);
    if(account.connected && !account.connecting && !githubRepos.length)await loadGitHubRepositories(true);
    if(account.error)$('github-feedback').textContent='Connexion interrompue. Vous pouvez réessayer.';
  }catch{$('github-account-state').textContent='Connexion GitHub indisponible';}
  finally{githubAccountPending=false;}
}
$('github-connect').addEventListener('click',async()=>{
  $('github-connect').disabled=true;
  try{await api('/api/github/connect',{});githubRepos=[];githubAccountVersion++;$('github-account-state').textContent='Terminez la connexion dans le navigateur.';}
  catch{$('github-feedback').textContent='Connexion impossible. Git Credential Manager doit être installé sur le poste.';$('github-connect').disabled=false;}
});
$('github-search').addEventListener('input',renderGitHubRepositories);
$('github-more').addEventListener('click',()=>loadGitHubRepositories());
setInterval(()=>{if($('github-dialog').open && !document.hidden)refreshGitHubAccount();},3000);


let repoDirectory='', repoFile='', repoBranch='', repoGeneration=0, repoLoading=false, repoSignature='';
const relativeDate=new Intl.RelativeTimeFormat('fr',{numeric:'auto'});
function repoTime(timestamp){
  if(!timestamp)return '—';
  const seconds=Math.round((timestamp-Date.now()/1000));
  const unit=Math.abs(seconds)<60?'second':Math.abs(seconds)<3600?'minute':Math.abs(seconds)<86400?'hour':'day';
  return relativeDate.format(Math.round(seconds/({second:1,minute:60,hour:3600,day:86400}[unit])),unit);
}
function repositoryPath(directory='',file=''){
  repoDirectory=directory;repoFile=file;repoGeneration++;repoSignature='';loadRepository();
}
function renderReadme(content){
  const fragment=document.createDocumentFragment();let code=null;
  function inline(parent,text){
    let end=0;
    for(const match of text.matchAll(/\*\*([^*\n]+)\*\*|`([^`\n]+)`/g)){
      parent.append(document.createTextNode(text.slice(end,match.index)));
      parent.append(element(match[1]?'strong':'code','',match[1]||match[2]));end=match.index+match[0].length;
    }
    parent.append(document.createTextNode(text.slice(end)));return parent;
  }
  for(const line of content.split('\n')){
    if(line.startsWith('```')){if(code)code=null;else{code=element('pre','readme-code','');fragment.append(code);}continue;}
    if(code){code.textContent+=line+'\n';continue;}
    const heading=line.match(/^(#{1,6})\s+(.+)/);
    if(heading)fragment.append(inline(element('h'+heading[1].length),heading[2]));
    else if(line.trim())fragment.append(inline(element('p',/^\s*[-*]\s/.test(line)?'readme-item':''),line));
  }
  return fragment;
}
async function loadRepository(){
  if(repoLoading)return;
  repoLoading=true;const version=projectVersion,generation=repoGeneration;
  try{
    const query=new URLSearchParams({directory:repoDirectory,file:repoFile,branch:repoBranch});
    const data=await api('/api/repository?'+query);
    if(version!==projectVersion||generation!==repoGeneration)return;
    $('repo-feedback').textContent=data.truncated?'Affichage limité à 1 000 entrées.':'';
    const signature=JSON.stringify([data.branch,data.branches,data.local,data.entries,data.file,data.readme,repoDirectory]);
    if(signature===repoSignature){document.querySelectorAll('.repo-age').forEach(node=>node.textContent=repoTime(Number(node.dataset.timestamp)));return;}
    repoSignature=signature;
    $('repo-mode').textContent=data.local?'Dossier local · en direct':'Lecture seule';
    const branches=data.branches.includes(data.branch)?data.branches:[data.branch,...data.branches];
    $('repo-branch').replaceChildren(...branches.map(name=>{const option=element('option','',name);option.value=name;return option;}));
    $('repo-branch').value=data.selected_branch||data.branch;
    const breadcrumb=$('repo-breadcrumb');breadcrumb.replaceChildren();
    const root=element('button','',state?.project?.name||'Projet');root.addEventListener('click',()=>repositoryPath());breadcrumb.append(root);
    let path='';for(const part of repoDirectory.split('/').filter(Boolean)){
      path+=(path?'/':'')+part;const target=path;breadcrumb.append(element('span','','/'));
      const button=element('button','',part);button.addEventListener('click',()=>repositoryPath(target));breadcrumb.append(button);
    }
    if(repoFile)breadcrumb.append(element('span','','/ '+repoFile.split('/').pop()));
    const entries=$('repo-entries');entries.replaceChildren();
    if(repoDirectory){const up=element('button','repo-row repo-up','↑ ..');up.addEventListener('click',()=>repositoryPath(repoDirectory.split('/').slice(0,-1).join('/')));entries.append(up);}
    for(const item of data.entries){
      const row=element('button','repo-row'+(item.path===repoFile?' selected':''));row.title=item.path;
      const name=element('span','repo-name');name.append(element('span','repo-icon',item.kind==='folder'?'▰':'▤'),element('span','',item.name));
      const age=element('time','repo-age',repoTime(item.time));age.dataset.timestamp=item.time||'';
      if(item.time){age.dateTime=new Date(item.time*1000).toISOString();age.title=new Date(item.time*1000).toLocaleString('fr-FR');}
      row.append(name,element('span','repo-message'+(item.modified?' modified':''),item.message||'—'),age);
      row.addEventListener('click',()=>item.kind==='folder'?repositoryPath(item.path):repositoryPath(repoDirectory,item.path));entries.append(row);
    }
    if(!data.entries.length)entries.append(element('p','empty-state','Aucun fichier à afficher.'));
    const preview=data.file||data.readme;$('repo-preview').classList.toggle('hidden',!preview);
    $('repo-preview-title').textContent=preview?.path||'';$('repo-content').replaceChildren();
    if(preview){
      if(preview.unavailable)$('repo-content').append(element('p','empty-state',preview.unavailable));
      else if(!data.file||/\.(md|rst)$/i.test(preview.path)){const readme=element('div','repo-readme');readme.append(renderReadme(preview.content));$('repo-content').append(readme);}
      else{const code=element('pre','repo-code');code.append(element('code','',preview.content));$('repo-content').append(code);}
    }
  }catch(error){if(version===projectVersion&&generation===repoGeneration){$('repo-feedback').textContent='Fichiers indisponibles. Réessayez.';repoSignature='';}}
  finally{repoLoading=false;if(version!==projectVersion||generation!==repoGeneration){if(currentView==='files')loadRepository();}}
}
$('repo-branch').addEventListener('change',()=>{repoBranch=$('repo-branch').value;repositoryPath();});
$('repo-refresh').addEventListener('click',()=>{repoSignature='';loadRepository();});
setInterval(()=>{if(!document.hidden&&currentView==='files'&&!$('workspace').classList.contains('hidden'))loadRepository();},4000);

$('download-zip').addEventListener('click',async()=>{
  const button=$('download-zip'),project=activeProject,version=projectVersion;button.disabled=true;button.textContent='Préparation…';
  try{
    const response=await fetch('/api/export'+(project?'?project='+encodeURIComponent(project):''),{cache:'no-store'});
    if(!response.ok){const result=await response.json();throw new Error(result.error||'export_unavailable');}
    const blob=await response.blob();const url=URL.createObjectURL(blob);
    const link=element('a');link.href=url;link.download=response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1]||'projet.zip';
    document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
    if(version===projectVersion)toast('ZIP téléchargé.');
  }catch(error){
    const reasons={export_source_busy:'Le code est en cours de modification. Réessayez dans quelques secondes.',export_size_limit:'Projet trop volumineux pour cet export.',export_empty:'Aucun fichier à exporter.'};
    if(version===projectVersion)toast(reasons[error.message]||'Téléchargement impossible. Réessayez.');
  }finally{button.disabled=false;button.textContent='Télécharger ZIP ↓';}
});
