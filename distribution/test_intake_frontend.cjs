// Pure algorithm checks; browser interactions are verified separately through CUA.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const handlers={};
const context={document:{addEventListener(event,handler){(handlers[event]??=[]).push(handler);}},render(){},renderQuickProgress(){},quick:{},project(){return context.p||{}},$(selector){return selector==='#help'?{}:null},save(){},uid(){return 'ui-id'},console};
vm.createContext(context);
vm.runInContext(fs.readFileSync('dist/intake.js','utf8'),context);
function file(name,size=3){return {name,isFile:true,file(ok){ok({name,size});}};}
function dir(name,batches){return {name,isDirectory:true,createReader(){let i=0;return {readEntries(ok){ok(batches[i++]||[]);}};}};}
(async()=>{
 context.entries=[dir('拍摄',[Array.from({length:100},(_,i)=>file(i+'.MOV')),[dir('食物',[[file('虾饺.mp4')]]),file('脚本.docx'),file('.DS_Store'),file('unsupported.pdf')]])];
 const rows=await vm.runInContext('batchEntries(entries)',context);
 assert.equal(rows.length,102);assert.equal(rows.at(-1).path,'拍摄/脚本.docx');assert.equal(rows[100].path,'拍摄/食物/虾饺.mp4');
 context.p={assets:[],script:'old',quickAssetIds:['old']};context.r={assets:[{id:'new',path:'/copy/clip.mov'}],scripts:[],selectedScriptId:null,warnings:[],mode:'copy'};
 vm.runInContext('applyBatch(p,r)',context);assert.equal(context.p.script,'');assert.equal(context.p.quickAssetIds[0],'new');assert.equal(context.p.intakeScriptHistory[0].text,'old');
 context.r={assets:[],scripts:[{id:'one',name:'a',text:'A'},{id:'two',name:'b',text:'B'}],selectedScriptId:null,warnings:[],mode:'path'};
 vm.runInContext('applyBatch(p,r)',context);assert.equal(context.p.scriptChoicePending,true);assert.equal(context.p.quickAssetIds[0],'new');
 context.r.selectedScriptId='two';vm.runInContext('applyBatch(p,r)',context);assert.equal(context.p.script,'B');assert.equal(context.p.scriptChoicePending,false);
 context.rows=[{path:'a.MOV',file:{size:1}},{path:'a.mov',file:{size:1}}];assert.throws(()=>vm.runInContext('batchFilter(rows)',context),/重名/);
 vm.runInContext('batchState.cancelled=true',context);await assert.rejects(vm.runInContext('batchEntries(entries)',context),/已停止/);
 // Exercise the actual drop handler -> recursive collector -> chunk transport -> project update.
 context.isLocal=true;context.engine={status:{},url:'http://127.0.0.1:8765',token:'test-only'};
 context.AbortController=AbortController;context.setTimeout=setTimeout;context.clearTimeout=clearTimeout;context.toast=()=>{};
 context.p={id:'drop-test',assets:[],script:'old'};
 let sent=0,expected=0,beginFiles=[],offsets=[];const received=[];
 context.fetch=async(url,options)=>{
  let result;
  if(url.includes('/begin')){beginFiles=JSON.parse(options.body).files;offsets=beginFiles.map(()=>0);expected=beginFiles.reduce((s,f)=>s+f.size,0);result={id:'test',chunkBytes:2,totalBytes:expected};}
  else if(url.includes('/chunk')){const q=new URL(url).searchParams,i=Number(q.get('index')),offset=Number(q.get('offset'));assert.equal(offset,offsets[i]);const bytes=Buffer.from(await options.body.arrayBuffer());received.push(bytes);offsets[i]+=bytes.length;sent+=bytes.length;result={offset:offsets[i]};}
  else if(url.includes('/finish')){assert.equal(sent,expected);result={assets:[{id:'real-registered-id',path:'/imports/clip.MOV'}],scripts:[{id:'script',name:'脚本.txt',text:'达人：好吃'}],selectedScriptId:'script',warnings:[],skipped:0,mode:'copy'};}
  else throw Error('unexpected endpoint');
  return {ok:true,json:async()=>result};
 };
 const movie=new File(['video'],'clip.MOV'),script=new File(['达人：好吃'],'脚本.txt');
 const dropFolder=dir('拍摄',[[{...file(movie.name),file(ok){ok(movie);}},{...file(script.name),file(ok){ok(script);}}]]);
 let captured=false,prevented=false,stopped=false;
 context.dropEvent={target:{closest(){return {id:'batch-drop',classList:{remove(){}}};}},preventDefault(){prevented=true;},stopImmediatePropagation(){stopped=true;},dataTransfer:{items:[{kind:'file',webkitGetAsEntry(){captured=true;return dropFolder;}}],files:[]}};
 vm.runInContext('var originalImportBatch=importBatch; importBatch=function(getRows){return lastDropPromise=originalImportBatch(getRows);}',context);
 handlers.drop[0](context.dropEvent);assert.equal(captured,true);assert.equal(prevented,true);assert.equal(stopped,true);
 await context.lastDropPromise;
 assert.equal(beginFiles.length,2);assert.equal(sent,expected);assert.equal(context.p.quickAssetIds[0],'real-registered-id');assert.equal(context.p.script,'达人：好吃');assert.equal(context.quick.busy,false);
 console.log('PASS: recursive >100-entry drag traversal, scripts, replacement, ambiguity, deduplication, cancellation, drop-to-chunks-to-project');
})().catch(e=>{console.error(e);process.exitCode=1;});
