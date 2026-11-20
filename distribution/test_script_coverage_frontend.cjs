const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const text=fs.readFileSync('dist/quick.js','utf8');
const ctx={esc:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')};
vm.createContext(ctx);
vm.runInContext(text.slice(text.indexOf('function scriptCoveragePanel('),text.indexOf('const quickPreviousRender=')),ctx);
ctx.c={dialogue:{required:2,complete:1,partial:1,missing:0,items:[{text:'<img onerror=x>',status:'partial',reason:'只选到半句'}]},shots:{required:1,unresolved:1,items:[{text:'摘电池',status:'conflict',reason:'会盖住达人反应'}]}};
const html=vm.runInContext('scriptCoveragePanel(c)',ctx);
assert.match(html,/仍有缺项/);assert.match(html,/部分匹配 1/);assert.match(html,/不能安全放入/);
assert.match(html,/会盖住达人反应/);assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
assert.match(vm.runInContext('scriptCoveragePanel(c,false)',ctx),/早晨复核/);
// A partially fulfilled parent can have real V2 clips without being complete.
ctx.c={dialogue:{required:0,complete:0,partial:0,missing:0,items:[]},shots:{required:1,coveredMain:0,inserted:0,unresolved:1,physicalInserts:1,items:[{
 id:'shot-1',text:'摘电池并表现变慢',status:'partial',reason:'还有动作未找到',
 componentCoverage:[{id:'shot-1:c1',text:'摘电池<img src=x>',status:'inserted',reason:'已安全放入',placement:{track:2,at:7}},
                    {id:'shot-1:c2',text:'变慢',status:'unmatched',reason:'缺少可靠画面'}],
 remainingComponents:[{id:'shot-1:c2',text:'变慢',status:'unmatched',reason:'缺少可靠画面'}],
 nonVisualObligations:[{text:'续航18公里<script>',kind:'claim',status:'needs-audio-review'}],placements:[{track:2,at:7}]
}]} ,scriptConflicts:[{scene:'山脚<script>',field:'时间',reason:'不一致，未选值',dialogue:{text:'现在16:00<img>',value:'16:00'},shot:{text:'手机14:00<img>',value:'14:00'}}]};
const components=vm.runInContext('scriptCoveragePanel(c)',ctx);
for(const phrase of ['仍有缺项','已补充 0','待解决 1','实际新增第二轨 1 段','不等于完成要求 1 项','已补入第二轨（候选）','草稿 7.00s','仍缺内容','变慢','非视觉义务','续航18公里','不据此认定整项完成','脚本数值冲突','台词原句：现在16:00','画面原句：手机14:00'])assert.ok(components.includes(phrase),phrase);
assert.doesNotMatch(components,/<img|<script/);assert.match(components,/&lt;img/);assert.match(components,/&lt;script/);
assert.doesNotMatch(vm.runInContext('scriptCoveragePanel(c,false)',ctx),/台词原句|画面要素进度/,'full details stay in the review panel');
ctx.c.shots.physicalInserts=0;ctx.c.shots.items[0].placements[0].existing=true;ctx.c.shots.items[0].componentCoverage[0].placement.existing=true;
const reused=vm.runInContext('scriptCoveragePanel(c)',ctx);
assert.match(reused,/实际新增第二轨 0 段/);assert.match(reused,/已有片段，未新增/);assert.match(reused,/其中新增 0 段/);
delete ctx.c.shots.physicalInserts;assert.doesNotMatch(vm.runInContext('scriptCoveragePanel(c)',ctx),/实际新增第二轨/,'old reports do not invent an insert total');
console.log('PASS: partial components, real versus existing V2, remaining/nonvisual obligations, numeric conflicts, legacy reports and escaped source text');
