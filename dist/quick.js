'use strict';
// A simple default entry; all existing editing tools and records remain available.
const advancedEngines=engines;
const quick={busy:false,assets:[],paths:'',advanced:false,error:'',submitting:false};
const editModes={script:{label:'按剧本剪辑',hint:'按剧本顺序选取完整台词，每句只留一遍；剔除重复试说、拍摄指令和无关口播。表情与笑声围绕入选台词保留，找不到的台词或画面会列出。'}};
function quickEditMode(){return 'script';}
function quickModeChoice(){return `<div class="field quick-script-rule"><strong>按剧本剪辑</strong><p class="subtle">${editModes.script.hint}</p></div>`;}
descriptions.overview=descriptions.engines=['晚上放素材，早晨接着剪。','一次启动，连续分析、粗剪和导出；不再逐批等待声音确认。素材留在本机。'];
pages.find(p=>p[0]==='overview')[2]='三步剪辑';
pages.find(p=>p[0]==='engines')[2]='三步剪辑';
function quickJob(){return engine.jobs.filter(j=>j.kind==='workflow'&&j.projectId===project().id).sort((a,b)=>b.created-a.created)[0];}
function quickFlow(){
 const j=quickJob(),s=engine.status,p=project();
 return `<div class="quick-health"><span class="status-dot"></span>${s?'本机服务已连接':'等待本机服务'} <span>／</span> 达芬奇 ${s?.resolve?.connected?'已连通':'待连接'} <span>／</span> 剪映 ${s?.jianying?.available?'就绪':'待检测'}${!isLocal?'<a href="http://127.0.0.1:8765/#engines" target="_blank" rel="noopener">打开本机执行版 ↗</a>':''}</div>
 <div class="quick-steps">
 <section class="panel quick-step"><div class="quick-step-head"><span class="quick-number">01</span><div><h2>放入本次拍摄文件夹</h2><p>脚本和素材放一起，自动区分</p></div></div>
 <div class="panel-pad">${typeof batchEntry==='function'?batchEntry(p):'<p>正在加载导入入口…</p>'}</div></section>
 <section class="panel quick-step"><div class="quick-step-head"><span class="quick-number">02</span><div><h2>开始达芬奇粗剪</h2><p>转写纠错 → 说话人分段 → 切点多帧复核</p></div></div><div class="panel-pad">${quickModeChoice(p)}<p>按剧本安排主线和补画面，复核完整台词的前后切点。新建独立项目。</p><button class="primary quick-start" data-quick="start" ${!s||!p.quickAssetIds?.length||['queued','running'].includes(j?.status)?'disabled':''}>开始夜间粗剪</button><p class="subtle">${s?.voiceGuard?.speakerReady&&s?.voiceGuard?.senseVoiceReady?'本地声纹＋笑声候选检测已就绪。':'声音引擎未就绪时，保守保留原声并提示。'} 目标 3 分钟不是硬截断；脚本本身过长或反应较多时仍会提示超时。未知/混合人声不按编导删除，未配置 pyannote 重叠检测。</p><div class="alert warm">开始前请保存达芬奇当前工作。接电、保持开盖，不退出本机服务或切换达芬奇项目；任务期间自动防空闲休眠。</div></div></section>
 <section class="panel quick-step"><div class="quick-step-head"><span class="quick-number">03</span><div><h2>自动打开剪映草稿</h2><p>保留多轨与切点，直接开始精剪</p></div></div><div class="panel-pad"><p>自动导出独立草稿，由已授权的本机助手打开；核对进入编辑页后才显示成功。</p><div id="quick-finish"></div><p class="subtle">夜间开始前让剪映停在首页或关闭。若正在编辑别的项目，助手会提示等待，不强退或覆盖。首次需手动授权辅助功能。</p></div></section></div>
 <section class="panel quick-progress"><div class="panel-head"><h2>本次执行</h2><button class="text-button" data-engine="refresh">刷新状态</button></div><div class="panel-pad" id="quick-progress" role="status"></div></section>
 <details class="quick-options"><summary>高级设置 <span>模型、画幅、逐句审核与手动操作</span></summary><div class="panel-pad"><div class="fields-row"><div class="field"><label for="quick-model">本地语音模型</label><select id="quick-model">${['small','base','tiny'].map(m=>`<option ${m===(p.quickModel||'small')?'selected':''}>${m}</option>`).join('')}</select></div><div class="field"><label for="quick-vision">本地视觉模型</label><select id="quick-vision">${(s?.localModels||[]).map(m=>`<option ${m===(p.quickVision||'qwen3-vl:2b-instruct')?'selected':''}>${esc(m)}</option>`).join('')}</select></div><div class="field"><label for="quick-layout">画幅</label><select id="quick-layout"><option value="vertical" ${p.quickLayout!=='horizontal'?'selected':''}>竖屏 1080 × 1920</option><option value="horizontal" ${p.quickLayout==='horizontal'?'selected':''}>横屏 1920 × 1080</option></select></div></div><p class="subtle">模型首次安装、单独分析、人工挑选片段或重新导出已有时间线，使用下方高级执行面板。花字、调色、转场、关键帧与混音插件不保证跨软件迁移。</p>${pages.filter(x=>!['overview','engines'].includes(x[0])).map(x=>`<a class="secondary" href="#${x[0]}">${x[2]}</a>`).join(' ')}<details class="quick-legacy"><summary>展开原执行面板与任务记录</summary>${advancedEngines()}</details></div></details>`;
}
overview=engines=quickFlow;
function renderQuickProgress(){
 const box=$('#quick-progress');if(!box)return;
 const j=quickJob(),done=j?.status==='succeeded',stage=j?.progress?.stage;
 const fail=['failed','interrupted'].includes(j?.status);
 const startButton=$('[data-quick="start"]');if(startButton)startButton.disabled=quick.busy||!engine.status||!project().quickAssetIds?.length||['queued','running'].includes(j?.status);
 const signature=JSON.stringify([j?.id,j?.status,j?.message,j?.progress?.stage,j?.result]);if(box.dataset.signature===signature)return;
 box.dataset.signature=signature;
 const detailsOpen=box.querySelector('details')?.open;
 box.innerHTML=j?`<div class="quick-stage"><span class="${stage?'current':''}">素材分析</span><i>→</i><span class="${['resolve','jianying','done'].includes(stage)?'current':''}">达芬奇粗剪</span><i>→</i><span class="${['jianying','done'].includes(stage)?'current':''}">剪映草稿</span></div><p><strong>${esc(jobNames[j.status]||j.status)}</strong> · ${esc(j.message)}</p>${fail?`<div class="alert warm">流程已停止，没有把失败标成完成。分析阶段可继续失败项；达芬奇写入结果不明时会停止重试，需先检查独立项目。</div><button class="secondary" data-quick="retry" data-id="${esc(j.id)}">继续本次任务</button>`:''}${done?`<p>分析 ${j.result.analysisCount} 个素材 · 主线 ${j.result.speechClips} 段 · 第二轨补画面 ${j.result.brollClips??j.result.foodClips??0} 段 · ${tc(j.result.duration)}</p><p class="subtle">${esc(j.result.resolve.project)}</p>`:''}<details><summary>查看执行详情与边界</summary><pre class="engine-result">${esc(JSON.stringify(j.result||j.progress,null,2))}</pre></details>`:'读取素材后，点击「一键开始粗剪」。运行时可以使用其他页面；请勿退出本机服务或切换达芬奇项目。';
 const finish=$('#quick-finish');if(finish)finish.innerHTML=done?`<strong>${esc(j.result.draft.draftName)}</strong><p>${j.result.draft.clipCount} 个片段 · ${j.result.draft.tracks.length} 条轨道</p><p class="${j.result.draft.draftOpened?'':'alert warm'}">${j.result.draft.draftOpened?'已核对剪映编辑页 · 可以开始精剪':esc(j.result.draft.note||'草稿已保存，等待打开')}</p><button class="secondary" data-engine="jy-open" data-id="${esc(j.id)}">打开本次草稿</button>${j.result.unattended?`<button class="secondary" data-quick="review" data-id="${esc(j.id)}">早晨复核 · ${j.result.morningReviewCount} 处</button><p class="subtle">脚本未完整匹配 ${j.result.missingScriptCount} 条（含部分匹配） · 自动去指导语 ${j.result.removedCommands} 段。${j.result.duration>180?'超过 3 分钟目标，请先看复核清单。':''}</p>`:''}<p class="subtle">草稿可编辑不等于内容已通过审片；请继续核对台词、反应和补画面。</p>`:'<div class="quick-wait">等待粗剪完成 · 自动执行</div>';
 if(done&&j.result.scriptCoverage&&finish)finish.insertAdjacentHTML('beforeend',scriptCoveragePanel(j.result.scriptCoverage,false));
 const details=box.querySelector('details');if(details){details.open=!!detailsOpen;if(done)details.querySelector('summary').insertAdjacentHTML('afterend',`<button class="text-button" data-quick="adopt" data-id="${esc(j.id)}">导入本次分析，进入逐句审核</button>`);}
 if(j){const mode=j.result?.editMode||j.editMode;box.insertAdjacentHTML('beforeend',`<p class="subtle">本次剪辑：${mode&&editModes[mode]?editModes[mode].label:'历史草稿'}${done&&j.result.policyLimitedClips?` · ${j.result.policyLimitedClips} 段已限制额外留量，请检查反应切点`:''}</p>`);}
 if(done&&['night-2','night-3'].includes(j.result.editorialRevision))box.insertAdjacentHTML('beforeend',`<p>转写纠错 ${j.result.asrCorrections} 处 · 时间校正 ${j.result.asrTimingRepairs||0} 处 · 语音分段 ${j.result.speakerTurns} 段 · 切点复核 ${j.result.boundaryReviewedClips} 段 / ${j.result.boundaryFrames} 帧 · 延长反应 ${j.result.reactionExtendedClips} 段</p><p class="subtle">${j.result.asrDisagreements} 处转写分歧保留待听审；这些是整批处理数量，不是准确率，也不等于成片中删掉了多少编导。</p>`);
 if(details&&(done||fail))details.querySelector('summary').insertAdjacentHTML('afterend',`<button class="text-button" data-quick="rebuild" data-id="${esc(j.id)}">复用本次分析，重新生成独立初剪</button>`);
 const start=$('[data-quick="start"]');if(start)start.disabled=quick.busy||!engine.status||!project().quickAssetIds?.length||['queued','running'].includes(j?.status);
}
function scriptCoveragePanel(coverage,full=true){
 if(!coverage)return '';
 const d=coverage.dialogue||{},s=coverage.shots||{},pending=(d.partial||0)+(d.missing||0)+(s.unresolved||0);
 const labels={complete:'整句候选',partial:'部分匹配',missing:'未匹配','covered-main':'主线覆盖候选',inserted:'补充镜头候选',unmatched:'未找到',uncertain:'无法确认',conflict:'不能安全放入'};
 const placement=p=>{if(!p)return '';const track=p.track===2?'第二轨':p.track===1?'主线':'已用片段';return `<small>${track}${p.existing?'（已有片段，未新增）':''}${Number.isFinite(p.at)?` · 草稿 ${p.at.toFixed(2)}s`:''}</small>`;};
 const components=x=>{
  const parts=x.componentCoverage||[],lookup=new Map(parts.map(c=>[c.id,c]));
  const rest=(x.remainingComponents||[]).map(c=>typeof c==='string'?(lookup.get(c)||{text:c}):c);
  const used=x.placements||[];
  return `${parts.length?`<div><strong>画面要素进度</strong><ul>${parts.map(c=>`<li>${esc(c.status==='inserted'?'已补入第二轨（候选）':labels[c.status]||'待核对')} · ${esc(c.text||'')}<br><small>${esc(c.reason||'')}</small>${placement(c.placement)}</li>`).join('')}</ul></div>`:''}${rest.length?`<div><strong>仍缺内容（该要求未完整完成）</strong><ul>${rest.map(c=>`<li>${esc(c.text||'')} · ${esc(c.reason||'待复核')}</li>`).join('')}</ul></div>`:''}${x.nonVisualObligations?.length?`<div><strong>非视觉义务 · 需听审／后期核对</strong><ul>${x.nonVisualObligations.map(c=>`<li>${esc(typeof c==='string'?c:c.text||'')}${c.reason?` · ${esc(c.reason)}`:''} · 不以画面代替完成</li>`).join('')}</ul></div>`:''}${used.length?`<p class="subtle">本项实际使用第二轨 ${used.length} 段，其中新增 ${used.filter(p=>!p.existing).length} 段；不据此认定整项完成。</p>`:''}`;
 };
 const row=x=>`<div><p><strong>${esc(labels[x.status]||'待核对')}</strong> · ${esc(x.scene||'')} ${esc(x.text||'')}<br><small>${esc(x.reason||'')}</small></p>${components(x)}</div>`;
 const physical=Number.isInteger(s.physicalInserts)&&s.physicalInserts>=0?`<p>实际新增第二轨 ${s.physicalInserts} 段（不等于完成要求 ${s.physicalInserts} 项；部分匹配也可能已补入可用画面）。</p>`:'';
 const conflicts=coverage.scriptConflicts||[];
 const conflictPanel=conflicts.length?`<section class="alert warm"><h3>脚本数值冲突 · 请核对原稿</h3><p>只提示，不自动选值；不改变本次台词或镜头计数。</p>${conflicts.map(c=>`<p><strong>${esc(c.scene||'')} · ${esc(c.field||'')}</strong><br>${esc(c.reason||'')}<br>台词原句：${esc(c.dialogue?.text||'')}（数值 ${esc(c.dialogue?.value||'')}）<br>画面原句：${esc(c.shot?.text||'')}（数值 ${esc(c.shot?.value||'')}）</p>`).join('')}</section>`:'';
 return `<section class="${pending?'alert warm':'alert'}"><strong>${pending?'草稿已生成，但脚本仍有缺项／部分匹配':'脚本候选已覆盖，仍需审片'}</strong><p>台词 ${Number(d.required)||0} 条：整句候选 ${Number(d.complete)||0} · 部分匹配 ${Number(d.partial)||0} · 未匹配 ${Number(d.missing)||0}</p><p>镜头 ${Number(s.required)||0} 项：主线覆盖 ${Number(s.coveredMain)||0} · 已补充 ${Number(s.inserted)||0} · 待解决 ${Number(s.unresolved)||0}</p>${physical}<p class="subtle">未匹配不代表原片没拍到。缺项会明确列出，不用无关镜头填满清单。</p>${full?`<h3>需处理的台词</h3>${(d.items||[]).filter(x=>x.status!=='complete').map(row).join('')||'<p>没有未解决项；整句候选仍需听审。</p>'}<h3>需处理的镜头</h3>${(s.items||[]).filter(x=>!['covered-main','inserted'].includes(x.status)).map(row).join('')||'<p>没有未解决项；镜头候选仍需审片。</p>'}<details><summary>查看全部脚本内容与匹配状态</summary>${(d.items||[]).map(row).join('')}${(s.items||[]).map(row).join('')}</details>${conflictPanel}${coverage.excludedContent?.length?`<details><summary>非同期声内容 · 配音／花字等</summary>${coverage.excludedContent.map(x=>`<p>${esc(typeof x==='string'?x:x.text||'')} · 未假装在原片找到</p>`).join('')}</details>`:''}`:'<p>点击「早晨复核」查看逐项原因与完整清单。</p>'}</section>`;
}
const quickPreviousRender=render;
render=function(){quickPreviousRender();const simple=['overview','engines'].includes(page);document.body.classList.toggle('quick-mode',simple);
 $('#nav').innerHTML=`<a class="nav-item ${simple?'active':''}" href="#engines"><span class="nav-icon">▷</span><span>三步剪辑</span></a><details class="quick-nav" ${simple?'':'open'}><summary>高级工作台</summary>${pages.filter(x=>!['overview','engines'].includes(x[0])).map(([id,icon,title])=>`<a class="nav-item ${page===id?'active':''}" href="#${id}"><span class="nav-icon">${icon}</span>${title}</a>`).join('')}</details>`;
 if(simple){$('#app>.alert')?.remove();renderQuickProgress();}
};
const quickPreviousJobs=renderJobs;
renderJobs=function(){quickPreviousJobs();renderQuickProgress();
 const jobs=engine.jobs.filter(j=>j.projectId===project().id).sort((a,b)=>b.created-a.created);
 [...document.querySelectorAll('#engine-jobs .engine-job')].forEach((card,i)=>{const j=jobs[i];if(j?.kind==='workflow')card.innerHTML=`<strong>三步自动初剪</strong> ${tag(jobNames[j.status]||j.status)}<p>${esc(j.message)}</p><a class="text-button" href="#engines">回三步首页查看结果</a>`;});
};
document.addEventListener('input',e=>{const p=project();if(e.target.id==='quick-paths'){p.quickPaths=e.target.value;p.quickAssetIds=[];save();$('#quick-assets').textContent='路径已修改，请重新读取素材';renderQuickProgress();}});
document.addEventListener('input',e=>{if(e.target.id==='quick-script'){project().script=e.target.value;project().scriptPreparation=null;project().scriptChoicePending=false;project().intakeScriptName='已修改原文';save();renderScriptPreparation(project());renderQuickProgress();}});
document.addEventListener('change',e=>{const keys={'quick-model':'quickModel','quick-vision':'quickVision','quick-layout':'quickLayout'};if(keys[e.target.id]){project()[keys[e.target.id]]=e.target.value;save();}});
document.addEventListener('click',async e=>{const b=e.target.closest('[data-quick]');if(!b||quick.busy)return;quick.busy=true;quick.error='';quick.submitting=['start','rebuild','retry'].includes(b.dataset.quick);b.disabled=true;renderQuickProgress();try{
 if(!engine.status)await connectEngine();
 const p=project();
 if(b.dataset.quick==='register'){
 const paths=(p.quickPaths||'').split('\n').map(x=>x.trim().replace(/^["']|["']$/g,'')).filter(Boolean);if(!paths.length)throw Error('请先粘贴素材文件或文件夹路径');
 const r=await api('/api/intake/scan',{paths});applyBatch(p,r);render();toast(`已读取本批 ${p.quickAssetIds?.length||0} 个素材，${r.scripts.length} 份脚本`);
 }else if(b.dataset.quick==='start'||b.dataset.quick==='rebuild'){
 if(!p.quickAssetIds?.length)throw Error('请先读取本批素材');
 if(p.scriptChoicePending)throw Error('请先选择本次要用的剧本版本');
 const editMode=quickEditMode(p);
 if(!(p.script||'').trim())throw Error('请把含人物台词的剧本与素材一起导入，系统会自动整理，无需手动改格式。');
 const prepared=await prepareBatchScript(p);
 if(!prepared.strictReady)throw Error('没有找到明确台词，请检查是否导入了本条视频的剧本；系统不会把全部口播放进粗剪。');
 const payload={kind:'workflow',unattended:true,targetSeconds:180,editMode,clientRequestId:uid(),projectId:p.id,name:p.name+' '+editModes[editMode].label.replace('（推荐）','')+' 初剪',assetIds:[...p.quickAssetIds],confirmed:true,model:p.quickModel||'small',visionModel:p.quickVision||(engine.status.localModels||[]).find(m=>m==='qwen3-vl:2b-instruct')||(engine.status.localModels||[])[0],fps:30,vertical:p.quickLayout!=='horizontal',script:'',editingScript:p.script||''};
 if(b.dataset.quick==='rebuild')payload.reuseAnalysisJobId=b.dataset.id;
 await api('/api/jobs',payload);await refreshJobs();toast('任务已提交，按钮下方持续显示后台进度；请勿重复提交');
 }else if(b.dataset.quick==='confirm-start'){
 if(!quick.pending)throw Error('请重新发起任务');
 await api('/api/jobs',quick.pending);quick.pending=null;$('#modal').close();await refreshJobs();toast('三步任务已启动，可在这里查看真实进度');
 }else if(b.dataset.quick==='retry'){await api('/api/retry',{id:b.dataset.id});await refreshJobs();}
 else if(b.dataset.quick==='adopt'){const result=await api('/api/workflow-analysis?id='+encodeURIComponent(b.dataset.id));if(!result.items.length)throw Error('尚无已完成分析');adopt({id:b.dataset.id,kind:'analyze',result});location.hash='speech';}
 else if(b.dataset.quick==='review'){await showMorningReview(b.dataset.id);}
 else if(b.dataset.quick==='review-download'){download('早晨复核清单.json',JSON.stringify(quick.review,null,2),'application/json');}
 else if(b.dataset.quick==='review-play'){
 const r=quick.review.review[Number(b.dataset.index)],{url}=await api('/api/media-url?id='+encodeURIComponent(r.assetId));
 $('#preview-title').textContent=r.source.split('/').pop();$('#preview-note').textContent=`原片 ${tc(r.sourceStart)}—${tc(r.sourceEnd)} · 草稿 ${tc(r.timelineStart)}；播放至标记终点后暂停`;
 $('#video').src=url;$('#video').onloadedmetadata=()=>{$('#video').currentTime=r.sourceStart;};$('#video').ontimeupdate=()=>{if($('#video').currentTime>=r.sourceEnd)$('#video').pause();};$('#preview').showModal();
 }
 }catch(err){quick.error=err.message;toast(err.message);}finally{quick.busy=false;quick.submitting=false;b.disabled=false;renderQuickProgress();}});
$('#help').onclick=()=>openForm('三步剪辑怎么用','<ol class="help-list"><li>双击本机「启动剪辑工作台.command」。在 Finder 复制素材目录路径，粘贴到第一步并读取。</li><li>点击一键开始粗剪。后台自动分析、整理人物语音和食物候选，生成新的达芬奇项目。</li><li>完成后自动导出多轨草稿并启动剪映，在首页打开显示的草稿名，继续审片和综艺包装。</li></ol><p>所有复杂设置在折叠区；角色和菜名不是可靠的自动结论。任务日志与失败原因会如实显示。</p>',()=>{});
$('#modal').addEventListener('close',()=>{$('#modal').classList.remove('quick-confirm');quick.pending=null;});
render();

async function showMorningReview(id){
 const r=quick.review=await api('/api/morning-review?id='+encodeURIComponent(id));
 openForm('按剧本粗剪 · 缺项与切点复核',`<p>初剪 ${tc(r.duration)} · ${r.review.length} 处待核对 · ${r.removed.length} 段指导语候选剔除。</p><div class="alert warm">${esc(r.policy)}<br>${esc(r.warnings.join(' '))}</div><button type="button" class="secondary" data-quick="review-download">下载完整清单 JSON</button><div class="table-wrap"><table><thead><tr><th>草稿位置</th><th>内容 / 原片位置</th><th>需复核原因</th></tr></thead><tbody>${r.review.map((x,i)=>`<tr><td>${tc(x.timelineStart)}<br><button type="button" class="text-button" data-quick="review-play" data-index="${i}">看片段</button></td><td>${esc(x.text)}<small>${esc(x.source.split('/').pop())}<br>${tc(x.sourceStart)}—${tc(x.sourceEnd)}</small></td><td>${esc(x.reasons.join('；'))}</td></tr>`).join('')}</tbody></table></div><h3>未完整匹配脚本</h3>${r.missingScript.map(x=>`<p>${x.target}. ${esc(x.text)} · ${esc(x.reason)}</p>`).join('')||'<p>无</p>'}`,()=>{});
 if(r.scriptCoverage)$('#modal-fields').insertAdjacentHTML('afterbegin',scriptCoveragePanel(r.scriptCoverage));
 if(r.speechRefinement){
  const corrections=r.speechRefinement.corrections||[],timing=r.speechRefinement.realignments||[];
  $('#modal-fields').insertAdjacentHTML('beforeend',`<details><summary>转写前后对照 · ${corrections.length} 处文字 / ${timing.length} 处时间校正</summary><p class="subtle">只依据原声；没有按脚本补造台词。时间校正文本仍需听审，原始分析保留。</p><div class="table-wrap"><table><thead><tr><th>原片位置</th><th>原转写</th><th>本次候选</th></tr></thead><tbody>${[...corrections,...timing].map(x=>`<tr><td>${esc(x.source.split('/').pop())}<br>${tc(x.start)}—${tc(x.end)}</td><td>${esc(x.original)}</td><td>${esc(x.chosen)}${x.needsListeningReview?'<small>时间修复候选 · 待听审</small>':''}</td></tr>`).join('')}</tbody></table></div></details>`);
 }
 if(r.boundaryReview){
  const changed=r.boundaryReview.clips.filter(x=>x.before[0]!==x.after[0]||x.before[1]!==x.after[1]);
  $('#modal-fields').insertAdjacentHTML('beforeend',`<details><summary>切点前后对照 · ${changed.length} 段延长</summary><p class="subtle">下表均为原素材时间。多帧模型建议加上保守停留，不是已通过审片。</p>${changed.map(x=>`<p>${esc(x.text)}<br><small>${tc(x.before[0])}—${tc(x.before[1])} → ${tc(x.after[0])}—${tc(x.after[1])}</small></p>`).join('')}</details>`);
 }
}
$('#preview').addEventListener('close',()=>{$('#video').ontimeupdate=null;});
