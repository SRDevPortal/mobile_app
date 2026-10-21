const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const source = fs.readFileSync(path.join(__dirname, '../mobileapp/page/doctor_clinical/doctor_clinical.js'), 'utf8');
const method = source.slice(source.indexOf('        reconcileQueue('), source.indexOf('        async open('));

test('background updates preserve cards, scroll anchors and deliberate navigation', async () => {
 const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_PATH || undefined});
 try {
  const page=await browser.newPage();
  await page.setContent('<style>.ac-queue-list{height:350px;overflow:auto;position:relative}.ac-booking{display:block;height:90px;width:300px}.ac-counts{height:30px}</style><main><div class="ac-queue-view"></div></main>');
  await page.addScriptTag({path:path.resolve(__dirname,'../../../frappe/node_modules/jquery/dist/jquery.min.js')});
  await page.evaluate(method=>{
   window.calendar=eval('({' + method + '})');
   Object.assign(calendar, {$root:$('main'), range:null,doctor:'',queue:'All',search:'',branchFilter:'',channel:'All'});
   window.payload=(ids,changed='')=>$('<div><div class="ac-counts">'+ids.length+'</div><div class="ac-queue-list">'+ids.map(id=>'<button class="ac-booking" data-booking="'+id+'">'+id+(changed===id?' updated':'')+'</button>').join('')+'</div></div>');
   window.ids=Array.from({length:30},(_,i)=>String(i));
   calendar.renderQueue=()=>calendar.reconcileQueue(payload(ids),Boolean(calendar.acceptQueueUpdates));
   calendar.renderQueue();
   window.list=$('.ac-queue-list')[0];list.scrollTop=900;window.card=$('[data-booking="10"]')[0];
  },method);
  await page.waitForTimeout(800);
  const unchanged=await page.evaluate(()=>{
   calendar.reconcileQueue(payload(ids));
   return {sameList:list===$('.ac-queue-list')[0],sameCard:card===$('[data-booking="10"]')[0],top:list.scrollTop};
  });
  assert.deepEqual(unchanged,{sameList:true,sameCard:true,top:900});
  await page.evaluate(()=>{calendar.queueScrolledAt=Date.now();calendar.reconcileQueue(payload(ids,'10'));});
  assert.equal(await page.locator('[data-booking="10"]').textContent(),'10');
  const inserted=await page.evaluate(()=>{
   clearTimeout(calendar.queueRenderTimer);calendar.queueScrolledAt=0;ids=['new',...ids];
   calendar.reconcileQueue(payload(ids));
   return {notice:$('.ac-apply-updates').length,inserted:$('[data-booking="new"]').length,top:list.scrollTop};
  });
  assert.deepEqual(inserted,{notice:1,inserted:0,top:900});
  await page.locator('.ac-apply-updates').click();
  assert.equal(await page.locator('[data-booking="new"]').count(),1);
  assert.equal(await page.evaluate(()=>list.scrollTop),990);
  const removed=await page.evaluate(()=>{
   calendar.queueScrolledAt=0;ids=ids.filter(id=>id!=='2');calendar.reconcileQueue(payload(ids,'10'));
   return {top:list.scrollTop,sameCard:card===$('[data-booking="10"]')[0],text:card.textContent};
  });
  assert.deepEqual(removed,{top:900,sameCard:true,text:'10 updated'});
  const navigation=await page.evaluate(()=>{calendar.doctor='another';calendar.reconcileQueue(payload(['other']));return $('.ac-queue-list')[0].scrollTop;});
  assert.equal(navigation,0);
 } finally {await browser.close();}
});

test('online doctor grid includes zero counts and opens a doctor profile with online bookings', async () => {
 const browser=await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_PATH || undefined});
 try {
  const page=await browser.newPage();
  await page.setContent('<main><input class="ac-calendar-date"><aside class="ac-doctors"><div class="ac-doctor-list"></div></aside><section class="ac-panel"><div class="ac-queue-view"></div></section></main>');
  await page.addScriptTag({path:path.resolve(__dirname,'../../../frappe/node_modules/jquery/dist/jquery.min.js')});
  await page.addScriptTag({path:path.resolve(__dirname,'../../../frappe/node_modules/moment/min/moment.min.js')});
  await page.evaluate(source=>{
   const queue=source.slice(source.indexOf('        renderDoctors() {'),source.indexOf('        async open('));
   const matches=source.slice(source.indexOf('        matches(r,'),source.indexOf('        filtered()'));
   const status=source.slice(source.indexOf('    const statusClass'),source.indexOf('    const activeRoute'));
   const esc=value=>$('<div>').text(String(value)).html(),icon=()=>'';
   const Harness=eval('(()=>{'+status+'; return class {'+queue+matches+'};})()');
   window.online=new Harness();
   Object.assign(online,{$root:$('main'),section:'online',date:moment('2026-10-17'),range:{start:moment('2026-10-17'),end:moment('2026-10-18')},
    channel:'Online',queue:'All',doctor:'',search:'',branchFilter:'',rememberView(){},color:()=> '#123456',
    doctors:[{id:'a',name:'Dr Alpha'},{id:'b',name:'Dr Beta'},{id:'c',name:'Dr Gamma',department:'Cardiology'}],rows:[
     {id:'2',doctor_id:'b',doctor_name:'Dr Beta',date:'2026-10-17',time:'09:00:00',patient_name:'Online B',status:'Pending',online:true},
     {id:'1',doctor_id:'a',doctor_name:'Dr Alpha',date:'2026-10-17',time:'11:00:00',patient_name:'Online A',status:'Approved',online:true},
     {id:'3',doctor_id:'a',doctor_name:'Dr Alpha',date:'2026-10-17',time:'11:00:00',patient_name:'In clinic',status:'Pending',online:false},
    ]});
   online.renderDoctors();online.renderQueue();
  },source);
  assert.equal(await page.locator('.ac-online-card').count(),3);
  assert.match(await page.locator('.ac-online-card[data-doctor="c"]').textContent(),/Cardiology.*0 online appointments/s);
  assert.equal(await page.locator('[data-booking]').count(),0);
  await page.evaluate(()=>{online.doctor='b';online.queueScrolledAt=0;online.renderQueue();});
  assert.deepEqual(await page.locator('[data-booking]').evaluateAll(nodes=>nodes.map(n=>n.dataset.booking)),['2']);
  assert.match(await page.locator('.ac-panel > .ac-online-profile .ac-online-profile-info').textContent(),/Dr Beta.*Appointments: 1/s);
  assert.equal(await page.locator('.ac-doctors .ac-online-profile').count(),0);
  await page.evaluate(()=>{online.doctor='a';online.search='online a';online.renderQueue();});
  assert.deepEqual(await page.locator('[data-booking]').evaluateAll(nodes=>nodes.map(n=>n.dataset.booking)),['1']);
 } finally {await browser.close();}
});

test('online appointment opens a styled right drawer with permitted actions and returns to the list', async () => {
 const browser=await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_PATH || undefined});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  await page.setContent('<main class="ac-online-mode"><section class="ac-panel"><div class="ac-queue-view"><button id="booking">Open booking</button></div><div class="ac-detail-view" hidden></div></section></main>');
  await page.addStyleTag({path:path.join(__dirname,'../public/css/doctor_clinical.css')});
  await page.addStyleTag({content:'[hidden]{display:none!important}'});
  await page.addScriptTag({path:path.resolve(__dirname,'../../../frappe/node_modules/jquery/dist/jquery.min.js')});
  await page.addScriptTag({path:path.resolve(__dirname,'../../../frappe/node_modules/moment/min/moment.min.js')});
  await page.evaluate(source=>{
   const methods=source.slice(source.indexOf('        async open('),source.indexOf('        action(action,'));
   const esc=v=>$('<div>').text(String(v)).html(),icon=()=>'',statusClass=v=>v.toLowerCase().replace(/ /g,'-'),statusMark=()=>'',labels={approve:'Approve'},API='calendar';
   window.record={id:'test',name:'TEST-BOOKING',patient_name:'Sample Patient',date:'2026-10-16',time:'16:00:00',source_doctype:'Patient Encounter',status:'Pending',doctor_id:'a',doctor_name:'Dr Alpha',phone:'123456',online:true,actions:['approve'],can_change_doctor:true,history:[]};
   window.frappe={xcall:async()=>record,show_alert:()=>{throw Error('Detail failed');}};
   window.drawer=new (eval('(class {' + methods + '})'))();
   Object.assign(drawer,{$root:$('main'),section:'online',detailRequest:0,doctors:[{id:'a',name:'Dr Alpha',department:'Medicine'}],rememberView(){},renderQueue(){},button:(action,label)=>`<button data-action="${action}">${label}</button>`});
   $('#booking').trigger('focus');
   return drawer.open(record);
  },source);
  assert.equal(await page.locator('.ac-detail-view').getAttribute('role'),'dialog');
  assert.equal(await page.locator('.ac-queue-view').isVisible(),true);
  assert.equal(await page.locator('.ac-drawer-backdrop').isVisible(),true);
  assert.match(await page.locator('.ac-drawer-doctor').textContent(),/Dr Alpha.*Medicine/s);
  assert.equal(await page.locator('[data-action="change-doctor"]').count(),1);
  assert.equal(await page.locator('[data-action="complete"]').count(),0);
  const rect=await page.locator('.ac-detail-view').boundingBox();
  assert.equal(Math.round(rect.x+rect.width),1440);
  assert.equal(Math.round(rect.width),470);
  await page.screenshot({path:path.join(__dirname,'../../../../online-drawer-preview.png')});
  await page.evaluate(()=>drawer.close());
  assert.equal(await page.locator('.ac-detail-view').isVisible(),false);
  assert.equal(await page.locator('.ac-drawer-backdrop').count(),0);
  assert.equal(await page.evaluate(()=>document.activeElement.id),'booking');
 } finally {await browser.close();}
});