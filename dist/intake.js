'use strict';
// One intake path for Finder drops, browser selection and zero-copy local paths.
const batchState={active:false,cancelled:false,controller:null,message:'',bytes:0,total:0,id:null,projectId:null};
const batchMedia=/\.(mov|mp4|mkv|m4v|wav|mp3|m4a|aac|flac|jpg|jpeg|png)$/i;
const batchScripts=/\.(docx|txt|md)$/i;
function batchSize(n){return n>=1024**3?(n/1024**3).toFixed(2)+' GB':(n/1024**2).toFixed(1)+' MB';}
function currentScriptPreparation(p){
 const prepared=p.scriptPreparation;
 return prepared?.revision==='script-normalize-4'&&prepared.sourceText===(p.script||'')?prepared:null;
}
function scriptPreparationView(p){
 const prepared=currentScriptPreparation(p);
 if(p.scriptChoicePending)return '<p class="subtle">选择本次脚本版本后，会自动整理其中的台词。</p>';
 if(!prepared)return `<p class="subtle">${p.script?'原剧本已保留；开始粗剪时自动整理台词，无需手动改格式。':'请把含人物台词的剧本与素材一起导入。'}</p>`;
 const shots=prepared.shotRequirements||[];
 const status=prepared.strictReady?`已整理 ${Number(prepared.count)||0} 条台词、${shots.length} 项镜头要求，可以开始按剧本粗剪。`:'没有找到明确台词，请检查是否导入了本条视频的剧本；无需手动改格式。';
 return `<p class="${prepared.strictReady?'subtle':'batch-warnings'}">${esc(status)}</p>${prepared.warnings?.length?`<p class="subtle">${prepared.warnings.map(esc).join('<br>')}</p>`:''}${prepared.count?`<details class="batch-script"><summary>查看自动整理的台词 · ${Number(prepared.count)||0} 条</summary><label class="mini-label" for="prepared-script">用于匹配素材的台词 · 自动整理，无需确认</label><textarea id="prepared-script" rows="6" readonly>${esc(prepared.normalizedText||'')}</textarea><p class="subtle">按原文提取，不补写台词；这是选片依据，不代表素材里一定说过这些话。</p></details>`:''}${shots.length?`<details class="batch-script"><summary>查看脚本镜头要求 · ${shots.length} 项</summary>${shots.map(s=>`<p><strong>${esc(s.scene||'镜头')}</strong> · ${esc(s.text)}</p>`).join('')}<p class="subtle">没有找到或不能安全放入的镜头，会列入缺项清单，不用无关画面替代。</p></details>`:''}`;
}
function renderScriptPreparation(p){
 if(project().id!==p.id)return;
 const box=$('#batch-script-preparation');if(box)box.innerHTML=scriptPreparationView(p);
}
async function prepareBatchScript(p){
 let prepared=currentScriptPreparation(p);
 if(prepared)return prepared;
 const text=p.script||'';
 const box=project().id===p.id?$('#batch-script-preparation'):null;
 if(box)box.textContent='正在从原脚本自动整理台词…';
 try{
  prepared=await api('/api/intake/prepare',{text});
  if((p.script||'')!==text)throw Error('整理期间脚本发生了变化，请再次点击开始；无需手动改格式。');
  if(prepared?.revision!=='script-normalize-4'||prepared.sourceText!==text||!Array.isArray(prepared.targets)||!Array.isArray(prepared.shotRequirements)||typeof prepared.strictReady!=='boolean')throw Error('脚本整理结果不完整，请检查本机服务后重试。');
  p.scriptPreparation=prepared;save();renderScriptPreparation(p);return prepared;
 }catch(error){
  p.scriptPreparation=null;save();
  if(box)box.textContent='台词自动整理未完成：'+error.message;
  throw error;
 }
}
function batchEntry(p){
 const scripts=p.intakeScripts||[];
 return `<div class="batch-drop" id="batch-drop" tabindex="0" role="group" aria-label="将本次拍摄文件夹拖入这里，或选择文件夹"><strong>把整个文件夹拖到这里</strong><p>人物、食物、脚本，一起放入 · 包含子文件夹</p><button class="secondary" data-batch="folder" ${batchState.active?'disabled':''}>选择文件夹</button><button class="secondary" data-batch="files" ${batchState.active?'disabled':''}>选择多个文件</button><input id="batch-folder" type="file" webkitdirectory multiple hidden><input id="batch-files" type="file" multiple accept=".mov,.mp4,.mkv,.m4v,.wav,.mp3,.m4a,.aac,.flac,.jpg,.jpeg,.png,.docx,.txt,.md" hidden></div>
 <p class="batch-note">脚本支持 Word（.docx）、TXT、Markdown。拖放／选择会保存本机副本，不传互联网；大批素材可用下方路径读取，避免重复占空间。</p><div id="batch-progress" role="status" aria-live="polite"></div>
 <div class="batch-status"><p id="quick-assets">${p.quickAssetIds?.length?`已读取 ${p.quickAssetIds.length} 个素材`:'尚未读取本批素材'}</p><p>脚本：${esc(p.scriptChoicePending?'多份候选，待选择':p.intakeScriptName||(p.script?'已填写':'未提供，请导入剧本'))}</p></div>
 ${p.intakeWarnings?.length?`<div class="batch-warnings">${p.intakeWarnings.map(esc).join('<br>')}</div>`:''}
 ${scripts.length>1?`<label class="mini-label" for="batch-script-choice">选择本次脚本</label><select id="batch-script-choice"><option value="" ${p.scriptChoicePending?'selected':''} disabled>请选择版本</option>${scripts.map(s=>`<option value="${esc(s.id)}" ${p.intakeScriptId===s.id?'selected':''}>${esc(s.name)}</option>`).join('')}<option value="none" ${p.intakeScriptId==='none'?'selected':''}>稍后补充剧本（暂不能开始）</option></select>`:''}
 <details class="batch-paths"><summary>路径读取 · 不复制文件</summary><label for="quick-paths" class="mini-label">文件夹或文件路径 · 每行一个</label><textarea id="quick-paths" rows="3" placeholder="/Users/你的名字/Movies/本次拍摄">${esc(p.quickPaths||'')}</textarea><p class="subtle">Finder 中按 ⌥⌘C 复制路径。脚本放在同一目录也会自动读取；剪辑期间不要移动原文件。</p><button class="secondary" data-quick="register">读取文件夹与脚本</button></details>
 <div id="batch-script-preparation" role="status" aria-live="polite">${scriptPreparationView(p)}</div>
 <details class="batch-script"><summary>查看原脚本 · 可选修改 ${p.script?'· '+p.script.length+' 字':''}</summary><label class="mini-label" for="quick-script">原脚本内容 · 保留原文，系统自动整理台词</label><textarea id="quick-script" rows="7" placeholder="可直接粘贴原脚本，不需要统一人物名称或台词格式。">${esc(p.script||'')}</textarea><p class="subtle">无需手动改格式。修改原文后，下次开始粗剪会自动重新整理。扫描件、旧版 .doc 和 PDF 暂不支持。</p></details>`;
}
function applyBatch(p,r){
 if(!r.assets.length&&!r.scripts.length)throw Error('没有找到可用素材或脚本。'+r.warnings.join('；'));
 if(p.script)p.intakeScriptHistory=[{name:p.intakeScriptName||'之前的脚本',text:p.script,at:new Date().toISOString()},...(p.intakeScriptHistory||[])].slice(0,3);
 if(r.assets.length){
  p.quickAssetIds=[...new Set(r.assets.map(a=>a.id))];
  for(const a of r.assets){const old=p.assets.find(x=>x.localId===a.id);if(old)Object.assign(old,{path:a.path});else p.assets.push({...a,id:uid(),localId:a.id,kind:'待分类',group:'',dish:''});}
 }
 p.intakeScripts=r.scripts;p.intakeScriptId=r.selectedScriptId||'';
 const selected=r.scripts.find(s=>s.id===r.selectedScriptId);
 p.script=selected?.text||'';p.intakeScriptName=selected?.name||'';p.scriptPreparation=selected?.preparation||null;
 p.scriptChoicePending=!!r.scripts.length&&!selected;
 p.intakeWarnings=[...r.warnings];p.intakeMode=r.mode;p.intakeImportId=r.importId||null;
 if(r.skipped)p.intakeWarnings.push(`已跳过 ${r.skipped} 个隐藏或不支持的文件。`);
 if(!batchState.active)Object.assign(batchState,{projectId:p.id,message:`读取完成：${r.assets.length} 个素材、${r.scripts.length} 份脚本。`,bytes:0,total:0,id:null});
 save();
}
function batchProgress(){
 const el=$('#batch-progress');if(!el)return;
 if(batchState.projectId!==project().id){el.innerHTML='';return;}
 el.innerHTML=batchState.message?`${esc(batchState.message)}${batchState.total?`<progress max="${batchState.total}" value="${batchState.bytes}"></progress><span>${batchSize(batchState.bytes)} / ${batchSize(batchState.total)}</span>`:''}${batchState.active?'<button class="text-button" data-batch="cancel">停止本次导入</button>':''}`:'';
}
function batchCheck(){if(batchState.cancelled)throw Error('已停止导入。未交给剪辑引擎；已复制部分留在本机导入目录，原文件未改动。');}
function batchFilter(rows){
 const accepted=[],seen=new Set();let skipped=0;
 for(const row of rows){
  if(row.path.split('/').some(p=>p.startsWith('.')||p.startsWith('~$'))||(!batchMedia.test(row.path)&&!batchScripts.test(row.path))){skipped++;continue;}
  if(!row.file.size)throw Error('文件为空：'+row.path);
  const key=row.path.normalize('NFC').toLowerCase();if(seen.has(key))throw Error('同一批有重名路径，请放进不同子文件夹：'+row.path);seen.add(key);
  accepted.push(row);if(accepted.length>1020)throw Error('本批最多 1000 个素材和 20 份脚本，请缩小文件夹范围');
 }
 return {rows:accepted,skipped};
}
async function batchEntries(entries){
 const rows=[];let visited=0;
 async function walk(entry,parent='',depth=0){
  batchCheck();if(++visited>100000||depth>50)throw Error('文件夹内容过多或嵌套过深，请缩小选择范围');
  if(entry.name.startsWith('.')||entry.name.startsWith('~$'))return;
  const path=parent+entry.name;
  if(entry.isFile){if(batchMedia.test(path)||batchScripts.test(path)){const file=await new Promise((ok,bad)=>entry.file(ok,bad));rows.push({file,path});if(rows.length>1020)throw Error('文件超过 1020 个，请缩小选择范围');}}
  else if(entry.isDirectory){const reader=entry.createReader();while(true){const children=await new Promise((ok,bad)=>reader.readEntries(ok,bad));if(!children.length)break;for(const child of children)await walk(child,path+'/',depth+1);}}
 }
 for(const entry of entries)await walk(entry);
 return rows;
}
async function batchRequest(path,body,binary=false){
 batchCheck();const controller=batchState.controller=new AbortController();
 const timer=setTimeout(()=>controller.abort(),60000);
 try{
  const response=await fetch(engine.url+path,{method:'POST',headers:{Authorization:'Bearer '+engine.token,'Content-Type':binary?'application/octet-stream':'application/json'},body:binary?body:JSON.stringify(body),signal:controller.signal});
  const result=await response.json();if(!response.ok)throw Error(result.error||'本机导入失败');return result;
 }catch(error){batchCheck();if(error.name==='AbortError')throw Error('本机服务响应超时，请检查启动窗口');throw error;}
 finally{clearTimeout(timer);if(batchState.controller===controller)batchState.controller=null;}
}
async function importBatch(getRows){
 if(quick.busy||batchState.active)return toast('当前操作尚未结束，请稍后再导入');
 if(!isLocal)return toast('请从本机启动器打开 http://127.0.0.1:8765 后导入');
 const target=project();quick.busy=true;Object.assign(batchState,{active:true,cancelled:false,message:'正在读取文件夹…',bytes:0,total:0,id:null,projectId:target.id});batchProgress();renderQuickProgress();
 try{
  if(!engine.status)await connectEngine();batchCheck();
  const {rows,skipped}=batchFilter(await getRows());batchCheck();
  if(!rows.length)throw Error('未发现支持的素材或 DOCX/TXT/MD 脚本');
  const start=await batchRequest('/api/intake/begin',{files:rows.map(r=>({path:r.path,size:r.file.size}))});
  batchState.id=start.id;batchState.total=start.totalBytes;
  let completed=0;
  for(let index=0;index<rows.length;index++){
   const row=rows[index];let offset=0,failures=0;
   while(offset<row.file.size){
    batchCheck();batchState.message=`本机复制 ${index+1}/${rows.length} · ${row.path}`;batchProgress();
    try{const r=await batchRequest(`/api/intake/chunk?id=${start.id}&index=${index}&offset=${offset}`,row.file.slice(offset,offset+start.chunkBytes),true);offset=r.offset;failures=0;}
    catch(error){batchCheck();if(++failures>2)throw error;const state=await batchRequest('/api/intake/status',{id:start.id});offset=state.offsets[index];}
    batchState.bytes=completed+offset;batchProgress();
   }
   completed+=row.file.size;
  }
  batchCheck();batchState.message='复制完成，正在识别脚本并登记素材…';batchProgress();
  const result=await batchRequest('/api/intake/finish',{id:start.id});batchCheck();result.skipped+=skipped;
  applyBatch(target,result);batchState.message=`导入完成：${result.assets.length} 个素材、${result.scripts.length} 份脚本。${target.scriptChoicePending?'请选定脚本版本。':'可以开始粗剪。'}`;
  toast(batchState.message);
 }catch(error){batchState.message=error.message+(batchState.id&&!batchState.cancelled?'；未完成批次不会进入粗剪，部分副本保留在本机 .local/imports。':'');toast(batchState.message);}
 finally{batchState.active=false;quick.busy=false;batchState.controller=null;render();batchProgress();renderQuickProgress();}
}
document.addEventListener('click',e=>{
 const b=e.target.closest('[data-batch]');if(!b)return;
 if(b.dataset.batch==='cancel'){batchState.cancelled=true;batchState.controller?.abort();batchState.message='正在停止导入…';batchProgress();}
 else if(!batchState.active)$('#batch-'+b.dataset.batch)?.click();
});
document.addEventListener('keydown',e=>{if(e.target.id==='batch-drop'&&['Enter',' '].includes(e.key)){e.preventDefault();if(!batchState.active)$('#batch-folder').click();}});
document.addEventListener('change',e=>{
 if(['batch-folder','batch-files'].includes(e.target.id)){const rows=Array.from(e.target.files).map(file=>({file,path:file.webkitRelativePath||file.name}));e.target.value='';if(rows.length)void importBatch(()=>rows);}
 if(e.target.id==='batch-script-choice'){
  const p=project(),id=e.target.value,s=p.intakeScripts.find(s=>s.id===id);p.intakeScriptId=id;p.script=s?.text||'';p.intakeScriptName=s?.name||'未选择，请补充剧本';p.scriptPreparation=s?.preparation||null;p.scriptChoicePending=false;p.intakeWarnings=p.intakeWarnings.filter(x=>!x.startsWith('发现多份脚本'));save();render();
 }
});
function batchDropTarget(e){return e.target.closest?.('#batch-drop,#quick-paths,#drop');}
document.addEventListener('dragover',e=>{const zone=batchDropTarget(e);if(zone){e.preventDefault();e.stopImmediatePropagation();e.dataTransfer.dropEffect='copy';zone.classList.add('dragover');}},true);
document.addEventListener('dragleave',e=>{const zone=batchDropTarget(e);if(zone&&!zone.contains(e.relatedTarget))zone.classList.remove('dragover');},true);
document.addEventListener('drop',e=>{
 const zone=batchDropTarget(e);if(!zone)return;e.preventDefault();e.stopImmediatePropagation();zone.classList.remove('dragover');
 // Snapshot entries during the drop event: access is lost after the first await.
 const entries=Array.from(e.dataTransfer.items||[]).filter(i=>i.kind==='file').map(i=>i.getAsEntry?.()||i.webkitGetAsEntry?.()).filter(Boolean);
 const files=Array.from(e.dataTransfer.files||[]);
 if(entries.length)return void importBatch(()=>batchEntries(entries));
 if(files.length)return void importBatch(()=>files.map(file=>({file,path:file.webkitRelativePath||file.name})));
 if(zone.id==='quick-paths'){
  try{const text=e.dataTransfer.getData('text/uri-list')||e.dataTransfer.getData('text/plain');zone.value=text.split(/\r?\n/).filter(x=>x&&!x.startsWith('#')).map(x=>x.startsWith('file://')?decodeURIComponent(new URL(x).pathname):x).join('\n');zone.dispatchEvent(new Event('input',{bubbles:true}));}catch{toast('无法读取路径，请直接选择文件夹');}
 }else toast('未收到文件，请从 Finder 拖入，或点「选择文件夹」');
},true);
// The advanced media page uses the same real import instead of preview-only Files.
importMedia=function(files){const rows=Array.from(files).map(file=>({file,path:file.webkitRelativePath||file.name}));return importBatch(()=>rows);};
const batchPreviousProgress=renderQuickProgress;
renderQuickProgress=function(){batchPreviousProgress();if(project().scriptChoicePending){const b=$('[data-quick="start"]');if(b)b.disabled=true;}batchProgress();};
const batchPreviousRender=render;
render=function(){batchPreviousRender();batchProgress();};
$('#help').onclick=()=>openForm('三步剪辑怎么用','<ol class="help-list"><li>把原脚本和素材放在同一个文件夹，整体拖到第一步，或点「选择文件夹」。Word（.docx）、TXT、Markdown 中的台词会自动整理，不用手动改格式。多份候选时请选择脚本版本。</li><li>点击「开始夜间粗剪」。系统自动整理剧本、按顺序选台词、去掉重复试说，并匹配脚本画面。</li><li>完成后生成剪映草稿并请求启动剪映，打开新草稿继续审片和包装。</li></ol><p>只有剧情大纲、没有明确台词时，会提示补充含台词的剧本，不会把全部口播放进草稿。拖入会保留本机副本，大批素材可用路径读取；原文件不改动，文稿与媒体不传互联网。</p>',()=>{});
render();
