const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
function harness(){
 let ui;const pending=[];
 const bridge={apiGet:async()=>({success:true,groups:[],users:[]}),apiPost:(endpoint)=>new Promise(resolve=>pending.push({endpoint,resolve}))};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../pages/dashboard/app.js'),'utf8'),{
  Vue:{ref:value=>({value}),computed:fn=>({get value(){return fn()}}),onMounted:()=>{},createApp:options=>({mount(){ui=options.setup()}})},window:{AstrBotPluginPage:bridge},console
 });
 ui.bots.value=[{bot_id:'10001',scope_mode:'per_group'},{bot_id:'20002',scope_mode:'per_group'}];ui.selectedBot.value='10001';
 return {ui,pending};
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
