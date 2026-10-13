const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function harness(){
 const calls=[],document={hidden:false},ui={sort(){},find(){return this;},remove(){},prepend(){}};
 const context={mobile_app:{},document,frappe:{provide(){},utils:{escape_html:String},datetime:{get_today:()=> '2026-10-13'},realtime:{socket:{connected:true}},xcall(method,args){return new Promise((resolve,reject)=>calls.push({method,args,resolve,reject}));}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../public/js/opd_queue.js'),'utf8'),context);
 const portal=Object.create(context.mobile_app.OPDPortal.prototype);
 Object.assign(portal,{visible:true,clinic:'branch-a',section:'queue',serial:0,$root:ui,connection(){},renderQueue(){this.renders=(this.renders||0)+1;}});
 const result={rooms:[],visits:[],daily:{date:'2026-10-13',visits:[]}};
 return {portal,calls,document,result,tick:()=>new Promise(resolve=>setImmediate(resolve))};
}
test('OPD event burst coalesces into one in-flight read and one follow-up',async()=>{
 const h=harness(),pending=h.portal.refresh();
 for(let i=0;i<20;i++)h.portal.refresh();
 assert.equal(h.calls.length,1);
 assert.match(h.calls[0].method,/queue_snapshot$/);
 h.calls[0].resolve(h.result);await h.tick();assert.equal(h.calls.length,2);
 h.calls[1].resolve(h.result);await pending;assert.equal(h.calls.length,2);
});
test('hidden OPD views defer work and failed reads do not loop',async()=>{
 const h=harness();h.document.hidden=true;await h.portal.refresh();assert.equal(h.calls.length,0);
 h.document.hidden=false;const pending=h.portal.refresh();h.calls[0].reject(new Error('offline'));await pending;
 assert.equal(h.calls.length,1);assert.equal(h.portal.refreshing,false);
});
test('changing branch during refresh discards stale data and loads new branch',async()=>{
 const h=harness(),pending=h.portal.refresh();h.portal.clinic='branch-b';h.portal.refresh();
 h.calls[0].resolve(h.result);await h.tick();assert.equal(h.portal.renders||0,0);
 assert.equal(h.calls[1].args.clinic,'branch-b');h.calls[1].resolve(h.result);await pending;
 assert.equal(h.portal.renders,1);
});
test('hidden during pending refresh does not start a follow-up until visible',async()=>{
 const h=harness(),pending=h.portal.refresh();h.portal.refresh();h.document.hidden=true;
 h.calls[0].resolve(h.result);await pending;assert.equal(h.calls.length,1);
 h.document.hidden=false;const next=h.portal.refresh();h.calls[1].resolve(h.result);await next;
 assert.equal(h.calls.length,2);
});
