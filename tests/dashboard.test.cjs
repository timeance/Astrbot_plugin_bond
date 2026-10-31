const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
function harness(){
 let ui;const pending=[];const reads=[];
 const bridge={apiGet:async(endpoint,params)=>{reads.push({endpoint,params});return {success:true,groups:[],users:[],logs:[{id:params.bot_id}]}},apiPost:(endpoint)=>new Promise(resolve=>pending.push({endpoint,resolve}))};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../pages/dashboard/app.js'),'utf8'),{
  Vue:{ref:value=>({value}),computed:fn=>({get value(){return fn()}}),onMounted:()=>{},createApp:options=>({mount(){ui=options.setup()}})},window:{AstrBotPluginPage:bridge},console
 });
 ui.bots.value=[{bot_id:'10001',scope_mode:'per_group'},{bot_id:'20002',scope_mode:'per_group'}];ui.selectedBot.value='10001';
 return {ui,pending,reads};
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

for(const operation of ['switch','restore']) test(`log tab reloads after ${operation}`,async()=>{
 const {ui,pending,reads}=harness();ui.tab.value='logs';
 if(operation==='switch'){ui.selectedBot.value='20002';await ui.selectBot();}
 else {ui.restoreName.value='saved.db';ui.prepareRestore();const done=ui.confirmRestore();pending[0].resolve({success:true});await done;}
 const request=reads.find(item=>item.endpoint==='logs');
 assert.ok(request);assert.equal(request.params.bot_id,ui.selectedBot.value);
 assert.equal(ui.logs.value[0].id,ui.selectedBot.value);assert.equal(ui.tab.value,'logs');
});
