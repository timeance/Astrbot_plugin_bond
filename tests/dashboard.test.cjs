const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
function harness(timerOverrides={},deferredEndpoints=[]){
 let ui;const pending=[];const pendingGets=[];const reads=[];
 const bridge={
  apiGet:async(endpoint,params)=>{
   reads.push({endpoint,params});
   if(deferredEndpoints.includes(endpoint)||endpoint==='prompts'||endpoint==='member'||(endpoint==='diagnostics'&&params.action==='export'))return new Promise(resolve=>pendingGets.push({endpoint,params,resolve}));
   if(endpoint==='settings')return {success:true,fields:[],values:{},revision:'settings-initial',providers:[]};
   if(endpoint==='bots')return {success:true,bots:[]};
   return {success:true,groups:[],users:[],logs:[{id:params.bot_id}]};
  },
  apiPost:(endpoint,params)=>new Promise(resolve=>pending.push({endpoint,params,resolve}))
 };
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../pages/dashboard/app.js'),'utf8'),{
  Vue:{ref:value=>({value}),computed:fn=>({get value(){return fn()}}),onMounted:()=>{},onUnmounted:()=>{},createApp:options=>({mount(){ui=options.setup()}})},window:{AstrBotPluginPage:bridge},document:{documentElement:{dataset:{bondTheme:'dark'}}},console,setTimeout,clearTimeout,...timerOverrides
 });
 ui.bots.value=[{bot_id:'10001',scope_mode:'per_group'},{bot_id:'20002',scope_mode:'per_group'}];ui.selectedBot.value='10001';
 return {ui,pending,pendingGets,reads};
}
for(const operation of ['backup','restore']) test(`switching during ${operation} releases flags and ignores old completion`,async()=>{
 const {ui,pending}=harness();
 let old;
 if(operation==='backup')old=ui.backupScope();
 else {ui.restoreName.value='old.db';ui.prepareRestore();old=ui.confirmRestore();}
 assert.equal(ui.backupLoading.value,true);
 ui.selectedBot.value='20002';await ui.selectBot();
 assert.equal(ui.backupLoading.value,false);
 const current=ui.backupScope();assert.equal(ui.backupLoading.value,true);
 pending[0].resolve({success:true,backup_name:'stale.db'});await old;
 assert.equal(ui.backupLoading.value,true);assert.equal(ui.latestBackup.value,'');
 pending[1].resolve({success:true,backup_name:'current.db'});await current;
 assert.equal(ui.backupLoading.value,false);assert.equal(ui.latestBackup.value,'current.db');
});

test('prompt target changes ignore stale kind and group responses',async()=>{
 const {ui,pendingGets}=harness();
 ui.groups.value=[{group_id:'10001',count:1}];
 ui.promptKind.value='behavior';ui.promptLevel.value='global';
 const old=ui.loadPrompts();
 assert.equal(pendingGets.length,1);
 ui.promptKind.value='assessment';ui.promptLevel.value='group';ui.promptGroupId.value='10001';
 const current=ui.loadPrompts();
 assert.equal(pendingGets.length,2);
 pendingGets[0].resolve({success:true,settings:{kind:'behavior',default:'旧',global:{mode:'custom',template:'旧模板'},groups:{}},resolved:'旧生效'});
 await old;
 assert.equal(ui.promptResolved.value,'');
 pendingGets[1].resolve({success:true,settings:{kind:'assessment',default:'默认',global:{mode:'default',template:''},groups:{}},resolved:'新生效'});
 await current;
 assert.equal(ui.promptResolved.value,'新生效');
 assert.equal(ui.promptTemplate.value,'');
 assert.equal(pendingGets[1].params.kind,'assessment');
 assert.equal(pendingGets[1].params.level,'group');
 assert.equal(pendingGets[1].params.group_id,'10001');
});

test('member target changes ignore stale user response',async()=>{
 const {ui,pendingGets}=harness();
 const old=ui.openMember({group_id:'10001',user_id:'u-old'});
 const current=ui.openMember({group_id:'10001',user_id:'u-current'});
 assert.equal(pendingGets.length,2);
 pendingGets[0].resolve({success:true,member:{group_id:'10001',user_id:'u-old',familiarity:1,affinity:2,trust:3,relation_title:'旧成员',tags:[],impression:''}});
 await old;
 assert.equal(ui.memberDetail.value,null);
 pendingGets[1].resolve({success:true,member:{group_id:'10001',user_id:'u-current',familiarity:4,affinity:5,trust:6,relation_title:'当前成员',tags:[],impression:''}});
 await current;
 assert.equal(ui.memberDetail.value.user_id,'u-current');
 assert.equal(ui.memberDetail.value.relation_title,'当前成员');
});

test('saving a default prompt sends an empty template',async()=>{
 const {ui,pending,pendingGets}=harness();
 ui.promptKind.value='behavior';ui.promptLevel.value='global';
 ui.promptMode.value='default';ui.promptTemplate.value='旧自定义内容';
 const save=ui.savePrompt('save');
 assert.equal(pending.length,1);
 assert.equal(pending[0].params.template,'');
 assert.equal(pending[0].params.mode,'default');
 pending[0].resolve({success:true,resolved:'默认生效'});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(pendingGets.length,1);
 pendingGets[0].resolve({success:true,settings:{kind:'behavior',default:'默认',global:{mode:'default',template:''},groups:{}},resolved:'默认生效'});
 await save;
 assert.equal(ui.promptTemplate.value,'');
});

test('undo completion from an old scope cannot update current state',async()=>{
 const {ui,pending}=harness();
 const old=ui.openUndo({id:1});
 ui.selectedBot.value='20002';
 await ui.selectBot();
 pending[0].resolve({success:false,error:'旧空间错误'});
 await old;
 assert.equal(ui.errorMsg.value,'');
 assert.equal(ui.undoPreview.value,null);
});

test('snapshot handlers are called explicitly from the template',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../pages/dashboard/app.js'),'utf8');
 assert.doesNotMatch(source,/@(?:click|change)="(?:loadPrompts|loadEvaluation)"/);
});

for(const operation of ['switch','restore']) test(`log tab reloads after ${operation}`,async()=>{
 const {ui,pending,reads}=harness();ui.tab.value='logs';
 if(operation==='switch'){ui.selectedBot.value='20002';await ui.selectBot();}
 else {ui.restoreName.value='saved.db';ui.prepareRestore();const done=ui.confirmRestore();pending[0].resolve({success:true});await done;}
 const request=reads.find(item=>item.endpoint==='logs');
 assert.ok(request);assert.equal(request.params.bot_id,ui.selectedBot.value);
 assert.equal(ui.logs.value[0].id,ui.selectedBot.value);assert.equal(ui.tab.value,'logs');
});


test('global settings save sends only changed fields and survives switching Bot',async()=>{
 const {ui,pending,reads}=harness();
 ui.tab.value='settings';
 ui.detailSettings.value={revision:'v1',values:{timezone:'UTC',memory_days:4},fields:[]};
 ui.detailValues.value={timezone:'UTC',memory_days:5};
 const save=ui.saveSettings();
 assert.equal(JSON.stringify(pending[0].params.values),JSON.stringify({memory_days:5}));
 assert.equal(pending[0].params.bot_id,undefined);assert.equal(pending[0].params.scope_mode,undefined);
 ui.selectedBot.value='20002';await ui.selectBot();
 assert.equal(ui.detailValues.value.memory_days,5);assert.equal(ui.detailLoading.value,true);
 assert.equal(reads.some(item=>item.endpoint==='settings'),false);
 pending[0].resolve({success:true,revision:'v2',values:{memory_days:5},fields:[]});await save;
 assert.equal(ui.detailSettings.value.revision,'v2');
 assert.equal(ui.detailLoading.value,false);
});

for(const operation of ['saveRelation','saveProfile','refreshImpression']) test(`closing during ${operation} releases the editor and ignores the old session`,async()=>{
 const {ui,pending,pendingGets}=harness();
 const member={group_id:'10001',user_id:'u-same',familiarity:4,affinity:5,trust:6,tags:[],impression:''};
 const first=ui.openMember(member);pendingGets[0].resolve({success:true,member});await first;
 const old=ui[operation]();
 assert.equal(ui.memberSaveLoading.value || ui.memberLoading.value,true);
 ui.closeMember();
 assert.equal(ui.memberSaveLoading.value,false);assert.equal(ui.memberLoading.value,false);
 const reopened=ui.openMember(member);pendingGets[1].resolve({success:true,member});await reopened;
 const current=ui.saveProfile();assert.equal(ui.memberSaveLoading.value,true);
 pending[0].resolve({success:false,error:'stale failure'});await old;
 assert.equal(ui.memberSaveLoading.value,true);assert.equal(ui.memberNotice.value,'');assert.equal(ui.errorMsg.value,'');
 pending[1].resolve({success:true});await new Promise(resolve=>setImmediate(resolve));
 pendingGets[2].resolve({success:true,member});await current;
 assert.equal(ui.memberSaveLoading.value,false);assert.equal(ui.memberNotice.value,'印象和标签已保存');
});

test('removed integration has no UI or API calls',()=>{
 const {ui,reads}=harness();
 assert.equal(ui.loadIntegration,undefined);
 assert.equal(reads.some(x=>x.endpoint==='livingmemory'),false);
});


test('group whitelist uses an array and an empty field restores all groups',async()=>{
 const {ui,pending}=harness();
 ui.detailSettings.value={values:{group_whitelist:[]},revision:'before'};
 ui.detailValues.value={group_whitelist:[]};
 ui.updateListSetting('group_whitelist','10001, 20002，30003');
 assert.deepEqual(Array.from(ui.detailValues.value.group_whitelist),['10001','20002','30003']);
 const save=ui.saveSettings();
 assert.equal(pending[0].endpoint,'settings');
 assert.deepEqual(Array.from(pending[0].params.values.group_whitelist),['10001','20002','30003']);
 pending[0].resolve({success:true,fields:[],values:{group_whitelist:['10001','20002','30003']},revision:'after'});
 await save;
 ui.updateListSetting('group_whitelist','   ');
 const clear=ui.saveSettings();
 assert.deepEqual(Array.from(pending[1].params.values.group_whitelist),[]);
 pending[1].resolve({success:true,fields:[],values:{group_whitelist:[]},revision:'cleared'});
 await clear;
 assert.deepEqual(Array.from(ui.detailValues.value.group_whitelist),[]);
});


test('temporary expiry refreshes the member and ignores a closed detail',async()=>{
 let tick;
 const {ui,pendingGets,reads}=harness({setTimeout:fn=>{tick=fn;return 1},clearTimeout:()=>{}});
 const open=ui.openMember({group_id:'10001',user_id:'u'});
 pendingGets[0].resolve({success:true,member:{group_id:'10001',user_id:'u',familiarity:90,affinity:65,trust:-40,temporary:{active:true,expires_at:Date.now()/1000-1}}});
 await open;
 assert.equal(ui.relationEditMode.value,'temporary');
 const expired=tick();
 assert.equal(pendingGets.length,2);
 pendingGets[1].resolve({success:true,member:{group_id:'10001',user_id:'u',familiarity:30,affinity:20,trust:10,temporary:{active:false}}});
 await expired;
 assert.equal(ui.relationForm.value.affinity,20);
 assert.equal(ui.relationEditMode.value,'permanent');
 assert.ok(reads.some(row=>row.endpoint==='users'));
 ui.closeMember();
 const before=pendingGets.length;
 await tick();
 assert.equal(pendingGets.length,before);
});


test('global list can refresh names without opening member detail',async()=>{
 const {ui,pending,reads}=harness();
 ui.selectedMode.value='global';
 const work=ui.refreshNames({group_id:'__global__',user_id:'8008'});
 assert.equal(pending[0].endpoint,'member');
 assert.equal(pending[0].params.group_id,'');
 assert.equal(pending[0].params.user_id,'8008');
 pending[0].resolve({success:true,member:{nickname:'新昵称'}});
 await work;
 assert.ok(reads.some(r=>r.endpoint==='users'));
 assert.equal(ui.memberDetail.value,null);
 assert.equal(ui.memberSaveLoading.value,false);
});

test('all-groups global detail sends an empty real-group selector',async()=>{
 const {ui,pendingGets}=harness();
 ui.selectedMode.value='global';
 const work=ui.openMember({group_id:'__global__',user_id:'8008'});
 assert.equal(pendingGets[0].params.group_id,'');
 pendingGets[0].resolve({success:true,member:{group_id:'',user_id:'8008'}});
 await work;
 assert.equal(ui.memberDetail.value.user_id,'8008');
});


test('row nickname refresh cannot overwrite a newly opened member session',async()=>{
 const {ui,pending,pendingGets}=harness();
 const refresh=ui.refreshNames({group_id:'10001',user_id:'8008'});
 const detail=ui.openMember({group_id:'10001',user_id:'8009'});
 pendingGets[0].resolve({success:true,member:{group_id:'10001',user_id:'8009',nickname:'当前成员'}});
 await detail;
 pending[0].resolve({success:false,error:'旧请求失败'});
 await refresh;
 assert.equal(ui.memberDetail.value.nickname,'当前成员');
 assert.equal(ui.errorMsg.value,'');
});


test('diagnostic export from old scope cannot prepare a download',async()=>{
 let downloads=0;
 const {ui,pendingGets}=harness({
  URL:{createObjectURL:()=>{downloads++;return 'blob:synthetic';},revokeObjectURL:()=>{}},
  Blob:class {}
 });
 const old=ui.diagnosticRequest('export');
 assert.equal(pendingGets.length,1);
 ui.selectedBot.value='20002';await ui.selectBot();
 pendingGets[0].resolve({success:true,diagnostics:{rows:[{private:'old-scope'}]}});
 await old;
 assert.equal(downloads,0);
 assert.equal(ui.diagnosticDownload.value,'');
 assert.equal(ui.diagnosticLoading.value,false);
});

test('diagnostic export keeps selected scope and releases previous download',async()=>{
 let downloads=0, released=0;
 const {ui,pendingGets}=harness({
  URL:{createObjectURL:()=>{downloads++;return 'blob:synthetic';},revokeObjectURL:()=>{released++;}},
  Blob:class {}
 });
 const result=ui.diagnosticRequest('export');
 assert.equal(pendingGets[0].params.scope_mode,'per_group');
 pendingGets[0].resolve({success:true,diagnostics:{rows:[]}});
 await result;
 assert.equal(downloads,1);
 assert.equal(ui.diagnosticDownload.value,'blob:synthetic');
 ui.selectedBot.value='20002';await ui.selectBot();
 assert.equal(released,1);
 assert.equal(ui.diagnosticDownload.value,'');
});

test('diagnostic clear requires local confirmation and ignores late old-scope completion',async()=>{
 const {ui,pending}=harness();
 await ui.diagnosticRequest('clear');
 assert.equal(ui.diagnosticConfirm.value,true);
 assert.equal(pending.length,0);
 const clear=ui.diagnosticRequest('clear');
 assert.equal(pending.length,1);
 assert.equal(pending[0].endpoint,'diagnostics');
 assert.equal(pending[0].params.bot_id,'10001');
 assert.equal(pending[0].params.action,'clear');
 ui.selectedBot.value='20002';await ui.selectBot();
 pending[0].resolve({success:true,diagnostics:{cleared:true}});
 await clear;
 assert.equal(ui.diagnosticStats.value,null);
 assert.equal(ui.diagnosticLoading.value,false);
});


test('member list requests server pagination and accepts corrected page metadata',async()=>{
 const {ui,pendingGets}=harness({},['users']);
 assert.equal(ui.userPageSize.value,50);assert.equal(ui.includeZeroUsers.value,false);
 ui.userPage.value=8;
 const work=ui.fetchUsers();const request=pendingGets[0];
 assert.equal(request.params.page,8);assert.equal(request.params.page_size,50);assert.equal(request.params.include_zero,'0');
 request.resolve({success:true,users:[{user_id:'8001'}],total:51,matched_count:60,collapsed_count:9,zero_count:9,page:2,pages:2});await work;
 assert.equal(ui.userPage.value,2);assert.equal(ui.userPages.value,2);assert.equal(ui.userTotal.value,51);assert.equal(ui.userMatchedCount.value,60);assert.equal(ui.userCollapsedCount.value,9);
 assert.equal(ui.visibleUsers.value.length,1);assert.equal(ui.loading.value,false);
});

test('member filters and sizes reset page and search reaches server including zero members',async()=>{
 const {ui,pendingGets}=harness({},['users']);
 for(const size of [30,50,100,200]){
  ui.userPage.value=9;ui.userPageSize.value=size;ui.filterGroup.value='123';ui.filterUser.value='008';
  const work=ui.changeListFilter();const request=pendingGets.at(-1);
  assert.equal(request.params.page,1);assert.equal(request.params.page_size,size);assert.equal(request.params.search,'008');assert.equal(request.params.group_id,'123');
  request.resolve({success:true,users:[{user_id:'8008'}],total:1,matched_count:1,collapsed_count:0,page:1,pages:1});await work;
  assert.equal(ui.visibleUsers.value[0].user_id,'8008');
 }
 ui.includeZeroUsers.value=true;const expanded=ui.resetUserPage();
 assert.equal(pendingGets.at(-1).params.include_zero,'1');pendingGets.at(-1).resolve({success:true,users:[]});await expanded;
});

test('member page request sequence rejects stale same-scope responses and flags',async()=>{
 const {ui,pendingGets}=harness({},['users']);
 const old=ui.fetchUsers();ui.userPages.value=3;const current=ui.changeUserPage(2);
 pendingGets[0].resolve({success:true,users:[{user_id:'old'}],total:99,page:1,pages:3});await old;
 assert.equal(ui.users.value.length,0);assert.equal(ui.loading.value,true);
 pendingGets[1].resolve({success:true,users:[{user_id:'current'}],total:99,matched_count:99,page:2,pages:3});await current;
 assert.equal(ui.users.value[0].user_id,'current');assert.equal(ui.userPage.value,2);assert.equal(ui.loading.value,false);
});

test('switching scope resets member filters and rejects old page response',async()=>{
 const deferred=['users'];const {ui,pendingGets}=harness({},deferred);
 ui.userPage.value=5;ui.includeZeroUsers.value=true;ui.filterUser.value='8001';
 const old=ui.fetchUsers();ui.selectedBot.value='20002';const switched=ui.selectBot();
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(ui.userPage.value,1);assert.equal(ui.filterUser.value,'');assert.equal(ui.includeZeroUsers.value,false);
 assert.equal(pendingGets[1].params.bot_id,'20002');assert.equal(pendingGets[1].params.page,1);
 pendingGets[1].resolve({success:true,users:[{user_id:'new-bot'}],total:1,page:1,pages:1});await switched;
 pendingGets[0].resolve({success:true,users:[{user_id:'old-bot'}],total:99,page:5,pages:9});await old;
 assert.equal(ui.users.value[0].user_id,'new-bot');assert.equal(ui.userPage.value,1);
});

test('evaluation entry shares log state and requests only the chosen dimension and assessment source',async()=>{
 const {ui,pendingGets}=harness({},['logs']);
 ui.tab.value='evaluation';ui.logDimension.value='trust';
 const work=ui.showEvaluationChanges();assert.equal(ui.tab.value,'logs');assert.equal(ui.logSource.value,'judge');
 assert.equal(pendingGets[0].params.dimension,'trust');assert.equal(pendingGets[0].params.source,'judge');
 pendingGets[0].resolve({success:true,logs:[{id:1,delta:0,relation_delta:{trust:-1}}]});await work;
 assert.equal(ui.logs.value.length,1);assert.equal(ui.logDimension.value,'trust');
});

test('legacy log dimensions stay unknown and known zeros remain zero',()=>{
 const {ui}=harness();
 const old=ui.logDeltaParts({delta:2,relation_delta:{}},'all');
 assert.deepEqual(Array.from(old,p=>p.value),[null,2,null]);
 assert.equal(ui.fmtKnownDelta(old[0].value),'未知');
 const modern=ui.logDeltaParts({delta:9,relation_delta:{familiarity:1,affinity:0,trust:-2}},'all');
 assert.deepEqual(Array.from(modern,p=>p.value),[1,0,-2]);
 assert.equal(ui.fmtKnownDelta(modern[1].value),'0');
 ui.logDimension.value='familiarity';assert.equal(ui.logDeltaParts({delta:1,relation_delta:{familiarity:2}}).length,1);
});

test('log dimension requests reject late old-filter results',async()=>{
 const {ui,pendingGets}=harness({},['logs']);ui.tab.value='logs';
 const old=ui.fetchLogs();ui.logDimension.value='familiarity';const current=ui.fetchLogs();
 pendingGets[0].resolve({success:true,logs:[{id:1}]});await old;
 assert.equal(ui.logs.value.length,0);assert.equal(ui.loading.value,true);
 pendingGets[1].resolve({success:true,logs:[{id:2}]});await current;
 assert.equal(ui.logs.value[0].id,2);assert.equal(ui.loading.value,false);
});

test('dimension view never adds a partial-undo parameter',async()=>{
 const {ui,pending}=harness();ui.logDimension.value='familiarity';
 const work=ui.openUndo({id:88});assert.equal(pending[0].endpoint,'undo');
 assert.equal(pending[0].params.id,88);assert.equal(pending[0].params.dimension,undefined);
 pending[0].resolve({success:true,preview:{current:3,after:0}});await work;
 const undone=ui.confirmUndo();assert.equal(pending[1].params.id,88);assert.equal(pending[1].params.dimension,undefined);
 pending[1].resolve({success:true});await undone;
});


test('saving configured concurrency keeps the actual running limit unchanged until reload',async()=>{
 const {ui,pending,pendingGets}=harness({},['models']);
 const config={enabled:false,small_provider_id:'',large_provider_id:'',assessment_mode:'tiered',small_timeout_sec:20,large_timeout_sec:30,cooldown_sec:120,per_user_daily_large_cap:2,per_bot_daily_large_cap:20};
 const load=ui.loadEvaluation();
 pendingGets[0].resolve({success:true,revision:'models-v1',configured:config,active:config,max_concurrent_assessments:2,active_max_concurrent_assessments:2});await load;
 ui.maxConcurrentAssessments.value=5;assert.equal(ui.activeMaxConcurrentAssessments.value,2);
 const save=ui.saveModels();assert.equal(pending[0].params.max_concurrent_assessments,5);
 assert.equal(pending[0].params.revision,'models-v1');assert.equal(pending[0].params.config.assessment_mode,undefined);
 pending[0].resolve({success:true,revision:'models-v2',restart_required:true});await save;
 assert.equal(ui.modelRevision.value,'models-v2');
 assert.equal(ui.maxConcurrentAssessments.value,5);assert.equal(ui.activeMaxConcurrentAssessments.value,2);
 const reload=ui.loadEvaluation();pendingGets[1].resolve({success:true,revision:'models-v2',configured:config,active:config,max_concurrent_assessments:5,active_max_concurrent_assessments:2});await reload;
 assert.equal(ui.maxConcurrentAssessments.value,5);assert.equal(ui.activeMaxConcurrentAssessments.value,2);
 const source=fs.readFileSync(path.join(__dirname,'../pages/dashboard/app.js'),'utf8');
 assert.match(source,/同时评估人数<\/dt><dd>{{ activeMaxConcurrentAssessments/);
});


test('returning to the shared log table reloads changed evaluation dimension even with cached rows',async()=>{
 const {ui,pendingGets}=harness({},['logs']);
 ui.logs.value=[{id:'cached-affinity'}];ui.tab.value='evaluation';ui.logDimension.value='familiarity';
 ui.switchTab('logs');assert.equal(pendingGets[0].params.dimension,'familiarity');
 assert.equal(ui.logs.value.length,0);pendingGets[0].resolve({success:true,logs:[]});
 await new Promise(resolve=>setImmediate(resolve));assert.equal(ui.loading.value,false);
});


test('section defaults are staged and preserve other sections, identity and models',async()=>{
 const {ui,pending}=harness();
 const values={relation_mode:'realistic','decay.half_life_base':10,'relation_daily_caps.affinity_up':12,
  'decay.familiarity_enabled':false,'decay.familiarity_half_life':90,
  'decay.familiarity_baseline':20,'decay.affinity_baseline':15,'decay.trust_baseline':20,
  timezone:'UTC',master_ids:'8001','command.ranking_access':'master','judge.provider_id':'summary',
  'assessment.small_provider_id':'chosen','inject.memory_days':4};
 ui.detailSettings.value={revision:'before',values:{...values},fields:[
  {path:'relation_mode',group:'basic',default:'galgame'},
  {path:'decay.familiarity_enabled',group:'rules',default:true},
  {path:'decay.familiarity_half_life',group:'rules',default:60},
  {path:'decay.familiarity_baseline',group:'rules',default:0},
  {path:'decay.affinity_baseline',group:'rules',default:0},
  {path:'decay.trust_baseline',group:'rules',default:0},
  {path:'decay.half_life_base',group:'rules',default:30},
  {path:'relation_daily_caps.affinity_up',group:'rules',default:10},
  {path:'timezone',group:'rules',default:'Asia/Shanghai'},
  {path:'master_ids',group:'basic',default:''},
  {path:'command.ranking_access',group:'commands',default:'all'},
  {path:'judge.provider_id',group:'memory',default:''},
  {path:'inject.memory_days',group:'memory',default:7}
 ]};ui.detailValues.value={...values};
 ui.detailLoading.value=true;ui.restoreSectionDefaults('rules');
 assert.equal(ui.detailValues.value['decay.half_life_base'],10);
 ui.detailLoading.value=false;ui.restoreSectionDefaults('basic');
 assert.equal(ui.detailValues.value.master_ids,'8001');
 ui.restoreSectionDefaults('rules');
 assert.equal(ui.detailValues.value.relation_mode,'realistic');
 assert.equal(ui.detailValues.value['relation_daily_caps.affinity_up'],10);
 assert.equal(ui.detailValues.value['decay.familiarity_enabled'],true);
 assert.equal(ui.detailValues.value['decay.familiarity_half_life'],60);
 assert.equal(ui.detailSettings.value.values['decay.familiarity_enabled'],false);
 assert.equal(ui.detailSettings.value.values['decay.familiarity_half_life'],90);
 for(const dimension of ['familiarity','affinity','trust'])assert.equal(ui.detailValues.value[`decay.${dimension}_baseline`],0);
 assert.equal(ui.detailSettings.value.values['decay.trust_baseline'],20);
 assert.equal(ui.detailValues.value['command.ranking_access'],'master');
 assert.equal(ui.detailValues.value['assessment.small_provider_id'],'chosen');
 assert.equal(ui.detailSettings.value.values.relation_mode,'realistic');
 assert.equal(pending.length,0);
 const save=ui.saveSettings();
 assert.equal(JSON.stringify(pending[0].params.values),JSON.stringify({'decay.half_life_base':30,'relation_daily_caps.affinity_up':10,'decay.familiarity_enabled':true,'decay.familiarity_half_life':60,'decay.familiarity_baseline':0,'decay.affinity_baseline':0,'decay.trust_baseline':0,timezone:'Asia/Shanghai'}));
 pending[0].resolve({success:true,revision:'after',values:{...ui.detailValues.value},fields:ui.detailSettings.value.fields});await save;
 assert.match(ui.detailNotice.value,/重载/);
 ui.restoreSectionDefaults('memory');
 assert.equal(ui.detailValues.value['judge.provider_id'],'summary');
 assert.equal(ui.detailValues.value['inject.memory_days'],7);
});

test('editing mode settings keeps the loaded runtime summary unchanged',()=>{
 const {ui}=harness();
 const values={relation_mode:'realistic','relation_daily_caps.affinity_up':12,'relation_daily_caps.affinity_down':8,
 'relation_daily_caps.familiarity_up':5,'relation_daily_caps.familiarity_down':0,
 'relation_daily_caps.trust_up':2,'relation_daily_caps.trust_down':5,
 'familiarity_growth.under10':1.5,'economy.level_mult.youhao':.75,
 'decay.familiarity_enabled':true,'decay.familiarity_half_life':60,
 'decay.enabled':true,'decay.half_life_base':30,'decay.trust_enabled':true,'decay.trust_half_life':90,
 'decay.familiarity_baseline':20,'decay.affinity_baseline':15,'decay.trust_baseline':20};
 ui.detailSettings.value={values:{...values},active_values:{...values},fields:[]};ui.detailValues.value={...values};
 ui.detailValues.value.relation_mode='galgame';
 ui.detailValues.value['economy.enabled']=false;
 assert.equal(ui.activeRuleSummary.value.mode,'基础 ×1');
 assert.equal(ui.activeRuleSummary.value.familiarity,'60 天半衰期');
 assert.equal(ui.activeRuleSummary.value.affinity,'30 天基础半衰期');
 assert.equal(ui.activeRuleSummary.value.trust,'90 天半衰期');
 assert.equal(JSON.stringify(ui.activeRuleSummary.value.baselines),JSON.stringify({familiarity:20,affinity:15,trust:20}));
 assert.equal(ui.detailValues.value['decay.trust_baseline'],20);
 ui.detailValues.value['decay.trust_baseline']=50;
 assert.equal(ui.activeRuleSummary.value.baselines.trust,20);
 ui.detailValues.value['decay.familiarity_half_life']=90;
 ui.detailValues.value['decay.familiarity_enabled']=false;
 assert.equal(ui.activeRuleSummary.value.familiarity,'60 天半衰期');
 ui.detailSettings.value.active_values['decay.familiarity_enabled']=false;
 assert.equal(ui.activeRuleSummary.value.familiarity,'关闭');
 assert.equal(ui.activeRuleSummary.value.caps[1].up,12);
 assert.equal(ui.detailSettings.value.values.relation_mode,'realistic');
 assert.equal(ui.detailValues.value['relation_daily_caps.affinity_up'],12);
 ui.detailValues.value.relation_mode='realistic';
});

test('evaluation defaults preserve selected models and mode and save only allowed parameters',async()=>{
 const {ui,pending}=harness();
 ui.modelConfig.value={enabled:true,small_provider_id:'chosen',large_provider_id:'review',assessment_mode:'single',cooldown_sec:120,window_sec:90,window_max_calls:1};
 ui.restoreEvaluationDefaults();
 assert.equal(ui.modelConfig.value.small_provider_id,'chosen');
 assert.equal(ui.modelConfig.value.large_provider_id,'review');
 assert.equal(ui.modelConfig.value.assessment_mode,'single');
 assert.equal(ui.modelConfig.value.enabled,true);
 assert.equal(ui.modelConfig.value.window_sec,300);assert.equal(ui.modelConfig.value.window_max_calls,5);
 assert.equal(ui.modelConfig.value.per_user_daily_small_cap,30);assert.equal(ui.modelConfig.value.per_user_daily_large_cap,8);
 assert.equal(ui.modelConfig.value.per_bot_daily_small_cap,300);assert.equal(ui.modelConfig.value.per_bot_daily_large_cap,100);
 assert.equal(ui.modelConfig.value.context_chars,4000);assert.equal(ui.modelConfig.value.context_events,8);
 assert.equal(pending.length,0);
 ui.modelRevision.value='before';
 const save=ui.saveModels();const payload=pending[0].params.config;
 for(const key of ['enabled','assessment_mode','small_provider_id','large_provider_id','cooldown_sec'])assert.equal(payload[key],undefined);
 assert.equal(payload.window_sec,300);assert.equal(payload.context_events,8);
 pending[0].resolve({success:true,restart_required:true});await save;
 assert.match(ui.modelNotice.value,/重载/);
});

for(const [key,value] of [['window_sec',0],['window_max_calls',1.5],['per_user_daily_small_cap',-1],['context_chars',''],['context_events',NaN],['context_turns',21],['context_chars',20001],['context_days',91],['context_events',21]])test(`invalid ${key} is rejected before saving`,async()=>{
 const {ui,pending}=harness();ui.restoreEvaluationDefaults();ui.modelRevision.value='before';ui.modelConfig.value[key]=value;
 await ui.saveModels();assert.equal(pending.length,0);assert.equal(ui.modelNoticeError.value,true);
});

test('rule and assessment controls keep a single entry and explain saved versus runtime',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../pages/dashboard/app.js'),'utf8');
 assert.doesNotMatch(source,/fillRecommendedRules|填入写实推荐|填入 Galgame 推荐|v-model.number="modelConfig.cooldown_sec"/);
 assert.match(source,/UTC 00:00/);assert.match(source,/当前关系设置/);
 assert.match(source,/<table class="xx-table" aria-label="当前关系设置">/);
 assert.match(source,/activeRuleSummary\[cap.key\]/);
 assert.match(source,/activeRuleSummary.baselines\[cap.key\]/);
 assert.match(source,/:max="field.path.endsWith\('_baseline'\) \? 100 : undefined"/);
 assert.match(source,/历史不再计分/);assert.match(source,/不同互动可分别评分/);
 assert.match(source,/保存并重载|保存后重载/);
 assert.doesNotMatch(source,/<details open|:open="group.group/);
 assert.match(source,/aria-describedby="'eval-hint-' \+ field.key"/);
 const {ui}=harness();
 assert.ok(ui.evaluationFields.every(field=>field.hint && field.hint.length<100));
});

test('settings can open and save without a Bot or scope',async()=>{
 const {ui,pending,pendingGets,reads}=harness({},['settings']);
 ui.selectedBot.value='';ui.selectedMode.value='';ui.bots.value=[];
 ui.switchTab('settings');
 assert.equal(pendingGets.length,1);
 assert.equal(JSON.stringify(pendingGets[0].params),'{}');
 const values={'assessment.enabled':false,'assessment.small_provider_id':'','inject.enabled':false};
 pendingGets[0].resolve({success:true,fields:[],values,revision:'initial',providers:[]});
 await new Promise(resolve=>setImmediate(resolve));
 ui.detailValues.value['inject.enabled']=true;
 const save=ui.saveSettings();
 assert.equal(pending[0].endpoint,'settings');
 assert.equal(JSON.stringify(pending[0].params),JSON.stringify({values:{'inject.enabled':true},revision:'initial'}));
 pending[0].resolve({success:true,fields:[],values:{...values,'inject.enabled':true},revision:'saved',providers:[]});await save;
 assert.equal(ui.detailSettings.value.revision,'saved');assert.match(ui.detailNotice.value,/重载/);
 assert.equal(reads.some(item=>item.endpoint==='groups'),false);
});

test('switching Bot, refreshing its list and revisiting settings preserve global drafts',async()=>{
 const {ui,reads}=harness();
 ui.detailSettings.value={revision:'draft-base',values:{relation_mode:'realistic'},fields:[]};
 ui.detailValues.value={relation_mode:'galgame'};
 ui.tab.value='settings';ui.selectedBot.value='20002';await ui.selectBot();
 ui.switchTab('users');ui.switchTab('settings');
 await ui.fetchBots();
 assert.equal(ui.selectedBot.value,'');
 assert.equal(ui.detailSettings.value.revision,'draft-base');
 assert.equal(ui.detailValues.value.relation_mode,'galgame');
 assert.equal(reads.some(item=>item.endpoint==='settings'),false);
});

for(const staleSuccess of [true,false]) test(`global settings reject stale ${staleSuccess?'success':'failure'} responses`,async()=>{
 const {ui,pendingGets}=harness({},['settings']);
 const old=ui.loadSettings();const current=ui.loadSettings();
 pendingGets[0].resolve({success:staleSuccess,error:'旧错误',fields:[],values:{relation_mode:'old'},revision:'old'});await old;
 assert.equal(ui.detailSettings.value,null);assert.equal(ui.detailLoading.value,true);assert.equal(ui.detailNotice.value,'');
 pendingGets[1].resolve({success:true,fields:[],values:{relation_mode:'galgame'},revision:'current',providers:[]});await current;
 assert.equal(ui.detailSettings.value.revision,'current');assert.equal(ui.detailValues.value.relation_mode,'galgame');
 assert.equal(ui.detailLoading.value,false);
});

test('failed settings saves preserve the draft and reject overlapping requests',async()=>{
 const {ui,pending,reads}=harness();
 ui.detailSettings.value={revision:'old-revision',values:{relation_mode:'realistic'},fields:[]};
 ui.detailValues.value={relation_mode:'galgame'};
 const save=ui.saveSettings();await ui.saveSettings();await ui.loadSettings();
 assert.equal(pending.length,1);assert.equal(reads.length,0);
 pending[0].resolve({success:false,error:'配置已更新，请刷新后重试'});await save;
 assert.equal(ui.detailSettings.value.revision,'old-revision');assert.equal(ui.detailValues.value.relation_mode,'galgame');
 assert.equal(ui.detailNoticeError.value,true);assert.match(ui.detailNotice.value,/刷新/);assert.equal(ui.detailLoading.value,false);
});

test('enabled assessment requires a daily provider before settings are submitted',async()=>{
 const {ui,pending}=harness();
 ui.detailSettings.value={revision:'v1',values:{'assessment.enabled':false},fields:[]};
 ui.detailValues.value={'assessment.enabled':true,'assessment.small_provider_id':'   '};
 await ui.saveSettings();
 assert.equal(pending.length,0);assert.match(ui.detailNotice.value,/日常评估模型/);assert.equal(ui.detailNoticeError.value,true);
 ui.detailValues.value['assessment.small_provider_id']='synthetic-text-provider';
 const save=ui.saveSettings();assert.equal(pending.length,1);
 pending[0].resolve({success:true,revision:'v2',values:{...ui.detailValues.value},fields:[]});await save;
 assert.equal(ui.detailNoticeError.value,false);
});

test('provider options expose only IDs and model names and retain missing selections',()=>{
 const {ui}=harness();
 ui.detailSettings.value={fields:[],providers:[{id:'daily',model:'text-model',api_key:'synthetic-hidden'}]};
 ui.detailValues.value={'assessment.small_provider_id':'daily','assessment.large_provider_id':'missing'};
 const daily=ui.providerOptions('assessment.small_provider_id');
 assert.equal(daily.length,1);assert.equal(daily[0].label,'text-model · daily');
 assert.doesNotMatch(JSON.stringify(daily),/api_key|synthetic-hidden/);
 const review=ui.providerOptions('assessment.large_provider_id');
 assert.equal(review[1].id,'missing');assert.match(review[1].label,/暂不可用/);
 ui.detailSettings.value.providers=[];
 assert.equal(ui.providerOptions('assessment.small_provider_id')[0].id,'daily');
 ui.detailValues.value['assessment.small_provider_id']='';assert.equal(ui.providerOptions('assessment.small_provider_id').length,0);
});

test('list placeholders distinguish groups, ignored bots and owners',()=>{
 const {ui}=harness();
 assert.match(ui.listPlaceholder('group_whitelist'),/所有群/);
 assert.match(ui.listPlaceholder('ignored_bot_ids'),/机器人账号/);
 assert.match(ui.listPlaceholder('master_ids'),/主人账号/);
 assert.doesNotMatch(ui.listPlaceholder('ignored_bot_ids'),/所有群/);
 ui.updateListSetting('ignored_bot_ids','9001, 9002，9003');
 assert.deepEqual(Array.from(ui.detailValues.value.ignored_bot_ids),['9001','9002','9003']);
});


test('evaluation saves require a loaded revision and adopt the revision from each save',async()=>{
 const {ui,pending,pendingGets}=harness({},['models']);
 await ui.saveModels();assert.equal(pending.length,0);assert.match(ui.modelNotice.value,/刷新/);
 const load=ui.loadEvaluation();
 pendingGets[0].resolve({success:true,revision:'r1',configured:{},active:{}});await load;
 const save=ui.saveModels();assert.equal(pending[0].params.revision,'r1');
 for(const key of ['enabled','assessment_mode','small_provider_id','large_provider_id'])assert.equal(pending[0].params.config[key],undefined);
 pending[0].resolve({success:true,revision:'r2',restart_required:true});await save;
 const again=ui.saveModels();assert.equal(pending[1].params.revision,'r2');
 pending[1].resolve({success:false,error:'配置已更新'});await again;
 assert.equal(ui.modelRevision.value,'r2');assert.equal(ui.modelNoticeError.value,true);
});

test('evaluation ignores older same-scope loads including their revisions',async()=>{
 const {ui,pendingGets}=harness({},['models']);
 const old=ui.loadEvaluation();const current=ui.loadEvaluation();
 pendingGets[0].resolve({success:true,revision:'old',configured:{cooldown_sec:5},active:{}});await old;
 assert.equal(ui.modelRevision.value,'');assert.equal(ui.evaluationLoading.value,true);
 pendingGets[1].resolve({success:true,revision:'current',configured:{cooldown_sec:60},active:{}});await current;
 assert.equal(ui.modelRevision.value,'current');assert.equal(ui.modelConfig.value.cooldown_sec,60);
});

test('theme bootstrap defaults to light and restores either saved theme before CSS',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../pages/dashboard/index.html'),'utf8');
 const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
 assert.ok(html.indexOf(script)<html.indexOf('rel="stylesheet"'));
 assert.match(html,/<html[^>]*data-bond-theme="light"/);
 for(const stored of ['light','dark','invalid',null]){
  const document={documentElement:{dataset:{bondTheme:'light',theme:'dark'}}};
  vm.runInNewContext(script,{document,window:{localStorage:{getItem:key=>{assert.equal(key,'bond-theme');return stored;}}}});
  assert.equal(document.documentElement.dataset.bondTheme,stored==='dark'?'dark':'light');
  assert.equal(document.documentElement.dataset.theme,'dark');
 }
 const document={documentElement:{dataset:{bondTheme:'light'}}};
 vm.runInNewContext(script,{document,window:{get localStorage(){throw new Error('storage blocked');}}});
 assert.equal(document.documentElement.dataset.bondTheme,'light');
});

test('manual theme switching survives unavailable storage and ignores the host theme',()=>{
 const document={documentElement:{dataset:{bondTheme:'dark',theme:'light'}}};
 const {ui}=harness({document});
 ui.toggleTheme();assert.equal(ui.theme.value,'light');assert.equal(document.documentElement.dataset.bondTheme,'light');
 document.documentElement.dataset.theme='dark';
 assert.equal(ui.theme.value,'light');
 ui.toggleTheme();assert.equal(ui.theme.value,'dark');assert.equal(document.documentElement.dataset.bondTheme,'dark');
});

test('manual theme choice is persisted independently of the host',()=>{
 const writes=[];
 const {ui}=harness({window:{localStorage:{setItem:(...args)=>writes.push(args)}}});
 ui.toggleTheme();ui.toggleTheme();
 assert.deepEqual(writes,[['bond-theme','light'],['bond-theme','dark']]);
});

test('quick start layout, section defaults and theme styling match the page contract',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../pages/dashboard/app.js'),'utf8');
 const css=fs.readFileSync(path.join(__dirname,'../pages/dashboard/app.css'),'utf8');
 const toolbar=source.match(/<div class="xx-settings-toolbar xx-detail-toolbar">([\s\S]*?)<\/div>/)[1];
 assert.match(toolbar,/刷新设置/);assert.match(toolbar,/xx-btn-primary/);assert.doesNotMatch(toolbar,/填入/);
 assert.doesNotMatch(source,/fillRecommendedRules|recommendDialog|填入写实推荐|填入 Galgame 推荐/);
 assert.match(source,/restoreSectionDefaults\(group.group\)/);
 assert.doesNotMatch(source,/:open="group.group === 'basic'"/);
 assert.match(source,/详细设置 → 基础设置/);
 assert.match(source,/保存并重载插件后生效/);
 assert.doesNotMatch(source,/v-model="modelConfig.assessment_mode"|请在 AstrBot 的 Bond 插件配置中修改|亲近度基线固定为0/);
 assert.match(source,/暂无可用模型，请先在 AstrBot 配置文本模型。/);
 assert.match(css,/--xx-bg: #0B1120/);assert.match(css,/--xx-card: #121B30/);assert.match(css,/--xx-ink: #ECF2FF/);
 assert.match(css,/--xx-bg: #EFF3FA/);assert.match(css,/--xx-card: #FAFCFF/);assert.match(css,/--xx-ink: #10213A/);
 assert.match(css,/:root\[data-bond-theme="light"\]/);assert.doesNotMatch(css,/\[data-theme/);
 assert.match(css,/color-scheme: dark/);assert.match(css,/color-scheme: light/);assert.match(css,/color-scheme: inherit/);
 assert.match(css,/\.xx-page select/);
 const {ui}=harness();
 assert.equal(ui.fmtSignedNum(35),'+35');assert.equal(ui.fmtSignedNum(-20),'-20');assert.equal(ui.fmtSignedNum(0),'0');assert.equal(ui.fmtSignedNum(null),'-');
 assert.equal(JSON.stringify(ui.dimBarStyle('familiarity',65)),JSON.stringify({left:'0%',width:'65%'}));
 assert.equal(JSON.stringify(ui.dimBarStyle('affinity',60)),JSON.stringify({left:'50%',width:'30%'}));
 assert.equal(JSON.stringify(ui.dimBarStyle('trust',-40)),JSON.stringify({left:'30%',width:'20%'}));
});


test('today summary uses selected Bot and space and ignores late scope responses',async()=>{
 const {ui,pendingGets}=harness({},['today_summary']);
 const first=ui.fetchTodaySummary();
 ui.selectedMode.value='global';
 const second=ui.fetchTodaySummary();
 assert.equal(pendingGets[0].params.bot_id,'10001');
 assert.equal(pendingGets[1].params.scope_mode,'global');
 const current={initial:{used:4,limit:300},review:{used:1,limit:100},changed_members:2};
 pendingGets[1].resolve({success:true,summary:current});await second;
 pendingGets[0].resolve({success:true,summary:{changed_members:99}});await first;
 assert.equal(ui.todaySummary.value,current);assert.equal(ui.todayLoading.value,false);
});

test('today summary fails visibly without inventing zero counts',async()=>{
 const {ui,pendingGets}=harness({},['today_summary']);
 ui.todaySummary.value={changed_members:5};
 const call=ui.fetchTodaySummary();assert.equal(ui.todaySummary.value,null);
 pendingGets[0].resolve({success:false,error:'synthetic failure'});await call;
 assert.equal(ui.todaySummary.value,null);assert.match(ui.todayError.value,/刷新重试/);
 assert.equal(ui.todayLoading.value,false);
});

test('overview refresh reads today summary but member pagination does not',async()=>{
 const {ui,reads}=harness();
 ui.userPages.value=3;await ui.changeUserPage(2);
 assert.equal(reads.filter(read=>read.endpoint==='today_summary').length,0);
 await ui.refresh();
 assert.equal(reads.filter(read=>read.endpoint==='today_summary').length,1);
});


test('event reasons use Chinese without rewriting custom descriptions',()=>{
 const {ui}=harness();
 assert.equal(ui.displayReason('normal_question'),'日常问答');
 assert.equal(ui.displayReason('friendly'),'表达友好');
 assert.equal(ui.displayReason('管理员调整'),'管理员调整');
 assert.equal(ui.displayReason('unknown_event'),'unknown_event');
 assert.equal(ui.displayReason('__proto__'),'__proto__');
});
