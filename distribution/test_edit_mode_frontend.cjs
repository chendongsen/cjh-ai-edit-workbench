const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('dist/quick.js','utf8'),handlers={},calls=[],messages=[];
let saved=0,p={id:'test',name:'测试',quickAssetIds:['a'],script:'达人：好吃'};
const hint={textContent:''};
const ctx={document:{addEventListener(type,fn){handlers[type]=fn;}},Object,
 project:()=>p,save(){saved++},$:()=>hint,quick:{busy:false},
 engine:{status:{localModels:['qwen3-vl:2b-instruct']}},renderQuickProgress(){},
 prepareBatchScript:async()=>({strictReady:true}),
 api:async(url,payload)=>calls.push({url,payload}),refreshJobs:async()=>{},toast:s=>messages.push(s),uid:()=> 'request',
 connectEngine:async()=>{},render(){}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('const editModes='),source.indexOf('descriptions.overview=')),ctx);
assert.equal(vm.runInContext('quickEditMode({})',ctx),'script');
assert.equal(vm.runInContext('quickEditMode({quickEditMode:"invalid"})',ctx),'script');
assert.match(vm.runInContext('quickModeChoice({quickEditMode:"strict"})',ctx),/按剧本剪辑/);
assert.doesNotMatch(vm.runInContext('quickModeChoice({})',ctx),/<select|严格按脚本|多留表情|均衡/);
vm.runInContext(source.slice(source.indexOf("document.addEventListener('change'"),source.indexOf("document.addEventListener('click',async")),ctx);
handlers.change({target:{id:'quick-edit-mode',value:'strict'}});
assert.equal(p.quickEditMode,undefined);assert.equal(saved,0);
vm.runInContext(source.slice(source.indexOf("document.addEventListener('click',async"),source.indexOf("$('#help').onclick")),ctx);
const click=async(action,id)=>{const b={dataset:{quick:action,id},disabled:false};await handlers.click({target:{closest:()=>b}});};
(async()=>{
 await click('start');assert.equal(calls[0].payload.editMode,'script');assert.match(calls[0].payload.name,/按剧本剪辑/);
 p.quickEditMode='expressive';await click('rebuild','old-analysis');
 assert.equal(calls[1].payload.editMode,'script');assert.equal(calls[1].payload.reuseAnalysisJobId,'old-analysis');
 p.quickEditMode='strict';p.script=' ';await click('start');assert.equal(calls.length,2);assert.match(messages.at(-1),/含人物台词的剧本/);
 await click('retry','old-job');assert.equal(calls[2].url,'/api/retry');assert.deepEqual(JSON.parse(JSON.stringify(calls[2].payload)),{id:'old-job'});
 delete p.quickEditMode;p.script='达人：好吃';await click('start');assert.equal(calls[3].payload.editMode,'script');
 console.log('PASS: one script mode, old settings ignored, script required, rebuild and retry routing');
})().catch(e=>{console.error(e);process.exitCode=1});
