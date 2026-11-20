'use strict';
// Independent local testing. Nothing is sent to a team server.
const betaIssueNames=['编导未去掉','误删达人','表情笑声被截断','闺蜜接话丢失','食物匹配错误','转写错误','草稿打不开','音画不同步','运行慢或卡住','其他'];
const betaStageNames={analysis:'素材分析与食物复核',voices:'声音与转写复核',boundaries:'切点多帧复核',resolve:'达芬奇粗剪',jianying:'剪映草稿转换'};
const betaMinutes=x=>x==null?'未测量':`${(x/60).toFixed(2)} 分钟`;
const betaBeforeFlow=quickFlow;
overview=engines=function(){
 return betaBeforeFlow()+`<details class="quick-options" id="beta-panel"><summary>本地内测 · 环境自检、耗时和问题反馈</summary><div class="panel-pad"><p>每台 Mac 独立处理素材。建议先试 10～20 条，连接电源；复杂包装继续在剪映完成。</p><button class="secondary" data-beta="check">检查本机环境</button> <button class="secondary" data-beta="feedback">记录问题 / 导出本次测试</button><div id="beta-health" role="status"></div><p class="subtle">报告不自动上传。不包含路径、台词或声纹；你填写的描述请自行检查。无本批声纹参考时保守保留人声，不保证自动去净编导。</p></div></details>`;
};
const betaBeforeProgress=renderQuickProgress;
renderQuickProgress=function(){
 betaBeforeProgress();
 const box=$('#quick-progress'),j=quickJob();if(!box||!j)return;
 let metrics=$('#beta-timing');
 if(!metrics){metrics=document.createElement('div');metrics.id='beta-timing';box.append(metrics);}
 const measured=j.timing?.phaseSeconds||{}, loads=j.timing?.modelLoadSecondsIncluded||{};
 const total=j.attemptHistory?.reduce((s,x)=>s+x.seconds,0);
 metrics.innerHTML=`<p>累计已结束尝试：${betaMinutes(total)}${j.status==='running'?` · 本次运行中 ${betaMinutes(Date.now()/1000-j.started)}`:''}</p>${Object.keys(measured).length?`<p class="subtle">${Object.entries(measured).map(([k,v])=>`${esc(betaStageNames[k]||k)} ${betaMinutes(v)}`).join(' ／ ')}</p><p class="subtle">其中模型加载 ${betaMinutes(Object.values(loads).reduce((a,b)=>a+b,0))}（已含在阶段时间内，不重复相加）。阶段结束后更新。缓存复用与重试详情随反馈导出。</p>`:'<p class="subtle">新任务将自动记录阶段耗时；旧任务不会补造测量数据。</p>'}`;
};
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-beta]');if(!b)return;b.disabled=true;
 try{
  if(!engine.status)await connectEngine();
  if(b.dataset.beta==='check'){
   const r=await api('/api/beta/status');
   const target=$('#beta-health');if(target)target.innerHTML=`<h3>${esc(r.version)} · ${esc(r.hardware.chip)} / ${r.hardware.memoryGB??'?'} GB</h3>${r.checks.map(c=>`<p>${c.ok?'✓':'⚠'} ${esc(c.name)}<br><small>${esc(c.help)}</small></p>`).join('')}<p>${esc(r.warning)}</p><p>建议 16 GB 或以上内存；低内存机器先小批测试。</p>`;
  }else if(b.dataset.beta==='feedback'){
   const j=quickJob();if(!j)throw Error('请先运行一批粗剪；失败的任务也可以反馈');
   const r=await api('/api/beta/report?id='+encodeURIComponent(j.id)),f=r.feedback;
   openForm('本次粗剪内测反馈',`<p>任务 ${esc(j.id.slice(0,8))} · ${esc(jobNames[j.status]||j.status)} · ${r.input.files} 个素材</p><p>自动运行累计 ${betaMinutes(r.timing.activeSeconds)} ／ 含排队和重试等待 ${betaMinutes(r.timing.elapsedSeconds)}</p>
   <input type="hidden" name="betaJob" value="${esc(j.id)}">
   ${selectField('draftOpened','在本机剪映打开并播放新草稿了吗？',f.draftOpened||'untested',[['untested','还没验收'],['yes','已打开并播放'],['no','打开失败']])}
   ${field('manualBaselineMinutes','同等素材手动粗剪通常需多少分钟（可留空）',f.manualBaselineMinutes??'','number')}
   ${field('reviewMinutes','这次审片和返工用了多少分钟（可留空）',f.reviewMinutes??'','number')}
   <fieldset><legend>遇到的问题（可多选）</legend>${betaIssueNames.map(x=>`<label class="engine-choice"><input type="checkbox" name="betaIssue" value="${esc(x)}" ${(f.issues||[]).includes(x)?'checked':''}>${esc(x)}</label>`).join('')}</fieldset>
   ${textField('note','问题描述：请写草稿时间点、现象和预期，不填私密内容',f.note||'')}
   <p class="subtle">先保存，再导出 JSON 发给负责人；不自动收集。报告不含原始日志和素材路径，描述由你自行确认。</p>
   <button type="button" class="primary" data-beta="save">保存反馈</button> <button type="button" class="secondary" data-beta="export" data-id="${esc(j.id)}">导出测试报告 JSON</button>
   <p id="beta-save-status" role="status"></p>`,()=>{});
   $('#modal-form button[type="submit"]').hidden=true;
  }else if(b.dataset.beta==='save'||b.dataset.beta==='export'){
   const form=$('#modal-form'),v=Object.fromEntries(new FormData(form));
   await api('/api/beta/feedback',{id:v.betaJob,issues:[...form.querySelectorAll('[name="betaIssue"]:checked')].map(x=>x.value),draftOpened:v.draftOpened,manualBaselineMinutes:v.manualBaselineMinutes,reviewMinutes:v.reviewMinutes,note:v.note});
   $('#beta-save-status').textContent='已保存在本机。';
   if(b.dataset.beta==='export'){
    const r=await api('/api/beta/report?id='+encodeURIComponent(v.betaJob));
    download('CJH_内测报告_'+v.betaJob.slice(0,8)+'.json',JSON.stringify(r,null,2),'application/json');
   }
  }
 }catch(err){toast(err.message);}finally{b.disabled=false;}
});
$('#modal').addEventListener('close',()=>{$('#modal-form button[type="submit"]').hidden=false;});
render();
