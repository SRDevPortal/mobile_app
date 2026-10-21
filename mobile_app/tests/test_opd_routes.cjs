const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function harness(routes){
 const calls=[],messages=[];let dialog;
 const context={mobile_app:{},crypto:{randomUUID:()=> 'test-request'},frappe:{
  provide(){},utils:{escape_html:String},user_roles:['OPD Staff'],msgprint:m=>messages.push(m),
  ui:{Dialog:class {
   constructor(config){Object.assign(this,config);dialog=this;this.fields_dict=Object.fromEntries(config.fields.filter(f=>f.fieldname).map(f=>[f.fieldname,{df:f,$wrapper:{html(){}}}]));this.values=Object.fromEntries(config.fields.filter(f=>f.fieldname).map(f=>[f.fieldname,f.default]));}
   show(){} disable_primary_action(){} enable_primary_action(){} get_value(k){return this.values[k];} async set_value(k,v){this.values[k]=v;}
  }},
  async xcall(method,args){calls.push({method,args});
   if(method.endsWith('.bootstrap'))return {clinics:[{name:'branch'}]};
   if(method.endsWith('.get_setup'))return {routes,rooms:[],practitioners:[{name:'doctor',practitioner_name:'Doctor'}],genders:[],clinic_name:'Branch'};
   if(method.endsWith('.checkin_context'))return {patient:'patient',doctor:'doctor',department:'Unconfigured department'};
   if(method.endsWith('.check_in'))return new Promise(()=>{});
   throw Error(method);
  }
 }};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../public/js/opd_queue.js'),'utf8'),context);
 return {context,calls,messages,get dialog(){return dialog;}};
}
test('check-in auto-selects a sole room route without using the patient department',async()=>{
 const h=harness([{name:'route-id',route_name:'General OPD',steps:[]}]);
 await h.context.mobile_app.opd_checkin({clinic:'branch',source_doctype:'Patient Encounter',name:'booking',status:'Approved'});
 assert.equal(h.dialog.fields_dict.department,undefined);
 assert.equal(h.dialog.get_value('route_id'),'route-id');
 h.dialog.primary_action(h.dialog.values);
 await new Promise(resolve=>setImmediate(resolve));
 const call=h.calls.find(c=>c.method.endsWith('.check_in'));
 assert.equal(call.args.route_id,'route-id');
 assert.equal(call.args.department,undefined);
});
test('multiple room routes require a deliberate selection before token creation',async()=>{
 const h=harness([{name:'route-a',route_name:'A'},{name:'route-b',route_name:'B'}]);
 await h.context.mobile_app.opd_checkin({clinic:'branch',source_doctype:'Patient Encounter',name:'booking',status:'Approved'});
 await h.dialog.primary_action(h.dialog.values);
 assert.equal(h.calls.some(c=>c.method.endsWith('.check_in')),false);
 assert.match(h.messages[0],/Choose a room route/);
});