// Execute the real summary/review rendering with DOM and transport only mocked.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('dist/quick.js','utf8');
const box={dataset:{},innerHTML:'',querySelector:()=>null,insertAdjacentHTML(_where,html){this.innerHTML+=html;}};
const finish={innerHTML:'',insertAdjacentHTML(_where,html){this.innerHTML+=html;}};
let job,reviewHtml='';
const ctx={quick:{busy:false},engine:{status:{}},project:()=>({quickAssetIds:['asset']}),
 quickJob:()=>job,jobNames:{succeeded:'完成'},editModes:{},
 $:selector=>selector==='#quick-progress'?box:selector==='#quick-finish'?finish:null,
 esc:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'),
 tc:value=>String(value),api:async()=>({duration:120,review:[],removed:[],policy:'保留原声',warnings:[],missingScript:[{target:1,text:'测试台词',reason:'部分匹配'}]}),
 openForm(_title,html){reviewHtml=html;}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function renderQuickProgress('),source.indexOf('function scriptCoveragePanel(')),ctx);
vm.runInContext(source.slice(source.indexOf('async function showMorningReview('),source.indexOf("$('#preview').addEventListener")),ctx);
function render(counts){
 box.dataset={};job={id:'job',status:'succeeded',message:'已完成',result:{analysisCount:10,speechClips:5,duration:120,
 resolve:{project:'测试'},draft:{draftName:'测试草稿',clipCount:7,tracks:['V1','V2'],draftOpened:true},
 unattended:true,morningReviewCount:2,missingScriptCount:3,removedCommands:0,...counts}};
 vm.runInContext('renderQuickProgress()',ctx);return box.innerHTML;
}
assert.match(render({brollClips:7,foodClips:2}),/第二轨补画面 7 段/);
assert.match(render({brollClips:0,foodClips:2}),/第二轨补画面 0 段/,'a real zero must not fall back to food count');
assert.match(render({foodClips:2}),/第二轨补画面 2 段/,'older tasks use the legacy count');
assert.match(render({}),/第二轨补画面 0 段/);
assert.doesNotMatch(box.innerHTML,/食物候选/);
assert.match(finish.innerHTML,/脚本未完整匹配 3 条（含部分匹配）/);
(async()=>{
 await vm.runInContext('showMorningReview("job")',ctx);
 assert.match(reviewHtml,/<h3>未完整匹配脚本<\/h3>/);
 assert.doesNotMatch(reviewHtml,/<h3>未匹配脚本<\/h3>/);
 console.log('PASS: broll-first summary, zero/legacy fallback, explicit partial-match wording and morning-review heading');
})().catch(error=>{console.error(error);process.exitCode=1;});
