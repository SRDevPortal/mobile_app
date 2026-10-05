const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../mobileapp/page/doctor_clinical/doctor_clinical.js'), 'utf8');
const methods = source.slice(source.indexOf('        renderDoctors() {'), source.indexOf('        renderQueue() {'));
const matching = source.slice(source.indexOf('        matches(r,'), source.indexOf('        matchesSearch(r)'));
function render(selected) {
 let html;
 const calendar = vm.runInNewContext('({' + methods + ',' + matching + '})', {esc: String});
 Object.assign(calendar, {doctor: selected, queue:'All', channel:'All', range:null,
   doctors:[{id:'HP1',name:'Dr One'},{id:'HP2',name:'Dr Two'}],
   rows:[{doctor_id:'HP1',doctor_name:'Dr One',status:'Pending'}, {doctor_id:'HP1',doctor_name:'Dr One',status:'Approved'}, {doctor_id:'HP2',doctor_name:'Dr Two',status:'Checked In'}],
   matchesSearch:()=>true, color:()=>'', $root:{find:()=>({html:value=>{html=value;}})}});
 calendar.renderDoctors();
 return html;
}
test('initial load shows totals for every doctor',()=>{
 const html=render('');
 assert.match(html, /data-doctor="HP1"[^]*?ac-doctor-total">2</);
 assert.match(html, /data-doctor="HP2"[^]*?ac-doctor-total">1</);
 assert.match(html, /data-doctor=""[^]*?ac-doctor-total">3</);
});
test('selecting a doctor leaves all sidebar totals unchanged',()=>{
 const totals=html=>[...html.matchAll(/ac-doctor-total">(\d+)</g)].map(m=>m[1]);
 assert.deepEqual(totals(render('HP1')), totals(render('')));
 assert.deepEqual(totals(render('HP2')), totals(render('')));
});
