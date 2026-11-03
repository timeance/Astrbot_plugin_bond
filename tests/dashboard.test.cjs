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
   return {success:true,groups:[],users:[],logs:[{id:params.bot_id}]};
  },
  apiPost:(endpoint,params)=>new Promise(resolve=>pending.push({endpoint,params,resolve}))
 };
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../pages/dashboard/app.js'),'utf8'),{
  Vue:{ref:value=>({value}),computed:fn=>({get value(){return fn()}}),onMounted:()=>{},onUnmounted:()=>{},createApp:options=>({mount(){ui=options.setup()}})},window:{AstrBotPluginPage:bridge},console,setTimeout,clearTimeout,...timerOverrides
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


test('detailed settings save sends changed fields only and ignores late completion',async()=>{
 const {ui,pending}=harness();
 ui.detailSettings.value={revision:'v1',values:{timezone:'UTC',memory_days:4},fields:[]};
 ui.detailValues.value={timezone:'UTC',memory_days:5};
 const save=ui.saveSettings();
 assert.equal(JSON.stringify(pending[0].params.values),JSON.stringify({memory_days:5}));
 ui.selectedBot.value='20002';await ui.selectBot();
 pending[0].resolve({success:true,revision:'v2',values:{memory_days:5},fields:[]});await save;
 assert.equal(ui.detailSettings.value,null);
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
 pendingGets[0].resolve({success:true,configured:config,active:config,max_concurrent_assessments:2,active_max_concurrent_assessments:2});await load;
 ui.maxConcurrentAssessments.value=5;assert.equal(ui.activeMaxConcurrentAssessments.value,2);
 const save=ui.saveModels();assert.equal(pending[0].params.max_concurrent_assessments,5);
 pending[0].resolve({success:true,restart_required:true});await save;
 assert.equal(ui.maxConcurrentAssessments.value,5);assert.equal(ui.activeMaxConcurrentAssessments.value,2);
 const reload=ui.loadEvaluation();pendingGets[1].resolve({success:true,configured:config,active:config,max_concurrent_assessments:5,active_max_concurrent_assessments:2});await reload;
 assert.equal(ui.maxConcurrentAssessments.value,5);assert.equal(ui.activeMaxConcurrentAssessments.value,2);
 const source=fs.readFileSync(path.join(__dirname,'../pages/dashboard/app.js'),'utf8');
 assert.match(source,/运行并发上限<\/dt><dd>{{ activeMaxConcurrentAssessments/);
});


test('returning to the shared log table reloads changed evaluation dimension even with cached rows',async()=>{
 const {ui,pendingGets}=harness({},['logs']);
 ui.logs.value=[{id:'cached-affinity'}];ui.tab.value='evaluation';ui.logDimension.value='familiarity';
 ui.switchTab('logs');assert.equal(pendingGets[0].params.dimension,'familiarity');
 assert.equal(ui.logs.value.length,0);pendingGets[0].resolve({success:true,logs:[]});
 await new Promise(resolve=>setImmediate(resolve));assert.equal(ui.loading.value,false);
});


test('recommended rules are staged without saving or changing identity, models and privacy',async()=>{
 const {ui,pending}=harness();
 ui.fillRecommendedRules();assert.equal(pending.length,0);
 const values={'decay.half_life_base':10,'decay.half_life_growth':1.3,'economy.negative_weight':1.5,'economy.repair_threshold':2,timezone:'UTC',master_ids:'8001',group_whitelist:['7001'],'diagnostics.enabled':false,'assessment.small_provider_id':'chosen','inject.enabled':false};
 ui.detailSettings.value={revision:'before',values:{...values},fields:[
  {path:'decay.half_life_base',default:30},{path:'decay.half_life_growth',default:1},
  {path:'economy.negative_weight',default:1},{path:'economy.repair_threshold',default:0},
  {path:'timezone',default:'Asia/Shanghai'},{path:'master_ids',default:''},
  {path:'group_whitelist',default:[]},{path:'diagnostics.enabled',default:true},
  {path:'assessment.small_provider_id',default:''},{path:'inject.enabled',default:true}
 ]};
 ui.detailValues.value={...values};
 ui.detailLoading.value=true;ui.fillRecommendedRules();assert.equal(ui.detailValues.value['decay.half_life_base'],10);
 ui.detailLoading.value=false;ui.fillRecommendedRules();
 const expected={'decay.half_life_base':30,'decay.half_life_growth':1,'economy.negative_weight':1,'economy.repair_threshold':0};
 assert.equal(JSON.stringify(ui.detailValues.value),JSON.stringify({...values,...expected}));
 assert.equal(ui.detailSettings.value.values['decay.half_life_base'],10);
 assert.equal(pending.length,0);
 const saving=ui.saveSettings();
 assert.equal(JSON.stringify(pending[0].params.values),JSON.stringify(expected));
 assert.equal(pending[0].params.revision,'before');
 pending[0].resolve({success:true,revision:'after',values:{...values,...expected},fields:[]});await saving;
 assert.match(ui.detailNotice.value,/重载/);
});
