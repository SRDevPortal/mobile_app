const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../public/js/patient_encounter_autosave.js','utf8');
function fixture(type='Appointment'){
 let pending=null,delay=0,auto=0,manual=0,marker;
 const events={};
 const frm={doc:{name:'PE-1',doctype:'Patient Encounter',docstatus:0,sr_encounter_type:type,__unsaved:1},
 is_new:()=>false,is_dirty:()=>!!frm.doc.__unsaved,
 dirty(){frm.doc.__unsaved=1;events['dirty.appointment_autosave']();},
 save:async()=>{manual++;frm.doc.__unsaved=0;},
 validate_and_save(action,callback,btn,onError,resolve,reject){assert.equal(action,'Save');auto++;marker=frm.doc.__opd_autosave;frm.doc.__unsaved=0;resolve();},
 $wrapper:{on(event,...args){events[event]=args.at(-1);}}};
 const frappe={ui:{form:{on(){},close_grid_form(){}}},request:{ajax_count:0},router:{on(){}},
 get_route:()=>['Form','Patient Encounter',frm.doc.name],show_alert(){},
 model:{get_all_docs:doc=>[doc]},meta:{docfield_list:{}}};
 const context={frappe,window:{cur_frm:frm},__:x=>x,setTimeout(fn,ms){pending=fn;delay=ms;return 1;},clearTimeout(){pending=null;}};
 vm.createContext(context);vm.runInContext(source,context);context.setup_appointment_autosave(frm);
 return {frm,frappe,dirty:()=>events['dirty.appointment_autosave'](),tick:async()=>{const f=pending;pending=null;if(f)await f();},
 get auto(){return auto},get manual(){return manual},get marker(){return marker},get delay(){return delay}};
}
(async()=>{
 let f=fixture();f.dirty();assert.equal(f.delay,1000);await f.tick();assert.equal(f.auto,1);assert.equal(f.marker,1);assert.equal(f.frm.doc.__opd_autosave,undefined);
 await f.frm.save('Save');assert.equal(f.manual,1);await f.tick();assert.equal(f.auto,1);
 for(const type of ['Order','Followup']){f=fixture(type);f.dirty();await f.tick();assert.equal(f.auto,0);}
 f=fixture();f.frm.doc.docstatus=1;f.dirty();await f.tick();assert.equal(f.auto,0);
 f=fixture();f.frm.is_new=()=>true;f.dirty();await f.tick();assert.equal(f.auto,0);
 f=fixture();f.frappe.request.ajax_count=1;f.dirty();await f.tick();assert.equal(f.auto,0);
 f.frappe.request.ajax_count=0;await f.tick();assert.equal(f.auto,1);
 console.log('PASS: one-second debounce, autosave marker, manual save, appointment-only, draft/new guards, pending requests');
})().catch(e=>{console.error(e);process.exitCode=1;});
