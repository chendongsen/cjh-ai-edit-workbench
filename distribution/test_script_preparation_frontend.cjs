// The real intake helper and real Start/Rebuild handlers, with transport only mocked.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const intake=fs.readFileSync('dist/intake.js','utf8'),quick=fs.readFileSync('dist/quick.js','utf8');
const handlers={},calls=[],messages=[],elements=new Map();
let p={id:'a',name:'测试项目',assets:[],quickAssetIds:['asset'],script:'',quickEditMode:'strict'},saved=0;
const prepared=text=>({revision:'script-normalize-4',sourceText:text,normalizedText:'达人：'+text,shotRequirements:[],
 targets:text.trim()?[{text,sourceName:null,dialogue:false}]:[],count:text.trim()?1:0,strictReady:!!text.trim(),warnings:[],omittedCount:0});
let prepareResponse=async text=>prepared(text);
const ctx={document:{addEventListener(type,fn){(handlers[type]??=[]).push(fn);}},Object,console,
 project:()=>p,save(){saved++},uid:()=> 'request-id',render(){},renderQuickProgress(){},
 $:selector=>{if(!elements.has(selector))elements.set(selector,{textContent:'',innerHTML:'',disabled:false});return elements.get(selector);},
 esc:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
 quick:{busy:false},engine:{status:{localModels:['qwen3-vl:2b-instruct']}},toast:message=>messages.push(message),
 connectEngine:async()=>{},refreshJobs:async()=>{},api:async(url,payload)=>{
  calls.push({url,payload});return url==='/api/intake/prepare'?prepareResponse(payload.text):{id:'job'};
 }};
vm.createContext(ctx);vm.runInContext(intake,ctx);
vm.runInContext(quick.slice(quick.indexOf('const editModes='),quick.indexOf('descriptions.overview=')),ctx);
const intakeChanges=[...handlers.change];
vm.runInContext(quick.slice(quick.indexOf("document.addEventListener('input'"),quick.indexOf("$('#help').onclick")),ctx);
const quickClick=handlers.click.at(-1),scriptInput=handlers.input.at(-1);
const run=code=>vm.runInContext(code,ctx);
const click=async(action,id)=>{const button={dataset:{quick:action,id},disabled:false};await quickClick({target:{closest:()=>button}});};
const switchScript=id=>intakeChanges.forEach(fn=>fn({target:{id:'batch-script-choice',value:id}}));
const jobs=()=>calls.filter(c=>c.url==='/api/jobs');
(async()=>{
 const raw='【达人（笑）】\n“这口太满足了！”';
 ctx.result={assets:[],scripts:[{id:'one',name:'原始脚本.docx',text:raw,preparation:prepared(raw)}],selectedScriptId:'one',warnings:[],skipped:0,mode:'path'};
 run('applyBatch(project(),result)');
 assert.equal(p.script,raw);assert.equal(p.scriptPreparation.sourceText,raw);
 assert.match(run('batchEntry(project())'),/已整理 1 条台词、0 项镜头要求，可以开始按剧本粗剪/);
 assert.match(run('batchEntry(project())'),/查看原脚本/);assert.doesNotMatch(run('batchEntry(project())'),/建议每行/);
 await click('start');assert.equal(jobs().length,1);assert.equal(jobs()[0].payload.editingScript,raw);
 assert.equal(calls.filter(c=>c.url==='/api/intake/prepare').length,0,'fresh import uses source-bound preparation');
 // Existing projects need no re-import or format edits.
 delete p.scriptPreparation;await click('rebuild','old-job');
 assert.equal(calls.filter(c=>c.url==='/api/intake/prepare').length,1);
 assert.equal(jobs().at(-1).payload.reuseAnalysisJobId,'old-job');
 assert.equal(jobs().at(-1).payload.editingScript,raw);
 await click('start');assert.equal(calls.filter(c=>c.url==='/api/intake/prepare').length,1,'cached source is reused');
 // Editing the source immediately invalidates both preview and submit cache.
 scriptInput({target:{id:'quick-script',value:'达人：新的台词'}});
 assert.equal(p.scriptPreparation,null);assert.match(elements.get('#batch-script-preparation').innerHTML,/开始粗剪时自动整理/);
 await click('start');assert.equal(calls.filter(c=>c.url==='/api/intake/prepare').length,2);
 assert.equal(jobs().at(-1).payload.editingScript,'达人：新的台词');
 // Switching versions (or choosing none) never reuses another version's target list.
 p.intakeScripts.push({id:'two',name:'新版.docx',text:'另一版原文',preparation:prepared('另一版原文')});
 switchScript('two');assert.equal(p.script,'另一版原文');assert.equal(p.scriptPreparation.sourceText,'另一版原文');
 switchScript('none');assert.equal(p.script,'');assert.equal(p.scriptPreparation,null);
 const count=jobs().length;await click('start');assert.equal(jobs().length,count);assert.match(messages.at(-1),/含人物台词的剧本/);
 // A new media-only batch clears stale script preparation and preserves the previous original in history.
 switchScript('one');ctx.result={assets:[{id:'new-asset',path:'/tmp/new.mov'}],scripts:[],selectedScriptId:null,warnings:[],skipped:0,mode:'path'};
 run('applyBatch(project(),result)');assert.equal(p.scriptPreparation,null);assert.equal(p.script,'');assert.equal(p.intakeScriptHistory[0].text,raw);
 // Zero targets is an explicit block for strict mode, not a hidden fallback.
 p.script='达人试吃，突出好吃';prepareResponse=async text=>({...prepared(text),targets:[],count:0,strictReady:false,warnings:['只有画面描述，没有明确台词。']});
 await click('start');assert.equal(jobs().length,count);assert.match(messages.at(-1),/没有找到明确台词/);
 assert.match(run('scriptPreparationView(project())'),/只有画面描述/);
 p.quickEditMode='balanced';await click('start');assert.equal(jobs().length,count);
 // Transport errors must neither claim success nor launch a job.
 p.quickEditMode='strict';p.script='尚未准备';p.scriptPreparation=null;
 prepareResponse=async()=>{throw Error('服务不可用');};const beforeFailure=jobs().length;
 await click('start');assert.equal(jobs().length,beforeFailure);assert.equal(p.scriptPreparation,null);assert.match(elements.get('#batch-script-preparation').textContent,/未完成/);
 // A source changed during prepare must not be overwritten or submitted with old targets.
 let resolvePreparation;prepareResponse=text=>new Promise(resolve=>{resolvePreparation=()=>resolve(prepared(text));});
 const pending=click('start');await Promise.resolve();assert.equal(typeof resolvePreparation,'function');
 scriptInput({target:{id:'quick-script',value:'整理过程中更新了原文'}});resolvePreparation();await pending;
 assert.equal(jobs().length,beforeFailure);assert.equal(p.script,'整理过程中更新了原文');assert.equal(p.scriptPreparation,null);assert.match(messages.at(-1),/发生了变化/);
 // A project switch while preparing writes only to the original project object.
 const original=p;const pendingProject=click('start');await Promise.resolve();
 p={id:'b',name:'另一个项目',assets:[],script:'另一项目',quickAssetIds:['b']};resolvePreparation();await pendingProject;
 assert.equal(p.scriptPreparation,undefined);assert.equal(original.scriptPreparation.sourceText,original.script);
 assert.equal(jobs().at(-1).payload.projectId,'a');assert.equal(jobs().at(-1).payload.editingScript,original.script);
 // All source-derived text remains escaped in previews and warnings.
 const hostile='</textarea><img src=x onerror=alert(1)>';p.script=hostile;p.scriptPreparation={...prepared(hostile),normalizedText:hostile,warnings:[hostile]};
 const html=run('batchEntry(project())');assert.doesNotMatch(html,/<img src=x/);assert.match(html,/&lt;img src=x/);
 // Stale or future-revision cached preparations cannot be used as ready states.
 p.scriptPreparation.sourceText='old';assert.equal(run('currentScriptPreparation(project())'),null);
 p.scriptPreparation.sourceText=p.script;p.scriptPreparation.revision='old-version';assert.equal(run('currentScriptPreparation(project())'),null);
 assert.ok(saved>0);
 console.log('PASS: original-preserving import, normalized preview, old project/rebuild preparation, invalidation, version switching, zero-target strict block, transport/race safety, XSS escaping');
})().catch(error=>{console.error(error);process.exitCode=1;});
