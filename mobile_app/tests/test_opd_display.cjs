const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function harness(){
 let timer,html='',requests=0;const listeners={};
 const ui={find(selector){this.selector=selector;return this},html(value){if(this.selector==='.opd-content')html=value;else if(this.selector==='.opd-upcoming-list')this.list=value;return this},toggleClass(){return this},text(){return this},remove(){return this},prepend(){return this}};
 const context={mobile_app:{},document:{hidden:false,removeEventListener(){}},window:{removeEventListener(){}},AbortSignal,clearInterval(){},
  setTimeout(fn,delay){timer={fn,delay};return 1},clearTimeout(){timer=null},
  fetch:async()=>{requests++;return {ok:true,json:async()=>({message:{sequence:1,date:'2026-10-19',calls:[]}})}},
  frappe:{provide(){},utils:{escape_html:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')},realtime:{socket:{connected:false,off(){}},off(){}}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../public/js/opd_queue.js'),'utf8'),context);
 const portal=Object.create(context.mobile_app.OPDPortal.prototype);
 Object.assign(portal,{visible:true,display:true,clinic:'branch',section:'display',serial:0,$root:ui,chime(){},connection(){}});
 return {portal,context,get timer(){return timer},get html(){return html},get list(){return ui.list},get requests(){return requests}};
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

test('display uses live queue totals and escapes next-up patient details',()=>{
 const h=harness();h.portal.data={sequence:1,date:'2026-10-21',calls:[],waiting_count:7,served_today:3,
  upcoming:[{token:'SR-007',patient_name:'<script>bad</script>',room:'2'}]};
 h.portal.renderTV();assert.match(h.html,/Waiting patients/);assert.doesNotMatch(h.list,/<script>bad/);assert.match(h.list,/&lt;script&gt;bad/);
});
test('checked-in list includes every patient without pagination or extra requests',()=>{
 const h=harness();h.portal.data={checked_in:Array.from({length:12},(_,i)=>({token:'SR-'+i,patient_name:'Patient '+i,room:1,status:'Waiting'}))};
 h.portal.renderCheckinList();
 for(let i=0;i<12;i++) assert.ok(h.list.includes('Patient '+i+'<'));
 assert.equal((h.list.match(/class="opd-upcoming-item"/g)||[]).length,12);
 assert.equal(h.requests,0);
});
test('called patients leave the right list and return when waiting for the next stage',()=>{
 const h=harness();h.portal.data={checked_in:[{token:'SR-001',patient_name:'Called patient',status:'Called',room:1},{token:'SR-002',patient_name:'Waiting patient',status:'Waiting',room:2}]};
 h.portal.renderCheckinList();assert.doesNotMatch(h.list,/Called patient/);assert.match(h.list,/Waiting patient/);
 h.portal.data.checked_in[0].status='Waiting';h.portal.renderCheckinList();assert.match(h.list,/Called patient/);
});
test('end-day states leave the waiting list empty',()=>{
 const h=harness();h.portal.data={checked_in:['Completed','Withdrawn','Called','In Progress','On Hold'].map((status,i)=>({token:'SR-'+i,patient_name:'Patient '+i,status,room:1}))};
 h.portal.renderCheckinList();assert.match(h.list,/No patients waiting/);assert.doesNotMatch(h.list,/opd-upcoming-item/);
 h.portal.data.checked_in.push({token:'SR-009',patient_name:'Still waiting',status:'Waiting',room:2});
 h.portal.renderCheckinList();assert.match(h.list,/Still waiting/);assert.equal((h.list.match(/class="opd-upcoming-item"/g)||[]).length,1);
});