'use strict';
// Keep the three-step surface small; detailed decisions and original clips are one click away.
const priorEditorialProgress=renderQuickProgress;
renderQuickProgress=function(){
 priorEditorialProgress();
 const box=$('#quick-progress'),j=quickJob();
 if(!box||j?.status!=='succeeded'||!j.result?.editorialRevision||box.querySelector('[data-editorial-audit]'))return;
 const r=j.result;
 const b=r.brollSummary;
 const combo=b?.visionCombo;
 if(combo&&combo.status!=='disabled')box.querySelector('details').insertAdjacentHTML('beforebegin',`<p class="subtle">组合视觉：${combo.status==='active'?'Chinese-CLIP 检索 → 表情保护 → Qwen 复核':'未启用，沿用原有视觉流程'}${combo.status==='active'?` · 表情保护拦截 ${Number(combo.faceProtected||0)} 次候选`:''}${combo.warnings?.length?` · ${combo.warnings.length} 条运行提醒，详见核对依据`:''}</p>`);
 const scriptPictures=b?.selectionBasis==='explicit-script-requirements';
 if(b)box.querySelector('details').insertAdjacentHTML('beforebegin',`<div class="alert"><p>第二轨补充：${scriptPictures?`按剧本匹配 ${Number(b.scriptCount)||0} 段`:`食物 ${Number(b.foodCount)||0} 段 · 环境 ${Number(b.environmentCount)||0} 段`} · 合计 ${(Number(b.seconds)||0).toFixed(1)} 秒${Number.isFinite(b.coverage)?`（全片 ${Math.round(b.coverage*100)}%）`:''}</p><p class="subtle">${scriptPictures?'未匹配项见缺项清单；不以泛场景画面代替剧本要求。':'首中尾抽帧复核，不是全片验收；不强凑比例，人物反应和原声优先保留。'}</p></div>`);
 box.querySelector('details').insertAdjacentHTML('beforebegin',`<div class="alert"><p>全素材分析中排除指导语 ${r.removedCommands} 处（约 ${r.removedCommandSeconds} 秒） · ${r.unattended?`入选草稿 ${r.morningReviewCount} 处待复核`:`不确定对话 ${r.reviewSpeechCount} 处待听审`}</p>${scriptPictures?'':`<p>食物候选池 ${r.foodCandidateCount} 个 · 实际使用 ${r.usedFoodSources} 个素材 · 插入 ${r.foodClips} 段</p>`}<button class="secondary" data-editorial-audit="${esc(j.id)}">核对删句与食物匹配依据</button></div>`);
};
function auditClipButton(asset,start,end,label='试听原片'){
 return `<button type="button" class="text-button" data-audit-asset="${esc(asset)}" data-start="${start}" data-end="${end}">${label}</button>`;
}
document.addEventListener('click',async e=>{
 const audit=e.target.closest('[data-editorial-audit]'),clip=e.target.closest('[data-audit-asset]');
 if(!audit&&!clip)return;
 const button=audit||clip;button.disabled=true;
 try{
  if(audit){
   const p=await api('/api/workflow-plan?id='+encodeURIComponent(audit.dataset.editorialAudit));
   const rows=(list)=>list.map(s=>`<article class="engine-job"><p>${esc(s.source.split('/').pop())} · ${s.start.toFixed(2)}–${s.end.toFixed(2)} 秒</p><strong>${esc(s.text)}</strong><p class="subtle">${esc(s.reason)}</p>${auditClipButton(s.assetId,Math.max(0,s.start-.3),s.end+.5)}</article>`).join('');
   openForm('本次剪辑决策 · 原素材未修改',`<p class="subtle">${p.revision?.startsWith('night-')?'声纹参考匹配加指导语判断，身份不确定保留；未做重叠人声分离。':'这是转写语义筛除，不是已确认的编导声纹。'} 相邻说话、重叠声音和示范台词仍可能残留。试听按钮打开原片，不会修改草稿。</p><details open><summary>已筛除 ${p.removedSpeech?.length||0} 处拍摄口令</summary>${rows(p.removedSpeech||[])}</details><details><summary>保留待听审 ${p.reviewSpeech?.length||0} 处（全素材分析，未必入选草稿）</summary>${rows(p.reviewSpeech||[])}</details><details><summary>食物插入 ${p.foodPlacements.length} 段</summary>${p.foodPlacements.map(s=>`<article class="engine-job"><strong>${esc(s.dish)} · 成片 ${tc(s.at)}</strong><p>${esc(s.quote)}</p><p class="subtle">${esc(s.matchBasis)}${s.matchBasis.includes('人工')?'':`；模型分数 ${s.confidence}（非准确率）`}</p>${auditClipButton(s.assetId,s.start,s.end,'看食物原片')}${s.mainAssetId?auditClipButton(s.mainAssetId,Math.max(0,s.mainSourceStart-.5),s.mainSourceStart+3,'听对应主线'):''}</article>`).join('')}</details><p class="subtle">${esc(p.notes)}</p>`,()=>{});
   $('#modal').classList.add('editorial-audit');
   if(p.brollSummary){
    const c=p.brollSummary.visionCombo;
    if(c?.warnings?.length)$('#modal-fields').insertAdjacentHTML('afterbegin',`<p class="alert">${c.warnings.map(x=>esc(x)).join('<br>')}</p>`);
    const section=$('#modal-fields details:last-of-type');
    if(section)section.querySelectorAll('article').forEach((article,index)=>{
     const shot=p.foodPlacements[index];
     if(Number.isFinite(shot?.retrievalScore))article.querySelector('.subtle').textContent=`${shot.matchBasis}；检索相似度 ${shot.retrievalScore.toFixed(3)}，只用于候选排序，不是准确率；插入前另做视觉复核。`;
    });
    if(section){section.querySelector('summary').textContent=`第二轨补充 ${p.foodPlacements.length} 段（含食物与环境）`;section.querySelectorAll('[data-audit-asset]').forEach(b=>{if(b.textContent==='看食物原片')b.textContent='看补充原片';});}
   }
  }else{
   const result=await api('/api/media-url?id='+encodeURIComponent(clip.dataset.auditAsset));
   const start=Number(clip.dataset.start),end=Number(clip.dataset.end),video=$('#video');
   $('#preview-title').textContent='原素材核对';$('#preview-note').textContent=`${start.toFixed(2)}–${end.toFixed(2)} 秒 · 本机播放，不上传`;
   video.src=result.url;video.onloadedmetadata=()=>{video.currentTime=Math.min(start,video.duration||0);};
   video.ontimeupdate=()=>{if(video.currentTime>=end)video.pause();};
   $('#preview').showModal();
  }
 }catch(error){toast(error.message);}finally{button.disabled=false;}
});
$('#preview').addEventListener('close',()=>{$('#video').ontimeupdate=null;});
$('#modal').addEventListener('close',()=>{$('#modal').classList.remove('editorial-audit');});
render();
