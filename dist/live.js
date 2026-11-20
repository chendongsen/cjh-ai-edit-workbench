'use strict';
// Stay visible beside Start, and keep reconnecting after a transient polling error.
const liveMessages=new Map();
const liveLegacyCounts=new Map();
const liveStages={startup:'启动应用与连接检查',analysis:'素材分析／食物复核',voices:'声音与台词复核',boundaries:'切点多帧复核',broll:'段落补画面与人物反应复核',resolve:'达芬奇写入粗剪',jianying:'导出剪映草稿',done:'草稿已生成'};
function liveBody(j,other=false){
 if(!j)return '<p>点击开始后，这里持续显示实际执行状态。</p>';
 const old=liveMessages.get(j.id),now=Date.now();
 if(!old||old.text!==j.message)liveMessages.set(j.id,{text:j.message,at:now});
 const active=['queued','running'].includes(j.status);
 const elapsed=!active&&(!j.started||!j.finished||!j.attemptHistory?.length)?null:Math.max(0,Math.round((active?now/1000:j.finished)-(j.started||j.created)));
 const timingText=elapsed===null?'历史任务 · 用时未记录':`${j.status==='queued'?'已等待':'本次运行'} ${elapsed<60?elapsed+' 秒':Math.floor(elapsed/60)+' 分 '+elapsed%60+' 秒'}`;
 const last=j.lastLogAt?j.lastLogAt*1000:liveMessages.get(j.id).at;
 let count='';
 if(j.analysisTotal)count=`<p>初次素材分析已处理 ${j.analysisProcessed} / ${j.analysisTotal} 个（不是整个粗剪的百分比）</p>`;
 const legacy=liveLegacyCounts.get(j.id);
 if(!count&&legacy?.total)count=`<p>初次素材分析已处理 ${legacy.processed} / ${legacy.total} 个（每 30 秒核对，不是整体百分比）</p>`;
 const match=(j.message||'').match(/读取素材 (\d+)\/(\d+)/);
 if(!count&&match)count=`<p>当前分析第 ${match[1]} / ${match[2]} 个素材</p>`;
 return `<strong>${other?'另一项目 · ':''}${esc(jobNames[j.status]||j.status)}${j.status==='running'?' · '+esc(liveStages[j.progress?.stage]||'后台处理中'):''}</strong><p>${esc(j.message||'等待后台接单')}</p>${count}<p class="subtle">任务 ${esc(j.id.slice(0,8))} · ${timingText}${j.queueAhead?` · 前面还有 ${j.queueAhead} 项`:''}</p>${j.status==='running'&&now-last>180000?'<p class="batch-warnings">本步骤超过 3 分钟没有新日志，后台可能仍在模型推理；暂不判断为失败，请勿重复提交。</p>':''}${j.status==='queued'&&!j.queueAhead?'<p class="subtle">等待本机工作进程接单或重试窗口；排队不等于已经开始分析。</p>':''}`;
}
function renderLive(){
 const button=$('[data-quick="start"]');if(!button)return;
 let box=$('#live-progress');if(!box){button.insertAdjacentHTML('afterend','<div id="live-progress" class="live-card" role="status" aria-live="polite"></div>');box=$('#live-progress');}
 const j=quickJob();
 box.innerHTML=quick.submitting?'<strong>正在提交任务…</strong><p>正在检查本机环境，请不要重复点击。</p>':liveBody(j);
 if(quick.error)box.insertAdjacentHTML('afterbegin',`<p class="batch-warnings">上次请求提示：${esc(quick.error)}</p>`);
 if(engine.pollError)box.insertAdjacentHTML('afterbegin',`<p class="batch-warnings">状态更新中断，正在每 4 秒重连：${esc(engine.pollError)}。最后确认时间：${engine.lastJobsAt?new Date(engine.lastJobsAt).toLocaleTimeString():'尚未同步'}。这不代表后台任务已停止。</p>`);
 const notice=engine.status?.autoLaunchEditors?(engine.status.autoStartModels?'已安装模型由任务自动启动，不用先开模型服务。':'')+'开始后自动连接达芬奇，生成草稿后交给剪映。'+(engine.status.jianying?.handoff?.trusted?'本机草稿助手已授权，会自动打开本次草稿；若正在编辑其他项目则提示等待。':'自动进入草稿需要一次性授权「CJH草稿助手」辅助功能；未授权时仍会保存草稿。'):'自动启动更新将在下次重启本机服务后启用。请等运行中的任务全部结束后再重启，不要中途关闭后台服务。';
 box.insertAdjacentHTML('beforeend',`<p class="subtle">${esc(notice)}</p>`);
 const other=engine.jobs.find(x=>x.kind==='workflow'&&x.projectId!==project().id&&['running','queued'].includes(x.status));
 let top=$('#live-other');
 if(other){if(!top){$('.quick-steps').insertAdjacentHTML('beforebegin','<div id="live-other" class="live-card" role="status"></div>');top=$('#live-other');}top.innerHTML=liveBody(other,true)+'<p class="subtle">此任务属于另一个项目／浏览器。当前页面所选项目不会自动变更。</p>';}
 else top?.remove();
}
const livePreviousProgress=renderQuickProgress;
renderQuickProgress=function(){livePreviousProgress();renderLive();};
const livePreviousRender=render;
render=function(){livePreviousRender();renderLive();};
// Keep this in-flight, pre-upgrade job visible without restarting its server.
let liveLegacyBusy=false;
async function liveLegacySync(){
 if(liveLegacyBusy||!engine.token||engine.status?.autoLaunchEditors)return;
 const active=engine.jobs.find(j=>j.kind==='workflow'&&j.status==='running'&&!j.analysisTotal);
 if(!active)return;liveLegacyBusy=true;
 try{
  const report=await api('/api/beta/report?id='+encodeURIComponent(active.id));
  const analysis=await api('/api/workflow-analysis?id='+encodeURIComponent(active.id));
  liveLegacyCounts.set(active.id,{processed:analysis.items.length,total:report.input.files});renderLive();
 }catch{/* Main polling surfaces connectivity errors; never stop the running job. */}
 finally{liveLegacyBusy=false;}
}
setInterval(liveLegacySync,30000);
const livePreviousJobs=renderJobs;
renderJobs=function(){livePreviousJobs();const active=engine.jobs.find(j=>j.kind==='workflow'&&j.status==='running');if(active&&!liveLegacyCounts.has(active.id))void liveLegacySync();};
render();
