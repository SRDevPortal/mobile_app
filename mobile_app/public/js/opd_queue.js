frappe.provide('mobile_app');
(() => {
    const API = 'mobile_app.api.opd_queue';
    const esc = value => frappe.utils.escape_html(String(value ?? ''));
    const stages = ['Vitals', 'Medical History', 'Doctor Consultation'];
    const roomFields = ['vitals_room', 'history_room', 'consultation_room'];
    const requestId = () => crypto.randomUUID();
    const read = (method, args = {}) => frappe.xcall(`${API}.${method}`, args);
    const confirm = message => new Promise(resolve => frappe.confirm(message, () => resolve(true), () => resolve(false)));
    const opts = rows => rows.map(r => typeof r === 'string' ? {value:r,label:r} : r);
    const btn = (action, label, extra = '', primary = false) => `<button class="opd-btn ${primary ? 'primary' : ''}" data-opd="${action}" ${extra}>${esc(label)}</button>`;
    const roomLabel = (rooms, id) => {const room = rooms.find(r => (r.name || r.client_id) === id); return room ? `Room ${room.room_number}` : id;};
    const staffLabel = room => (room?.assignments || []).map(a => a.practitioner || a.user).join(', ');
    const reasonField = {fieldname:'reason',label:'Reason',fieldtype:'Small Text',reqd:1};
    const waitLabel = time => time ? `${Math.max(0,moment().diff(moment(time),'minutes'))} min` : '?';

    async function write(method, args) {
        const payload = {...args, request_id:args.request_id || requestId()};
        try {return await read(method, payload);} catch (error) {
            // A dropped connection can occur after commit. Retry the exact request.
            if (!error?.status || error.status === 0 || error.status >= 500) {
                if (await confirm('The result could not be confirmed. Retry this same request safely?')) return write(method,payload);
            }
            throw error;
        }
    }

    function receipt(visit, rooms) {
        const content = `<div class="opd-slip"><h3>OPD Visit Token</h3><div class="token">${esc(visit.token)}</div>
            <h4>${esc(visit.patient_name)}</h4><p>${esc(visit.visit_date)} &middot; ${esc(visit.department)}</p>
            <p>Doctor: ${esc(visit.consultation_doctor_name || visit.consultation_doctor)}</p><div class="opd-route">${visit.stages.map(s => `<span>${esc(s.stage)}<br><strong>${esc(roomLabel(rooms,s.room))}</strong></span>`).join(' &rarr; ')}</div></div>`;
        const d = new frappe.ui.Dialog({title:'Token issued',fields:[{fieldname:'slip',fieldtype:'HTML'}],primary_action_label:'Print token',primary_action:()=>{
            const win=window.open('','_blank','width=600,height=700');
            if (!win) {frappe.msgprint('Allow pop-ups to print the token.');return;}
            win.document.write(`<html><head><title>OPD Token ${esc(visit.token)}</title><style>body{font-family:sans-serif;text-align:center;padding:24px}.token{font-size:72px;font-weight:bold}.opd-route span{display:block;margin:18px}</style></head><body>${content}</body></html>`);
            win.document.close();win.focus();win.print();
        }});
        d.fields_dict.slip.$wrapper.html(content);d.show();
    }

    async function checkin(clinic, source, done) {
        const setup=await read('get_setup',{clinic});
        if (!setup.routes.length) {frappe.msgprint('Configure department rooms in OPD Setup before issuing tokens.');return;}
        const context=source ? await read('checkin_context',{clinic,doctype:source.source_doctype,name:source.name}) : {};
        if(context.visit){receipt(await read('get_visit',{clinic,visit:context.visit}),setup.rooms);return;}
        const fields=[
            {fieldname:'department',label:'Confirm department',fieldtype:'Select',options:['',...setup.routes.map(r=>r.department)],default:context.department,reqd:1},
            {fieldname:'doctor',label:'Consultation doctor',fieldtype:'Select',options:[{value:'',label:''},...opts(setup.practitioners.map(p=>({value:p.name,label:p.practitioner_name})))],default:context.doctor,reqd:1},
            {fieldname:'route',fieldtype:'HTML'},
            {fieldname:'patient',label:'Existing patient',fieldtype:'Link',options:'Patient',default:context.patient,
                read_only:!!context.patient,get_query:()=>({query:`${API}.patient_query`,filters:{clinic}})},
            {fieldname:'new_patient',label:'Register a new patient',fieldtype:'Check',hidden:!!context.patient},
            {fieldname:'first_name',label:'Patient name',fieldtype:'Data',default:context.patient_name,depends_on:'eval:doc.new_patient',mandatory_depends_on:'eval:doc.new_patient'},
            {fieldname:'sex',label:'Gender',fieldtype:'Select',options:['',...setup.genders],depends_on:'eval:doc.new_patient',mandatory_depends_on:'eval:doc.new_patient'},
            {fieldname:'mobile',label:'Mobile number',fieldtype:'Data',depends_on:'eval:doc.new_patient',mandatory_depends_on:'eval:doc.new_patient'},
            {...reasonField,reqd:0,label:'Reason for changing the booked doctor'}
        ];
        let busy=false;
        const d=new frappe.ui.Dialog({title:source?'Check in & issue OPD token':'Walk-in check-in',fields,primary_action_label:'Issue token',primary_action:async values=>{
            if(busy)return;
            if(!values.patient && !values.new_patient){frappe.msgprint('Select an existing patient or register a new patient.');return;}
            if(source && context.doctor!==values.doctor && !values.reason?.trim()){frappe.msgprint('Enter a reason for changing the booked doctor.');return;}
            busy=true;d.disable_primary_action();
            try{
                const visit=await write('check_in',{clinic,department:values.department,doctor:values.doctor,
                    patient:values.new_patient?null:values.patient,patient_details:values.new_patient?{first_name:values.first_name,sex:values.sex,mobile:values.mobile}:null,
                    source_doctype:source?.source_doctype,source_name:source?.name,expected_status:source?.status || 'Approved',reason:values.reason});
                d.hide();receipt(visit,setup.rooms);done?.();
            }finally{busy=false;d.enable_primary_action();}
        }});
        const showRoute=()=>{
            const route=setup.routes.find(r=>r.department===d.get_value('department'));
            const room=route && setup.rooms.find(r=>r.name===route.consultation_room);
            const assigned=(room?.assignments||[]).map(a=>a.practitioner).filter(Boolean);
            const mismatch=route && d.get_value('doctor') && !assigned.includes(d.get_value('doctor'));
            d.fields_dict.route.$wrapper.html(route?`<div class="opd-notice">${roomFields.map((f,i)=>`${esc(stages[i])}: <b>${esc(roomLabel(setup.rooms,route[f]))}</b>`).join(' &rarr; ')}<br>Consultation doctors: ${assigned.map(id=>esc(setup.practitioners.find(p=>p.name===id)?.practitioner_name||id)).join(', ')}${mismatch?'<p class="opd-error">The selected doctor is not assigned to this consultation room. Choose an assigned doctor or update OPD Setup.</p>':''}</div>`:'');
        };
        d.fields_dict.department.df.onchange=showRoute;d.fields_dict.doctor.df.onchange=showRoute;d.show();showRoute();
    }
    mobile_app.opd_checkin = async (source, done) => {
        const boot=await read('bootstrap');
        if(!boot.reception){frappe.msgprint('Reception or an appointment manager must issue OPD tokens.');return;}
        if(!boot.clinics.length){frappe.msgprint('No OPD clinic is available.');return;}
        const clinic=boot.clinics[0].name;
        await checkin(clinic,source,done);
    };

    mobile_app.OPDPortal=class {
        constructor(wrapper,display=false,host=null){
            this.host=host;
            this.wrapper=wrapper;this.display=display;this.visible=false;this.section='queue';this.serial=0;
            this.page=host ? host.page : frappe.ui.make_app_page({parent:wrapper,title:display?'Waiting Room':'OPD',single_column:true});
            this.$root=$(`<div class="opd-root ${display?'opd-tv':''}"></div>`).appendTo(host ? $(wrapper) : this.page.main);
            this.changed=event=>{if(event.clinic===this.clinic && this.visible){if(this.busy)this.refreshPending=true;else if(this.section!=='setup')this.refresh();else this.$root.find('.opd-connection').text('Live - setup changes may require refresh');}};
            this.connected=()=>{this.baseline=null;this.connection(true);if(this.visible && this.section!=='setup')this.refresh();};
            this.disconnected=()=>this.connection(false);
            this.focus=()=>{if(!document.hidden && this.visible && this.section!=='setup'){this.baseline=null;this.refresh();}};
            this.$root.on('click','[data-opd]',event=>{event.preventDefault();this.action($(event.currentTarget).attr('data-opd'),$(event.currentTarget));});
        }
        async show(){
            if(!this.visible){frappe.realtime.on('opd_queue_changed',this.changed);frappe.realtime.socket?.on('connect',this.connected);frappe.realtime.socket?.on('disconnect',this.disconnected);document.addEventListener('visibilitychange',this.focus);}
            this.visible=true;this.baseline=null;
            try{
                this.boot=await read('bootstrap');
                this.clinic ||= this.boot.clinics[0]?.name;
                if(!this.clinic){this.$root.html('<div class="opd-empty">No clinic is assigned to your account. Contact an appointment manager.</div>');return;}
                if(this.boot.display_only && !this.display){frappe.set_route('opd-display');return;}
                this.section=this.display?'display':(this.host ? this.section : (frappe.get_route()[1] || 'queue'));
                if(this.section==='setup' && !this.boot.manager)this.section='queue';
                this.shell();await this.refresh();
            }catch(error){this.$root.html('<div class="opd-empty">Unable to load OPD. Check your connection and clinic access, then reload.</div>');}
        }
        hide(){this.visible=false;++this.serial;frappe.realtime.off('opd_queue_changed',this.changed);frappe.realtime.socket?.off('connect',this.connected);frappe.realtime.socket?.off('disconnect',this.disconnected);document.removeEventListener('visibilitychange',this.focus);}
        connection(live){this.$root.find('.opd-connection').toggleClass('live',live).text(live?'Connected':'Disconnected - displayed information may be outdated');}
        shell(){
            const title=this.display?'Please wait for your token to be called':'OPD Patient Flow';
            this.$root.html(`<header class="opd-head"><div><h1>${title}</h1><p>${esc(this.boot.clinics.find(c=>c.name===this.clinic)?.clinic_name)}</p></div><div class="opd-actions"><span class="opd-connection"></span>${this.display?btn('sound','Enable sound')+btn('fullscreen','Full screen'):(this.host?.opdOnly?'':btn('calendar','Appointment calendar'))}${btn('refresh','Refresh')}</div></header>
                ${this.display?'':`<nav class="opd-nav">${btn('queue','OPD Queue')}${btn('room','My Room')}${this.boot.manager?btn('setup','OPD Setup'):''}${btn('tv','Waiting-Room Display')}</nav>`}<div class="opd-content"><div class="opd-empty">Loading...</div></div>`);
            this.$root.find(`[data-opd="${this.section}"]`).addClass('active');this.connection(frappe.realtime.socket?.connected!==false);
        }
        async refresh(){
            if(!this.visible||this.busy)return;
            const serial=++this.serial;
            try{
                const data=await read(this.display?'display_snapshot':this.section==='setup'?'get_setup':'snapshot',{clinic:this.clinic});
                if(serial!==this.serial||!this.visible)return;
                if(data.rooms)data.rooms.sort((a,b)=>String(a.room_number).localeCompare(String(b.room_number),undefined,{numeric:true}));
                this.data=data;
                if(this.display)this.renderTV();else if(this.section==='setup'){this.setup=structuredClone(data);this.dirty=false;this.renderSetup();}else if(this.section==='room')this.renderRoom();else this.renderQueue();
            }catch(e){if(serial===this.serial)this.$root.find('.opd-content').prepend('<div class="opd-notice">Refresh failed. The information below may be outdated. Use Refresh to try again.</div>');}
        }
        async mutate(method,args){
            if(this.busy)return;
            this.busy=true;this.$root.find('button').prop('disabled',true);
            try{return await write(method,{clinic:this.clinic,...args});}
            finally{this.busy=false;this.$root.find('button').prop('disabled',false);this.refreshPending=false;if(this.section!=='setup')await this.refresh();}
        }
        renderQueue(){
            const d=this.data;
            const rows=d.visits.map(v=>{const s=v.stages[v.stage_index];return `<tr><td><strong>${esc(v.token)}</strong><small>${esc(v.visit_date)}</small></td><td>${esc(v.patient_name)}${v.priority?'<small class="opd-badge priority">Priority</small>':''}</td><td>${esc(v.department)}<small>${esc(v.consultation_doctor_name || v.consultation_doctor)}</small></td><td>${esc(s.stage)}<small>${esc(roomLabel(d.rooms,s.room))}</small></td><td><span class="opd-badge">${esc(s.state)}</span><small>${waitLabel(s.queued_at)} since queue entry</small></td><td>${btn('visit','Open',`data-id="${esc(v.name)}"`)}${btn('print','Print token',`data-id="${esc(v.name)}"`)}</td></tr>`;}).join('');
            this.$root.find('.opd-content').html(`<div class="opd-toolbar"><h2>Active visits ? ${d.visits.length}</h2>${d.reception?btn('walkin','Check in walk-in','',true):''}</div><div class="opd-panel opd-scroll">${rows?`<table class="opd-table"><thead><tr><th>Token</th><th>Patient</th><th>Department / Doctor</th><th>Stage / Room</th><th>Status</th><th>Actions</th></tr></thead><tbody>${rows}</tbody></table>`:'<div class="opd-empty">No patients are waiting. Check in an appointment from the calendar or register a walk-in.</div>'}</div>`);
        }
        renderRoom(){
            const d=this.data;
            const cards=d.rooms.filter(r=>r.enabled).map(room=>{
                const session=d.sessions.find(s=>s.room===room.name), mine=session?.controller===d.user;
                const current=d.visits.find(v=>v.name===session?.current_visit), stage=current?.stages[current.stage_index];
                const eligible=d.visits.filter(v=>{const s=v.stages[v.stage_index];return s.room===room.name && s.state==='Waiting' && (room.purpose!=='Doctor Consultation'||!session||v.consultation_doctor===session.practitioner);});
                let actions='';
                if(!session && room.assignments.some(a=>a.user===d.user))actions=btn('occupy','Control this room',`data-room="${esc(room.name)}"`,true);
                if(mine){
                    const attr=`data-session="${esc(session.name)}"`;
                    actions=(!current&&session.status==='Available'?btn('call_next','Call next patient',attr,true):'')+
                        (stage?.state==='Called'?btn('start','Patient arrived ? Start',attr,true)+btn('recall','Call again',attr)+btn('absent','Patient not present',attr):'')+
                        (stage?.state==='In Progress'?btn('complete','Complete stage',attr,true)+btn('release','Release patient',attr):'')+
                        btn(session.status==='Paused'?'resume':'pause',session.status==='Paused'?'Resume':'Pause',attr)+(!current?btn('end','End session',attr):'');
                }
                if(session&&d.manager)actions+=btn('handover','Transfer control',`data-session="${esc(session.name)}"`);
                return `<article class="opd-room-card"><h2>Room ${esc(room.room_number)}</h2><p>${esc(room.purpose)} &middot; ${esc(room.room_label)}</p><p>${session?`${esc(session.controller)} &middot; ${esc(session.status)}`:'No active controller'}${session?.practitioner?`<br>${esc(session.practitioner)}`:''}</p><span class="opd-badge">${eligible.length} waiting</span>${current?`<div class="opd-current"><strong>${esc(current.token)}</strong><h3>${esc(current.patient_name)}</h3><p>${esc(current.department)} &middot; ${esc(stage.state)}</p>${btn('visit','Patient record',`data-id="${esc(current.name)}"`)}</div>`:''}<div class="opd-actions">${actions}</div></article>`;
            }).join('');
            this.$root.find('.opd-content').html(`<div class="opd-room-grid">${cards||'<div class="opd-empty">No rooms are assigned. Ask a manager to configure your room in OPD Setup.</div>'}</div>`);
        }

        renderSetup(){
            const s=this.setup;
            const rooms=s.rooms.map((r,i)=>{
                const id=r.name||r.client_id, departments=s.routes.filter(t=>roomFields.some(f=>t[f]===id)).map(t=>t.department).join(', ');
                return `<tr><td><strong>${esc(r.room_number)}</strong><small>${esc(r.room_label)}</small></td><td>${esc(r.purpose)}</td><td>${esc(staffLabel(r))||'Not assigned'}</td><td>${esc(departments)||'Not yet used'}</td><td>${r.enabled?'Enabled':'Disabled'}</td><td>${btn('edit-room','Edit',`data-index="${i}"`)}</td></tr>`;
            }).join('');
            const routes=s.routes.map((r,i)=>`<tr><td><strong>${esc(r.department)}</strong></td>${roomFields.map(f=>{const room=s.rooms.find(x=>(x.name||x.client_id)===r[f]);return `<td>${esc(roomLabel(s.rooms,r[f]))}<small>${esc(staffLabel(room))}</small>${room?btn('edit-room','Edit room / staff',`data-index="${s.rooms.indexOf(room)}"`):'<span class="opd-error">Choose a room</span>'}</td>`;}).join('')}<td>${btn('edit-route','Edit route',`data-index="${i}"`)}</td></tr>`).join('');
            this.$root.find('.opd-content').html(`<div class="opd-notice">${this.dirty?'Unsaved changes. ':''}Changes apply to new check-ins. Use Apply to waiting patients to review existing visits separately.</div>
                <div class="opd-toolbar">${btn('save-setup','Save all changes','',true)}${btn('apply-routes','Apply to waiting patients')}${btn('display-users','Display accounts')}</div>
                <section class="opd-panel"><div class="opd-head"><h2>Rooms, purposes & staff</h2>${btn('add-room','Add room')}</div><div class="opd-scroll"><table class="opd-table"><thead><tr><th>Room</th><th>Purpose</th><th>Assigned staff / doctor</th><th>Used by departments</th><th>Status</th><th></th></tr></thead><tbody>${rooms||'<tr><td colspan="6">Add your first room.</td></tr>'}</tbody></table></div></section>
                <section class="opd-panel"><div class="opd-head"><h2>Department routes</h2>${btn('add-route','Add department route')}</div><div class="opd-scroll"><table class="opd-table"><thead><tr><th>Department</th><th>1. Vitals</th><th>2. Medical History</th><th>3. Consultation</th><th></th></tr></thead><tbody>${routes||'<tr><td colspan="5">Configure rooms above, then connect departments to their three rooms.</td></tr>'}</tbody></table></div></section>`);
        }
        editRoom(index){
            const r=index==null?{client_id:requestId(),enabled:1,purpose:'Vitals',assignments:[]}:this.setup.rooms[index];
            const d=new frappe.ui.Dialog({title:index==null?'Add room':`Edit Room ${r.room_number}`,size:'large',fields:[
                {fieldname:'room_number',label:'Room number',fieldtype:'Data',reqd:1,default:r.room_number},
                {fieldname:'room_label',label:'Room name (optional)',fieldtype:'Data',default:r.room_label},
                {fieldname:'purpose',label:'Purpose',fieldtype:'Select',options:stages,reqd:1,default:r.purpose},
                {fieldname:'enabled',label:'Enabled',fieldtype:'Check',default:r.enabled},
                {fieldname:'assignments',label:'Assigned staff and doctors',fieldtype:'Table',in_place_edit:true,data:structuredClone(r.assignments),fields:[
                    {fieldname:'user',label:'Staff account',fieldtype:'Select',options:['',...this.setup.users.map(u=>u.name)],in_list_view:1,reqd:1,columns:5},
                    {fieldname:'practitioner',label:'Doctor',fieldtype:'Select',options:['',...this.setup.practitioners.map(p=>p.name)],in_list_view:1,columns:5}
                ]},
                {fieldtype:'HTML',options:'<p>Consultation rooms require a doctor on every staff assignment. Editing this room affects all departments using it.</p>'}
            ],primary_action_label:'Apply to setup',primary_action:values=>{
                const updated={...r,...values,assignments:(values.assignments||[]).map(a=>({user:a.user,practitioner:a.practitioner||null}))};
                if(index==null)this.setup.rooms.push(updated);else this.setup.rooms[index]=updated;
                this.dirty=true;d.hide();this.renderSetup();
            }});d.show();
        }
        editRoute(index){
            const r=index==null?{}:this.setup.routes[index];
            const fields=[{fieldname:'department',label:'Department',fieldtype:'Select',options:['',...this.setup.departments],reqd:1,default:r.department},
                ...roomFields.map((f,i)=>({fieldname:f,label:stages[i]+' room',fieldtype:'Select',reqd:1,default:r[f],
                    options:[{value:'',label:'Select room'},...this.setup.rooms.filter(x=>x.enabled&&x.purpose===stages[i]).map(x=>({value:x.name||x.client_id,label:`Room ${x.room_number} &middot; ${staffLabel(x)}`}))]})),
                {fieldname:'booking_labels',label:'Booking disease labels / URLs (one exact value per line)',fieldtype:'Small Text',default:r.booking_labels,description:'Optional explicit mapping used to suggest the department at reception. Reception still confirms it.'}];
            const d=new frappe.ui.Dialog({title:'Department room route',fields,primary_action_label:'Apply to setup',primary_action:values=>{
                if(index==null)this.setup.routes.push(values);else this.setup.routes[index]={...r,...values};
                this.dirty=true;d.hide();this.renderSetup();
            }});d.show();
        }
        async saveSetup(){
            const s=this.setup, numbers=new Set(), departments=new Set();
            for(const r of s.rooms){
                const number=(r.room_number||'').trim().toLowerCase();
                if(!number||numbers.has(number)){frappe.msgprint('Each room must have a unique number.');return;}numbers.add(number);
                if(r.enabled && (!r.assignments.length || r.assignments.some(a=>!a.user || (r.purpose==='Doctor Consultation'&&!a.practitioner)))){frappe.msgprint(`Room ${esc(r.room_number)} needs valid staff/doctor assignments.`);return;}
            }
            for(const route of s.routes){
                if(!route.department||departments.has(route.department)){frappe.msgprint('Each route must have a distinct department.');return;}departments.add(route.department);
                for(let i=0;i<3;i++){const room=s.rooms.find(r=>(r.name||r.client_id)===route[roomFields[i]]);if(!room?.enabled||room.purpose!==stages[i]){frappe.msgprint(`${esc(route.department)} needs an enabled ${stages[i]} room.`);return;}}
            }
            if(!await confirm(`Save configuration for ${s.rooms.length} rooms and ${s.routes.length} departments?<br>Affected departments: ${s.routes.map(r=>esc(r.department)).join(', ') || 'None'}. Existing patients keep their saved routes.`))return;
            await this.mutate('save_setup',{expected_version:s.config_version,rooms:s.rooms,routes:s.routes,display_users:s.display_users});
            await this.refresh();frappe.show_alert({message:'OPD setup saved',indicator:'green'});
        }
        async applyRoutes(){
            if(this.dirty){frappe.msgprint('Save setup changes before applying routes to waiting patients.');return;}
            const preview=await read('preview_reroute',{clinic:this.clinic});
            if(!preview.patients.length){frappe.msgprint('No waiting patients need a route change.');return;}
            const rows=preview.patients.map(p=>`<tr><td>${esc(p.token)}</td><td>${esc(p.patient_name)}</td><td>${p.rooms.map(r=>esc(roomLabel(this.setup.rooms,r))).join(' &rarr; ')}</td><td>${p.blocked?esc(p.reason):'Will update'}</td></tr>`).join('');
            const eligible=preview.patients.filter(p=>!p.blocked);
            const d=new frappe.ui.Dialog({title:'Review waiting-patient route changes',size:'large',fields:[{fieldname:'preview',fieldtype:'HTML'},reasonField],primary_action_label:`Update ${eligible.length} waiting patients`,primary_action:async values=>{
                if(!eligible.length)return;d.disable_primary_action();
                try{await this.mutate('apply_routes',{expected_version:preview.config_version,patients:eligible.map(p=>({visit:p.visit,revision:p.revision})),reason:values.reason});d.hide();frappe.show_alert({message:'Waiting-patient routes updated',indicator:'green'});}finally{d.enable_primary_action();}
            }});d.fields_dict.preview.$wrapper.html(`<table class="opd-table"><thead><tr><th>Token</th><th>Patient</th><th>New rooms</th><th>Result</th></tr></thead><tbody>${rows}</tbody></table>`);d.show();if(!eligible.length)d.disable_primary_action();
        }
        async openVisit(id){
            const visit=await read('get_visit',{clinic:this.clinic,visit:id});
            const d=new frappe.ui.Dialog({title:`Token ${visit.token} - ${visit.patient_name}`,size:'large',fields:[{fieldname:'detail',fieldtype:'HTML'}]});
            const current=visit.stages[visit.stage_index];
            d.fields_dict.detail.$wrapper.html(`<p>${esc(visit.department)} &middot; Doctor: ${esc(visit.consultation_doctor_name || visit.consultation_doctor)}</p><table class="opd-table"><thead><tr><th>Stage</th><th>Room</th><th>Status</th></tr></thead><tbody>${visit.stages.map(s=>`<tr><td>${esc(s.stage)}</td><td>${esc(roomLabel(this.data.rooms,s.room))}</td><td>${esc(s.state)}</td></tr>`).join('')}</tbody></table>
                <div class="opd-actions" style="margin-top:20px">${btn('patient','Open patient record')}${visit.encounter?btn('encounter','Open encounter'):(frappe.model.can_create('Patient Encounter')?btn('new-encounter','New clinical encounter'):'')}${this.data.reception&&visit.status==='Active'?btn('reroute','Change department / doctor')+btn('priority',visit.priority?'Remove priority':'Mark priority')+(current.state==='On Hold'?btn('requeue','Return to queue'):'')+btn('withdraw','Withdraw visit'):''}</div>`);
            d.fields_dict.detail.$wrapper.on('click','[data-opd]',async e=>{
                const action=$(e.currentTarget).attr('data-opd');
                if(action==='new-encounter'){d.hide();frappe.new_doc('Patient Encounter',{patient:visit.patient,practitioner:visit.consultation_doctor,medical_department:visit.department});return;}
                if(action==='patient'||action==='encounter'){d.hide();frappe.set_route('Form',action==='patient'?'Patient':'Patient Encounter',action==='patient'?visit.patient:visit.encounter);return;}
                let fields=[reasonField];
                if(action==='reroute'){
                    const setup=await read('get_setup',{clinic:this.clinic});
                    fields=[{fieldname:'department',label:'Department',fieldtype:'Select',options:setup.routes.map(r=>r.department),default:visit.department,reqd:1},
                        {fieldname:'doctor',label:'Consultation doctor',fieldtype:'Select',options:setup.practitioners.map(p=>p.name),default:visit.consultation_doctor,reqd:1},reasonField];
                }
                frappe.prompt(fields,async values=>{await this.mutate('visit_action',{visit:id,expected_version:visit.revision,action,priority:visit.priority?0:1,...values});d.hide();},'Update visit','Save');
            });d.show();
        }
        async roomAction(action,sessionId){
            const session=this.data.sessions.find(s=>s.name===sessionId);
            if(!session)return;
            const send=values=>this.mutate('room_action',{session:session.name,expected_version:session.revision,action,...values});
            if(action==='release')return frappe.prompt([reasonField],send,'Release patient to hold','Release');
            if(action==='handover'){
                const room=this.data.rooms.find(r=>r.name===session.room);
                return frappe.prompt([{fieldname:'controller',label:'New controller',fieldtype:'Select',options:room.assignments.map(a=>a.user),reqd:1},
                    {fieldname:'practitioner',label:'Consultation doctor',fieldtype:'Select',options:['',...room.assignments.map(a=>a.practitioner).filter(Boolean)],default:session.practitioner},reasonField],send,'Transfer room control','Transfer');
            }
            if(['complete','absent','end'].includes(action) && !await confirm({complete:'Complete this stage and move the patient to the next step?',absent:'Mark this patient absent and release the room?',end:'End your room session?'}[action]))return;
            await send({});
        }
        renderTV(){
            const d=this.data, fresh=this.baseline!=null?d.calls.filter(c=>c.call_id>this.baseline):[];
            if(fresh.length)this.chime();
            this.baseline=d.sequence;
            this.$root.find('.opd-content').html(`<div class="opd-tv-grid">${d.calls.map(c=>`<article class="opd-tv-card ${fresh.some(x=>x.call_id===c.call_id)?'fresh':''}"><div class="opd-badge">${esc(c.stage)}</div><div class="opd-tv-token">${esc(c.token)}</div><div class="opd-tv-name">${esc(c.patient_name)}</div><div class="opd-tv-room">Please go to <strong>Room ${esc(c.room)}</strong></div>${c.visit_date!==d.date?`<small>Visit date: ${esc(c.visit_date)}</small>`:''}</article>`).join('')||'<div class="opd-empty"><h2>Please take a seat</h2><p>Your token and room will appear here when called.</p></div>'}</div>`);
        }
        chime(){
            if(!this.audio || this.audio.state!=='running')return;
            const oscillator=this.audio.createOscillator(),gain=this.audio.createGain();
            oscillator.connect(gain);gain.connect(this.audio.destination);oscillator.frequency.value=660;
            const at=this.audio.currentTime;gain.gain.setValueAtTime(0,at);gain.gain.linearRampToValueAtTime(.15,at+.03);gain.gain.exponentialRampToValueAtTime(.001,at+.8);oscillator.start(at);oscillator.stop(at+.85);
        }
        async action(action,button){
            if(this.busy)return;
            try{
                if(['queue','room','setup','calendar','tv','refresh'].includes(action)&&this.dirty&&!await confirm('Discard unsaved OPD setup changes?'))return;
                if(['queue','room','setup'].includes(action)){this.section=action;this.dirty=false;this.shell();await this.refresh();return;}
                if(action==='calendar'){if(this.host)this.host.switchSection('calendar');else frappe.set_route('doctor-clinical');return;}
                if(action==='tv'){window.open('/app/opd-display','_blank','noopener');return;}
                if(action==='refresh'){await this.refresh();return;}
                if(action==='sound'){this.audio ||= new (window.AudioContext||window.webkitAudioContext)();await this.audio.resume();button.text('Sound enabled');return;}
                if(action==='fullscreen'){await this.$root[0].requestFullscreen();return;}
                if(action==='walkin'){await checkin(this.clinic,null,()=>this.refresh());return;}
                if(action==='visit'){await this.openVisit(button.attr('data-id'));return;}
                if(action==='print'){receipt(await read('get_visit',{clinic:this.clinic,visit:button.attr('data-id')}),this.data.rooms);return;}
                if(action==='occupy'){
                    const room=this.data.rooms.find(r=>r.name===button.attr('data-room'));
                    const assignment=room.assignments.find(a=>a.user===this.data.user);
                    await this.mutate('start_session',{room:room.name,practitioner:assignment?.practitioner});return;
                }
                if(button.attr('data-session')){await this.roomAction(action,button.attr('data-session'));return;}
                if(action==='add-room'||action==='edit-room'){this.editRoom(action==='add-room'?null:Number(button.attr('data-index')));return;}
                if(action==='add-route'||action==='edit-route'){this.editRoute(action==='add-route'?null:Number(button.attr('data-index')));return;}
                if(action==='save-setup'){await this.saveSetup();return;}
                if(action==='apply-routes'){await this.applyRoutes();return;}
                if(action==='display-users'){
                    frappe.prompt([{fieldname:'users',label:'Display accounts',fieldtype:'MultiSelect',options:this.setup.display_accounts,default:this.setup.display_users.join(', '),description:'Accounts must have the OPD Display role. Assign that role in User settings first.'}],values=>{
                        this.setup.display_users=String(values.users||'').split(',').map(x=>x.trim()).filter(Boolean);this.dirty=true;this.renderSetup();
                    },'Waiting-room display access','Apply to setup');
                }
            }catch(error){/* Frappe displays validation errors. Keep the current screen/draft. */}
        }
    };
})();
