const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function harness(){
 let timer,html='',requests=0;const listeners={};
 const ui={find(){return this},html(value){html=value;return this},toggleClass(){return this},text(){return this},remove(){return this},prepend(){return this}};
 const context={mobile_app:{},document:{hidden:false,removeEventListener(){}},window:{removeEventListener(){}},AbortSignal,
  setTimeout(fn,delay){timer={fn,delay};return 1},clearTimeout(){timer=null},
  fetch:async()=>{requests++;return {ok:true,json:async()=>({message:{sequence:1,date:'2026-10-19',calls:[]}})}},
  frappe:{provide(){},utils:{escape_html:String},realtime:{socket:{connected:false,off(){}},off(){}}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../public/js/opd_queue.js'),'utf8'),context);
 const portal=Object.create(context.mobile_app.OPDPortal.prototype);
 Object.assign(portal,{visible:true,display:true,clinic:'branch',section:'display',serial:0,$root:ui,chime(){},connection(){}});
 return {portal,context,get timer(){return timer},get html(){return html},get requests(){return requests}};
}
test('single call fills display; multiple calls return to the multi-card layout',()=>{
 const h=harness(),call={call_id:1,stage:'Vitals',token:'SR-001',patient_name:'Test',room:'2',visit_date:'2026-10-19'};
 h.portal.data={sequence:1,date:'2026-10-19',calls:[call]};h.portal.renderTV();
 assert.match(h.html,/opd-tv-grid opd-tv-single/);
 h.portal.data.calls.push({...call,call_id:2});h.portal.renderTV();assert.doesNotMatch(h.html,/opd-tv-single/);
});
test('display reads once without scheduling periodic refreshes',async()=>{
 const h=harness();await h.portal.refresh();assert.equal(h.requests,1);assert.equal(h.timer,undefined);
 h.portal.hide();assert.equal(h.timer,null);
});
test('failed display refresh does not poll or retry automatically',async()=>{
 const h=harness();h.context.fetch=async()=>{throw Error('offline')};
 await h.portal.refresh();assert.equal(h.portal.lastRefresh,null);assert.equal(h.timer,undefined);
});
