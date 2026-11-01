const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
function harness(){
 let ui;const pending=[];const pendingGets=[];const reads=[];
 const bridge={
  apiGet:async(endpoint,params)=>{
   reads.push({endpoint,params});
   if(endpoint==='prompts'||endpoint==='member')return new Promise(resolve=>pendingGets.push({endpoint,params,resolve}));
   return {success:true,groups:[],users:[],logs:[{id:params.bot_id}]};
  },
  apiPost:(endpoint,params)=>new Promise(resolve=>pending.push({endpoint,params,resolve}))
 };
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../pages/dashboard/app.js'),'utf8'),{
  Vue:{ref:value=>({value}),computed:fn=>({get value(){return fn()}}),onMounted:()=>{},createApp:options=>({mount(){ui=options.setup()}})},window:{AstrBotPluginPage:bridge},console
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
