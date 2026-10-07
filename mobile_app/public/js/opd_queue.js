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
    const trashIcon = '<svg class="opd-trash-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6"/></svg>';
    const btn = (action, label, extra = '', primary = false) => `<button class="opd-btn ${primary ? 'primary' : ''} ${action==='delete-branch'?'opd-delete-btn':''}" data-opd="${action}" ${extra}>${action==='delete-branch'?trashIcon:''}${esc(label)}</button>`;
    const roomLabel = (rooms, id) => {const room = rooms.find(r => (r.name || r.client_id) === id); return room ? `OPD ${room.room_number}` : id;};
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
        const content = `<div class="opd-slip"><h3>OPD Visit Token</h3><p>${esc(visit.clinic_name||'')}</p><div class="token">${esc(visit.token)}</div>
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
            {fieldname:'doctor',label:'Consultation doctor',fieldtype:'Select',options:[{value:'',label:''},...opts(setup.practitioners.map(p=>({value:p.name,label:p.practitioner_name})))],hidden:!source,default:context.doctor,reqd:!!source},
            {fieldname:'route',fieldtype:'HTML'},
            {fieldname:'patient',label:'Existing patient',fieldtype:'Link',options:'Patient',default:context.patient,
                read_only:!!context.patient,get_query:()=>({query:`${API}.patient_query`,filters:{clinic}})},
            {fieldname:'new_patient',label:'Register a new patient',fieldtype:'Check',hidden:!!context.patient},
            {fieldname:'first_name',label:'Patient name',fieldtype:'Data',default:context.patient_name,depends_on:'eval:doc.new_patient',mandatory_depends_on:'eval:doc.new_patient'},
            {fieldname:'sex',label:'Gender',fieldtype:'Select',options:['',...setup.genders],depends_on:'eval:doc.new_patient',mandatory_depends_on:'eval:doc.new_patient'},
            {fieldname:'mobile',label:'Mobile number',fieldtype:'Data',depends_on:'eval:doc.new_patient',mandatory_depends_on:'eval:doc.new_patient'},
            {...reasonField,reqd:0,hidden:!source,label:'Reason for changing the booked doctor'}
        ];
        let busy=false;
        const d=new frappe.ui.Dialog({title:(source?'Check in & issue OPD token':'Walk-in check-in')+' - '+setup.clinic_name,fields,primary_action_label:'Issue token',primary_action:async values=>{
            if(busy)return;
            if(!source){
                const route=setup.routes.find(r=>r.department===values.department);
                const room=setup.rooms.find(r=>r.name===route?.consultation_room);
                const assigned=[...new Set((room?.assignments||[]).map(a=>a.practitioner).filter(Boolean))];
                if(assigned.length!==1){frappe.msgprint('Assign one consultation doctor to this department?s consultation room in OPD Setup before issuing a walk-in token.');return;}
                values.doctor=assigned[0];
            }
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
            const mismatch=source && route && d.get_value('doctor') && !assigned.includes(d.get_value('doctor'));
            d.fields_dict.route.$wrapper.html(route?`<div class="opd-notice">${(route.steps||roomFields.map((f,i)=>({stage:stages[i],room:route[f]}))).map(step=>`${esc(step.stage)}: <b>${esc(roomLabel(setup.rooms,step.room))}</b>`).join(' &rarr; ')}${source?`<br>Consultation doctors: ${assigned.map(id=>esc(setup.practitioners.find(p=>p.name===id)?.practitioner_name||id)).join(', ')}`:''}${mismatch?'<p class="opd-error">The selected doctor is not assigned to this consultation room. Choose an assigned doctor or update OPD Setup.</p>':''}</div>`:'');
        };
        d.fields_dict.department.df.onchange=showRoute;d.fields_dict.doctor.df.onchange=showRoute;d.show();showRoute();
    }
    mobile_app.opd_checkin = async (source, done) => {
        const boot=await read('bootstrap');
        if(!boot.reception){frappe.msgprint('Reception or an appointment manager must issue OPD tokens.');return;}
        if(!boot.clinics.length){frappe.msgprint('No OPD clinic is available.');return;}
        if(source?.clinic){await checkin(source.clinic,source,done);return;}
        if(boot.clinics.length===1){await checkin(boot.clinics[0].name,source,done);return;}
        frappe.prompt([{fieldname:'clinic',label:'Branch',fieldtype:'Select',reqd:1,options:boot.clinics.map(c=>({value:c.name,label:c.clinic_name}))}],values=>checkin(values.clinic,source,done),'Select check-in branch','Continue');
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
            this.fitDisplay=()=>{
                if(!this.display||!this.visible)return;
                const top=document.fullscreenElement===this.$root[0]?0:Math.max(0,this.$root[0].getBoundingClientRect().top);
                this.$root.css('height',`${Math.max(240,window.innerHeight-top)}px`);
            };
            this.focus=()=>{if(!document.hidden && this.visible && this.section!=='setup'){this.baseline=null;this.refresh();}};
            this.$root[0].addEventListener('error',event=>{if(event.target.matches('.opd-doctor-avatar img'))event.target.remove();},true);
            this.$root.on('click',event=>{if(!$(event.target).closest('.opd-branch-menu').length)this.$root.find('.opd-branch-menu').prop('open',false);});
            this.$root.on('keydown',event=>{if(event.key==='Escape'){this.$root.find('.opd-branch-menu').prop('open',false).find('summary').trigger('focus');}});
            this.$root.on('change','[data-opd-branch]',async event=>{
                if(this.busy)return;
                this.clinic=event.target.value;this.selectedRoom=null;this.baseline=null;this.data=null;this.dirty=false;
                if(this.host&&frappe.get_route()[1]==='room')frappe.set_route('doctor-clinical');
                const url=new URL(location.href);url.searchParams.set('clinic',this.clinic);history.replaceState(null,'',url);
                this.shell();await this.refresh();
            });
            this.$root.on('change','[data-opd-date]',event=>{this.queueDate=event.target.value||frappe.datetime.get_today();this.refresh();});
            this.$root.on('input change','[data-queue-filter]',event=>{
                this.queueSearch=this.$root.find('[data-queue-filter="search"]').val()||'';
                this.queueStatus=this.$root.find('[data-queue-filter="status"]').val()||'';
                this.filterQueue();
            });
            this.$root.on('click','[data-opd]',event=>{event.preventDefault();this.action($(event.currentTarget).attr('data-opd'),$(event.currentTarget));});
        }
        async show(){
            if(!this.visible){frappe.realtime.on('opd_queue_changed',this.changed);frappe.realtime.socket?.on('connect',this.connected);frappe.realtime.socket?.on('disconnect',this.disconnected);document.addEventListener('visibilitychange',this.focus);}
            this.visible=true;this.baseline=null;
            if(this.display&&!this.pollTimer)this.pollTimer=setInterval(()=>{
                if(this.visible&&!document.hidden&&!this.busy&&!this.refreshing)this.refresh();
            },5000);
            if(this.display){window.addEventListener('resize',this.fitDisplay);document.addEventListener('fullscreenchange',this.fitDisplay);requestAnimationFrame(this.fitDisplay);}
            try{
                this.boot=await read('bootstrap',{room:this.selectedRoom||null});
                this.clinic ||= this.boot.room_clinic || new URLSearchParams(location.search).get('clinic') || this.boot.clinics[0]?.name;
                if(this.clinic&&!this.boot.clinics.some(c=>c.name===this.clinic))throw new Error('Branch access denied');
                if(!this.clinic){this.$root.html('<div class="opd-empty">No clinic is assigned to your account. Contact an appointment manager.</div>');return;}
                if(this.boot.display_only && !this.display){frappe.set_route('opd-display');return;}
                this.section=this.display?'display':(this.host ? this.section : (frappe.get_route()[1] || 'queue'));
                if(this.section==='setup' && !this.boot.manager)this.section='queue';
                this.shell();await this.refresh();
            }catch(error){this.$root.html('<div class="opd-empty">Unable to load OPD. Check your connection and clinic access, then reload.</div>');}
        }
        hide(){clearInterval(this.pollTimer);this.pollTimer=null;window.removeEventListener('resize',this.fitDisplay);document.removeEventListener('fullscreenchange',this.fitDisplay);this.visible=false;++this.serial;frappe.realtime.off('opd_queue_changed',this.changed);frappe.realtime.socket?.off('connect',this.connected);frappe.realtime.socket?.off('disconnect',this.disconnected);document.removeEventListener('visibilitychange',this.focus);}
        connection(live){this.$root.find('.opd-connection').toggleClass('live',live).text(live?'Connected':'Disconnected - displayed information may be outdated');}
        shell(){
            const title=this.display?'Please wait for your token to be called':'OPD Patient Flow';
            this.$root.html(`<header class="opd-head"><div><h1>${title}</h1><p>${esc(this.boot.clinics.find(c=>c.name===this.clinic)?.clinic_name)}</p><label>Branch <select data-opd-branch aria-label="OPD branch">${this.boot.clinics.map(c=>`<option value="${esc(c.name)}" ${c.name===this.clinic?'selected':''}>${esc(c.clinic_name)}</option>`).join('')}</select></label></div><div class="opd-actions"><span class="opd-connection"></span>${this.display?btn('sound','Enable sound')+btn('fullscreen','Full screen'):(this.host?.opdOnly?'':btn('calendar','Appointment calendar'))}${btn('refresh','Refresh')}</div></header>
                ${this.display?'':`<nav class="opd-nav">${btn('queue','OPD Queue')}${btn('room','My OPD')}${this.boot.manager?btn('setup','OPD Setup'):''}${btn('tv','Waiting-Room Display')}<details class="opd-branch-menu"><summary class="opd-btn">Branch <span aria-hidden="true">&#9662;</span></summary><div class="opd-branch-menu-items">${this.boot.branch_admin?btn('add-branch','Add branch')+btn('manage-branches','Manage branches')+btn('edit-branch','Branch settings'):''}${btn('branch-summary','Branch summary')}</div></details></nav>`}<div class="opd-content"><div class="opd-empty">Loading...</div></div>`);
            this.$root.find(`[data-opd="${this.section}"]`).addClass('active');this.connection(!!frappe.realtime.socket?.connected);
        }
        async refresh(){
            if(!this.visible||!this.clinic)return;
            if(this.busy){this.refreshPending=true;return;}
            const serial=++this.serial;
            this.refreshing=true;
            try{
                const data=await read(this.display?'display_snapshot':this.section==='setup'?'get_setup':'snapshot',{clinic:this.clinic});
                if(!this.display && this.section==='queue'){
                    this.queueDate ||= frappe.datetime.get_today();
                    const daily=await read('daily_visits',{clinic:this.clinic,date:this.queueDate});
                    data.visits=daily.visits;data.daily=daily;
                }
                if(serial!==this.serial||!this.visible)return;
                if(data.rooms)data.rooms.sort((a,b)=>String(a.room_number).localeCompare(String(b.room_number),undefined,{numeric:true}));
                this.data=data;
                if(this.display)this.renderTV();else if(this.section==='setup'){this.setup=structuredClone(data);this.dirty=false;this.renderSetup();}else if(this.section==='room')this.renderRoom();else this.renderQueue();
            }catch(e){if(serial===this.serial)this.$root.find('.opd-content').prepend('<div class="opd-notice">Refresh failed. The information below may be outdated. Use Refresh to try again.</div>');}
            finally{this.refreshing=false;}
        }
        async mutate(method,args){
            if(this.busy)return;
            this.busy=true;this.$root.find('button').prop('disabled',true);
            try{return await write(method,{clinic:this.clinic,...args});}
            finally{this.busy=false;this.$root.find('button').prop('disabled',false);this.refreshPending=false;if(this.section!=='setup')await this.refresh();}
        }
        renderQueue(){
            const d=this.data;
            const rows=d.visits.map(v=>{const s=v.stages[v.stage_index],status=v.status==='Active'?s.state:v.status;return `<tr data-queue-state="${esc(status)}" data-queue-search="${esc([v.token,v.patient_name,v.consultation_doctor_name||v.consultation_doctor,v.department].join(' ').toLowerCase())}"><td><strong>${esc(v.token)}</strong><small>${esc(v.visit_date)}</small></td><td>${esc(v.patient_name)}${v.priority?'<small class="opd-badge priority">Priority</small>':''}</td><td>${esc(v.department)}<small>${esc(v.consultation_doctor_name || v.consultation_doctor)}</small></td><td>${esc(s.stage)}<small>${esc(roomLabel(d.rooms,s.room))}</small></td><td><span class="opd-badge" data-state="${esc(status)}">${esc(status)}</span>${v.status==='Active'?`<small>${waitLabel(s.queued_at)} since queue entry</small>`:''}</td><td>${btn('visit','Open',`data-id="${esc(v.name)}"`)}${btn('print','Print token',`data-id="${esc(v.name)}"`)}${v.status==='Active'?btn('manage-visit','Manage visit',`data-id="${esc(v.name)}"`):''}</td></tr>`;}).join('');
            this.$root.find('.opd-content').html(`<div class="opd-toolbar"><h2>Daily visits <span class="opd-count">${d.visits.length}</span></h2>${d.reception?btn('walkin','Check in walk-in','',true):''}${d.manager?btn('end-day','End day'):''}<div class="opd-queue-filters"><input type="date" data-opd-date aria-label="Visit date" value="${esc(this.queueDate)}"><input type="search" data-queue-filter="search" aria-label="Filter patients or tokens" placeholder="Filter patient or token..." value="${esc(this.queueSearch||'')}"><select data-queue-filter="status" aria-label="Filter queue status">${['','Waiting','Called','In Progress','On Hold','Completed','Withdrawn'].map(x=>`<option value="${esc(x)}" ${x===(this.queueStatus||'')?'selected':''}>${esc(x||'All statuses')}</option>`).join('')}</select></div></div><div class="opd-day-counts">${Object.entries(d.daily?.counts||{}).map(([label,count])=>`<div><small>${esc(label)}</small><strong>${count}</strong></div>`).join('')}</div>${d.daily?.previous_active?`<div class="opd-notice">${d.daily.previous_active} unfinished visits from earlier dates remain active in My OPD. Review them before ending the day.</div>`:''}<div class="opd-panel opd-scroll">${rows?`<table class="opd-table"><thead><tr><th>Token</th><th>Patient</th><th>Department / Doctor</th><th>Stage / OPD</th><th>Status</th><th>Actions</th></tr></thead><tbody>${rows}</tbody></table>`:'<div class="opd-empty">No visits for this date. Select another date or check in a patient.</div>'}</div>`);
            this.filterQueue();
        }
        filterQueue(){
            const search=(this.queueSearch||'').trim().toLowerCase(),status=this.queueStatus||'';
            let count=0;
            this.$root.find('[data-queue-state]').each((_,row)=>{
                const match=(!status||row.dataset.queueState===status)&&(!search||row.dataset.queueSearch.includes(search));
                row.hidden=!match;if(match)count++;
            });
            this.$root.find('.opd-filter-empty').remove();
            if(!count&&this.data.visits.length)this.$root.find('.opd-scroll').append('<div class="opd-empty opd-filter-empty">No patients match these filters.</div>');
        }
        roomControls(room,session,current,stage){
            if(!session)return btn('occupy','Start OPD session',`data-room="${esc(room.name)}"`,true);
            const attr=`data-session="${esc(session.name)}"`;
            return (!current&&session.status==='Available'?btn('call_next','Call next patient',attr,true):'')+
                (stage?.state==='Called'?btn('start','Patient arrived - Start',attr,true)+btn('recall','Call again',attr)+btn('absent','Patient not present',attr):'')+
                (stage?.state==='In Progress'?btn(session.status==='Paused'?'complete':'complete_next',session.status==='Paused'?'Complete stage':'Finish & call next',attr,true)+btn('release','Release patient',attr):'')+
                btn(session.status==='Paused'?'resume':'pause',session.status==='Paused'?'Resume':'Pause',attr)+(!current?btn('end','Close OPD',attr):'');
        }
        roomDoctors(room,session){
            const doctors=room.doctors||[];
            if(!doctors.length)return '<p class="opd-doctor-empty">No doctor assigned</p>';
            return `<div class="opd-room-doctors">${doctors.map(doctor=>{
                const name=doctor.practitioner_name||doctor.name;
                const initials=name.replace(/^dr[. ]*/i,'').split(/\s+/).filter(Boolean).slice(0,2).map(x=>x[0]).join('').toUpperCase();
                const photo=String(doctor.image||'');
                const safePhoto=/^(https?:\/\/|\/(?!\/))/.test(photo);
                return `<div class="opd-room-doctor"><div class="opd-doctor-avatar"><span>${esc(initials)}</span>${safePhoto?`<img src="${esc(photo)}" alt="${esc(name)}" loading="lazy">`:''}</div><div><strong>${esc(name)}</strong>${doctor.sr_qualification?`<small>${esc(doctor.sr_qualification)}</small>`:''}${doctor.diseases?.length?`<small>${doctor.diseases.map(esc).join(' ? ')}</small>`:''}</div><span class="opd-badge opd-doctor-status">${esc(session?.status||'Available')}</span></div>`;
            }).join('')}</div>`;
        }
        renderRoom(){
            const d=this.data;
            if(this.selectedRoom){this.renderRoomDetail();return;}
            const cards=d.rooms.filter(r=>r.enabled).map(room=>{
                const session=d.sessions.find(s=>s.room===room.name);
                const waiting=d.visits.filter(v=>{const s=v.stages[v.stage_index];return s.room===room.name&&s.state==='Waiting';}).length;
                return `<article class="opd-room-card" data-purpose="${esc(room.purpose_name||room.purpose)}"><h2>OPD ${esc(room.room_number)}</h2><p>${esc(room.purpose_name||room.purpose)} &middot; ${esc(room.room_label)}</p><p>${session?`${esc(session.controller)} &middot; ${esc(session.status)}`:'Available'}</p><span class="opd-badge">${waiting} waiting</span><div class="opd-actions">${btn('open-room',session?'View OPD':'Open OPD',`data-room="${esc(room.name)}"`,true)}</div></article>`;
            }).join('');
            this.$root.find('.opd-content').html(`<div class="opd-room-grid">${cards||'<div class="opd-empty">No enabled rooms are available. Ask a manager to configure rooms in OPD Setup.</div>'}</div>`);
        }
        renderRoomDetail(){
            const d=this.data,room=d.rooms.find(r=>r.name===this.selectedRoom);
            if(!room){this.$root.find('.opd-content').html(`${btn('back-rooms','Back to OPDs')}<div class="opd-empty">This room is no longer available.</div>`);return;}
            const session=d.sessions.find(s=>s.room===room.name),current=d.visits.find(v=>v.name===session?.current_visit),stage=current?.stages[current.stage_index];
            const visits=d.visits.filter(v=>v.stages[v.stage_index].room===room.name);
            const waiting=visits.filter(v=>v.stages[v.stage_index].state==='Waiting').sort((a,b)=>(b.priority-a.priority)||String(a.stages[a.stage_index].queued_at).localeCompare(String(b.stages[b.stage_index].queued_at))||a.name.localeCompare(b.name));
            const held=visits.filter(v=>v.stages[v.stage_index].state==='On Hold');
            const rows=list=>list.map(v=>`<tr><td><strong>${esc(v.token)}</strong>${v.priority?'<small>Priority</small>':''}</td><td>${esc(v.patient_name)}</td><td>${esc(v.department)}</td><td>${esc(v.consultation_doctor_name||v.consultation_doctor)}</td><td>${esc(waitLabel(v.stages[v.stage_index].queued_at))}</td><td>${btn('visit','Open patient',`data-id="${esc(v.name)}"`)}</td></tr>`).join('');
            const table=(title,list)=>`<section class="opd-panel"><h3>${esc(title)} (${list.length})</h3>${list.length?`<div style="overflow:auto"><table class="opd-table"><thead><tr><th>Token</th><th>Patient</th><th>Department</th><th>Doctor</th><th>Waiting</th><th></th></tr></thead><tbody>${rows(list)}</tbody></table></div>`:'<p>No patients here.</p>'}</section>`;
            this.$root.find('.opd-content').html(`<div class="opd-room-layout"><main class="opd-room-main"><div class="opd-room-title"><div><h1>OPD ${esc(room.room_number)}</h1><p class="opd-room-subtitle">${esc(room.purpose_name||room.purpose)} &middot; ${esc(room.room_label)}</p></div>${btn('back-rooms','Back to OPDs')}</div>
                <section class="opd-panel opd-room-current"><h3>Current patient</h3>${current?`<div class="opd-current"><strong>${esc(current.token)}</strong><h2>${esc(current.patient_name)}</h2><p>${esc(current.department)} &middot; ${esc(stage.state)}</p>${btn('visit','Patient details',`data-id="${esc(current.name)}"`)}</div>`:'<p>No patient is currently being seen.</p>'}<div class="opd-actions">${room.enabled?this.roomControls(room,session,current,stage):'<p>This room is disabled.</p>'}${session?btn('room-vitals','Vitals',`data-session-vitals="${esc(session.name)}"`):''}</div></section>
                ${current?.vitals?`<section class="opd-panel"><h3>Latest vitals</h3><p>${esc(current.vitals.recorded_at)}</p><div class="opd-vitals-summary">${current.vitals.options.filter(o=>current.vitals.values[o.id]!=null).map(o=>`<div><small>${esc(o.label)}</small><strong>${esc(current.vitals.values[o.id])} ${esc(o.unit)}</strong></div>`).join('')}</div></section>`:''}
                ${room.purpose==='Doctor Consultation'&&session?.practitioner?'<p>Call next selects patients for the current doctor. Patients for other doctors remain waiting.</p>':''}${table('Waiting patients',waiting)}${table('On hold',held)}</main><aside class="opd-room-profiles" aria-label="Assigned doctors">${this.roomDoctors(room,session)}</aside></div>`);
        }

        renderSetup(){
            const s=this.setup;
            const rooms=s.rooms.map((r,i)=>{
                const id=r.name||r.client_id, departments=s.routes.filter(t=>(t.steps||roomFields.map(f=>({room:t[f]}))).some(step=>step.room===id)).map(t=>t.department).join(', ');
                return `<tr><td><strong>${esc(r.room_number)}</strong><small>${esc(r.room_label)}</small></td><td>${esc(s.purposes.find(p=>p.id===(r.purpose_id||r.purpose))?.name||r.purpose)}</td><td>${esc(staffLabel(r))||'Not assigned'}</td><td>${esc(departments)||'Not yet used'}</td><td>${r.enabled?'Enabled':'Disabled'}</td><td>${btn('edit-room','Edit',`data-index="${i}"`)}${btn('delete-room','Delete',`data-index="${i}"`)}</td></tr>`;
            }).join('');
            const routePurposes=[...s.purposes].sort((a,b)=>(a.stage==='Doctor Consultation')-(b.stage==='Doctor Consultation'));
            const routes=s.routes.map((r,i)=>`<tr><td><strong>${esc(r.department)}</strong></td>${routePurposes.map(p=>{const step=r.steps?.find(x=>x.purpose_id===p.id);const room=s.rooms.find(x=>(x.name||x.client_id)===step?.room);return `<td>${room?esc(roomLabel(s.rooms,step.room)):'Not included'}${room?`<small>${esc(staffLabel(room))}</small>`:''}</td>`;}).join('')}<td>${btn('edit-route','Edit route',`data-index="${i}"`)}${btn('delete-route','Delete',`data-index="${i}"`)}</td></tr>`).join('');
            this.$root.find('.opd-content').html(`<div class="opd-notice">Changes save automatically when you submit a form. Existing patients keep their saved room assignments.</div>
                <section class="opd-panel"><div class="opd-head"><h2>OPD purposes</h2>${btn('add-purpose','Add purpose')}</div><p>Add, edit or delete the purpose options available when configuring a room.</p><div class="opd-purpose-grid">${s.purposes.map((p,i)=>`<article class="opd-purpose-card"><h3>${esc(p.name)}</h3><div class="opd-actions">${btn('edit-purpose','Edit',`data-index="${i}"`)}${btn('delete-purpose','Delete',`data-index="${i}"`)}</div></article>`).join('')||'<div class="opd-empty">Add a purpose to configure rooms.</div>'}</div></section>
                <section class="opd-panel"><div class="opd-head"><h2>OPDs, purposes & doctors</h2>${btn('add-room','Add OPD')}</div><div class="opd-scroll"><table class="opd-table"><thead><tr><th>OPD</th><th>Purpose</th><th>Doctors</th><th>Used by departments</th><th>Status</th><th></th></tr></thead><tbody>${rooms||'<tr><td colspan="6">Add your first room.</td></tr>'}</tbody></table></div></section>
                <section class="opd-panel"><div class="opd-head"><h2>Department routes</h2>${btn('add-route','Add department route')}</div><div class="opd-scroll"><table class="opd-table"><thead><tr><th>Department</th>${routePurposes.map(p=>`<th>${esc(p.name)}</th>`).join('')}<th></th></tr></thead><tbody>${routes||'<tr><td colspan="5">Configure rooms above, then select the purposes and rooms each department needs.</td></tr>'}</tbody></table></div></section>`);
        }
        async editBranch(edit){
            const setup=await read('get_setup',{clinic:this.clinic});
            const branch=edit?await read('branch_details',{clinic:this.clinic}):{};
            const d=new frappe.ui.Dialog({title:edit?'Branch settings':'Add branch',fields:[
                {fieldname:'branch_name',label:'Branch name',fieldtype:'Data',reqd:1,default:branch.clinic_name},
                {fieldname:'code',label:'Token prefix',fieldtype:'Data',reqd:1,default:branch.code,description:'For example GGN or NOD. New tokens use this prefix.'},
                {fieldname:'address',label:'Address',fieldtype:'Small Text',default:branch.address},
                {fieldname:'members',label:'Users with OPD access at this branch',fieldtype:'MultiSelect',options:setup.users.map(u=>u.name),default:(branch.members||[]).join(', '),description:'Branch administrators have access to every branch.'},
                {fieldname:'enabled',label:'Active branch',fieldtype:'Check',default:edit?branch.enabled:1},
                {fieldname:'copy_setup',label:'Copy rooms, purposes and routes from current branch',fieldtype:'Check',hidden:edit},
                {fieldname:'display_users',label:'Waiting-room display accounts',fieldtype:'MultiSelect',options:setup.display_accounts,default:edit?setup.display_users.join(', '):'',hidden:!edit}
            ],primary_action_label:'Done',primary_action:async values=>{
                d.disable_primary_action();
                try{
                    const result=await this.mutate('save_branch',{branch_name:values.branch_name,code:values.code,address:values.address,enabled:values.enabled,copy_setup:values.copy_setup,members:String(values.members||'').split(',').map(x=>x.trim()).filter(Boolean),branch:branch.name,expected_revision:branch.revision});
                    if(edit){const current=await read('get_setup',{clinic:this.clinic});await this.mutate('save_setup',{expected_version:current.config_version,rooms:current.rooms,routes:current.routes,purposes:current.purposes,display_users:String(values.display_users||'').split(',').map(x=>x.trim()).filter(Boolean)});}
                    d.hide();this.clinic=values.enabled?result.branch:null;this.boot=await read('bootstrap');this.clinic ||= this.boot.clinics[0]?.name;this.selectedRoom=null;this.section='setup';await this.show();
                }finally{d.enable_primary_action();}
            }});d.show();
        }
        editPurpose(index){
            const p=index==null?{id:requestId(),stage:'Vitals'}:this.setup.purposes[index];
            const d=new frappe.ui.Dialog({title:index==null?'Add purpose':'Edit purpose',fields:[
                {fieldname:'name',label:'Purpose name',fieldtype:'Data',reqd:1,default:p.name}
            ],primary_action_label:'Done',primary_action:async values=>{
                values.name=values.name.trim();
                if(!values.name||this.setup.purposes.some(x=>x.id!==p.id&&x.name.toLowerCase()===values.name.toLowerCase())){frappe.msgprint('Enter a unique purpose name.');return;}
                const next=structuredClone(this.setup);
                if(index==null)next.purposes.push({...p,...values});else next.purposes[index]={...p,...values};
                await this.saveSetup(next,d);
            }});d.show();
        }
        editRoom(index){
            const r=index==null?{client_id:requestId(),enabled:1,purpose:this.setup.purposes[0]?.stage,purpose_id:this.setup.purposes[0]?.id,assignments:[]}:this.setup.rooms[index];
            const d=new frappe.ui.Dialog({title:index==null?'Add OPD':`Edit OPD ${r.room_number}`,size:'large',fields:[
                {fieldname:'room_number',label:'OPD number',fieldtype:'Data',reqd:1,default:r.room_number},
                {fieldname:'room_label',label:'OPD name (optional)',fieldtype:'Data',default:r.room_label},
                {fieldname:'purpose_id',label:'Purpose',fieldtype:'Select',options:this.setup.purposes.map(p=>({value:p.id,label:p.name})),reqd:1,default:r.purpose_id||r.purpose},
                {fieldname:'enabled',label:'Enabled',fieldtype:'Check',default:r.enabled},
                {fieldname:'assignments',label:'Doctors',fieldtype:'Table',in_place_edit:true,data:structuredClone(r.assignments),fields:[
                    {fieldname:'practitioner',label:'Doctor',fieldtype:'Select',options:['',...this.setup.practitioners.map(p=>p.name)],in_list_view:1,columns:5}
                ]},
                {fieldtype:'HTML',options:'<p>Consultation rooms require at least one doctor. Authorized OPD staff can open any available room. Editing this room affects all departments using it.</p>'}
            ],primary_action_label:'Done',primary_action:async values=>{
                const updated={...r,...values,purpose:this.setup.purposes.find(p=>p.id===values.purpose_id).stage,assignments:(values.assignments||[]).filter(a=>a.practitioner).map(a=>({practitioner:a.practitioner}))};
                const next=structuredClone(this.setup);
                if(index==null)next.rooms.push(updated);else next.rooms[index]=updated;
                await this.saveSetup(next,d);
            }});d.show();
        }
        editRoute(index){
            const r=index==null?{}:this.setup.routes[index];
            const purposes=[...this.setup.purposes].sort((a,b)=>(a.stage==='Doctor Consultation')-(b.stage==='Doctor Consultation'));
            const fields=[{fieldname:'department',label:'Department',fieldtype:'Select',options:['',...this.setup.departments],reqd:1,default:r.department},
                ...purposes.map((p,i)=>({fieldname:'purpose_'+i,label:p.name+' OPD',fieldtype:'Select',
                    default:r.steps?.find(x=>x.purpose_id===p.id)?.room,
                    options:[{value:'',label:'Not included'},...this.setup.rooms.filter(x=>x.enabled&&(x.purpose_id||x.purpose)===p.id).map(x=>({value:x.name||x.client_id,label:`OPD ${x.room_number} - ${staffLabel(x)}`}))],
                    description:this.setup.rooms.some(x=>x.enabled&&(x.purpose_id||x.purpose)===p.id)?'':'Create a room with this purpose to select it here.'})),
                {fieldname:'booking_labels',label:'Booking disease labels / URLs (one exact value per line)',fieldtype:'Small Text',default:r.booking_labels}];
            const d=new frappe.ui.Dialog({title:'Department OPD route',fields,primary_action_label:'Done',primary_action:async values=>{
                const steps=purposes.map((p,i)=>({purpose_id:p.id,stage:p.name,room:values['purpose_'+i]})).filter(x=>x.room);
                const updated={...r,department:values.department,booking_labels:values.booking_labels,steps};
                roomFields.forEach((f,i)=>updated[f]=steps.find(x=>x.purpose_id===stages[i])?.room||null);
                const next=structuredClone(this.setup);
                if(index==null)next.routes.push(updated);else next.routes[index]=updated;
                await this.saveSetup(next,d);
            }});d.show();
        }
        async saveSetup(next=this.setup,dialog=null){
            if(this.busy)return;
            dialog?.disable_primary_action();
            try{
                await this.mutate('save_setup',{expected_version:next.config_version,rooms:next.rooms,routes:next.routes,display_users:next.display_users,purposes:next.purposes});
                dialog?.hide();this.dirty=false;
                await this.refresh();frappe.show_alert({message:'Saved',indicator:'green'});
            }catch(error){
                // Preserve entered dialog values; the rejected candidate never changes the saved setup.
                frappe.show_alert({message:'Not saved. Check the error and try again.',indicator:'red'});
            }finally{dialog?.enable_primary_action();}
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
            const data=await read('patient_overview',{clinic:this.clinic,visit:id});
            const visit=data.visit,clinic=this.clinic,current=visit.stages[visit.stage_index];
            const renderFields=fields=>`<dl class="opd-read-fields">${fields.map(f=>{
                let value=f.value;
                if(f.type==='Table'||f.type==='Table MultiSelect')return `<div class="opd-read-wide"><dt>${esc(f.label)}</dt><dd>${value?.length?value.map((row,i)=>`<details><summary>Entry ${i+1}</summary>${renderFields(row)}</details>`).join(''):'--'}</dd></div>`;
                if(f.type==='Check')value=value?'Yes':'No';
                if(f.type==='Text Editor')value=new DOMParser().parseFromString(String(value||''),'text/html').body.textContent;
                return `<div><dt>${esc(f.label)}</dt><dd>${esc(value==null||value===''?'--':value)}</dd></div>`;
            }).join('')}</dl>`;
            const d=new frappe.ui.Dialog({title:'Patient details',fields:[{fieldname:'viewer',fieldtype:'HTML'}]});
            d.$wrapper.addClass('opd-patient-drawer');
            const $body=d.fields_dict.viewer.$wrapper;
            const sex=data.patient.fields.find(f=>f.label==='Sex'||f.label==='Gender')?.value;
            const patientValue=label=>data.patient.fields.find(f=>f.label===label)?.value;
            const dob=patientValue('Date of Birth')||patientValue('DOB');
            const age=dob&&moment(dob).isValid()?moment().diff(moment(dob),'years'):null;
            const vital=label=>data.vitals?.fields.find(f=>f.label===label)?.value;
            const bp=vital('Blood Pressure')||(vital('Blood Pressure (systolic)')&&vital('Blood Pressure (diastolic)')?`${vital('Blood Pressure (systolic)')}/${vital('Blood Pressure (diastolic)')}`:null);
            const pulse=vital('Heart Rate / Pulse'),spo2=vital('SpO2')||vital('Oxygen Saturation');
            const $header=$(`<header class="opd-patient-banner"><div class="opd-patient-heading"><h2>${esc(visit.patient_name)}</h2><p>UHID: ${esc(visit.patient)}${sex?' &bull; '+esc(sex):''}${age!=null?' &bull; '+age+' Yrs':''}</p></div><span class="opd-badge opd-header-token">TOKEN ${esc(visit.token)}</span><button type="button" class="opd-drawer-close" aria-label="Close patient details">&times;</button></header>`);
            d.$wrapper.find('.modal-header').hide();d.$wrapper.find('.modal-content').prepend($header);
            $header.on('click','.opd-drawer-close',()=>d.hide());
            $body.html(`<section class="opd-read-section"><div class="opd-visit-context"><div><small>Assigned OPD</small><strong>${esc(current.stage)} - ${esc(roomLabel(this.data.rooms,current.room))}</strong></div><div><small>Status</small><span class="opd-drawer-status">${esc(visit.status==='Active'?current.state:visit.status)}</span></div></div>
                <h4 class="opd-drawer-section-title">Appointment Info</h4><table class="opd-appointment-table"><tbody>${[
                    ['Department',visit.department],['Doctor',visit.consultation_doctor_name||visit.consultation_doctor],
                    ['Visit type',visit.source_doctype?'Appointment':'Walk-in']
                ].map(([label,value])=>`<tr><th scope="row">${esc(label)}</th><td>${esc(value||'--')}</td></tr>`).join('')}</tbody></table>
                <h4 class="opd-drawer-section-title">Vitals Summary (Today)</h4><div class="opd-vitals-summary">${[['Blood Pressure',bp?bp+' mmHg':null],['Pulse',pulse?pulse+' bpm':null],['SpO2',spo2?spo2+'%':null]].concat((data.room_vitals?.options||[]).filter(o=>!['systolic','diastolic','pulse','spo2'].includes(o.id)&&data.room_vitals.values[o.id]!=null&&String(data.room_vitals.recorded_at).slice(0,10)===frappe.datetime.get_today()).map(o=>[o.label,String(data.room_vitals.values[o.id])+' '+o.unit])).map(([label,value])=>`<div><small>${label}</small><strong>${esc(value||'--')}</strong></div>`).join('')}</div>
                </section>
                <nav class="opd-patient-tabs" aria-label="Patient records">${[['invoice','Invoice'],['prescription','Prescription'],['history','Clinical history'],['dispatch','Dispatch details']].map(([key,label])=>`<button type="button" data-patient-tab="${key}">${label}</button>`).join('')}</nav><section class="opd-read-section opd-patient-records" aria-live="polite"></section>`);
            const $footer=$('<footer class="opd-drawer-footer"><button type="button" class="opd-drawer-dismiss">Close</button><button type="button" class="opd-drawer-print">Print Token Ticket</button></footer>');
            d.$wrapper.find('.modal-content').append($footer);
            $footer.on('click','.opd-drawer-dismiss',()=>d.hide());
            $footer.on('click','.opd-drawer-print',()=>{d.hide();receipt(visit,this.data.rooms);});
            let serial=0,tab='invoice',offset=0;
            const load=async (selected,append=false)=>{
                const request=++serial;tab=selected;if(!append)offset=0;
                $body.find('[data-patient-tab]').removeClass('active').attr('aria-pressed','false').filter(`[data-patient-tab="${tab}"]`).addClass('active').attr('aria-pressed','true');
                const area=$body.find('.opd-patient-records');if(!append)area.html('<p>Loading records...</p>');
                area.find('[data-patient-more]').prop('disabled',true);
                try{
                    const result=await read('patient_records',{clinic,visit:id,tab,offset});if(request!==serial)return;
                    if(!append)area.empty();area.find('[data-patient-more]').remove();
                    area.append(result.records.map(r=>`<details class="opd-read-record"><summary>${esc(r.doctype)} ? ${esc(r.name)}</summary>${renderFields(r.fields)}</details>`).join(''));
                    if(!append&&!result.records.length)area.append('<p>No records available in this tab.</p>');
                    if(result.restricted.length)area.append('<p class="opd-read-note">Some records require additional viewing permissions.</p>');
                    offset=result.next_offset;if(result.has_more)area.append('<button type="button" class="opd-btn" data-patient-more>Load more</button>');
                }catch(error){if(request===serial)area.html('<p>Unable to load these records. Select the tab to retry.</p>');}
            };
            $body.on('click','[data-patient-tab]',event=>load(event.currentTarget.dataset.patientTab));
            $body.on('click','[data-patient-more]',()=>load(tab,true));
            d.$wrapper.on('hidden.bs.modal',()=>{serial++;});d.show();await load('invoice');
        }
        async manageVisit(id){
            const visit=await read('get_visit',{clinic:this.clinic,visit:id});
            const d=new frappe.ui.Dialog({title:`Token ${visit.token} - ${visit.patient_name}`,size:'large',fields:[{fieldname:'detail',fieldtype:'HTML'}]});
            const current=visit.stages[visit.stage_index];
            d.fields_dict.detail.$wrapper.html(`<p>${esc(visit.department)} &middot; Doctor: ${esc(visit.consultation_doctor_name || visit.consultation_doctor)}</p><table class="opd-table"><thead><tr><th>Stage</th><th>OPD</th><th>Status</th></tr></thead><tbody>${visit.stages.map(s=>`<tr><td>${esc(s.stage)}</td><td>${esc(roomLabel(this.data.rooms,s.room))}</td><td>${esc(s.state)}</td></tr>`).join('')}</tbody></table>
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
            if(['complete','complete_next','absent','end'].includes(action) && !await confirm({complete_next:'Finish this patient&#39;s stage and call the next waiting patient? If nobody is waiting, the room stays available.',complete:'Complete this stage and move the patient to the next step?',absent:'Mark this patient absent and release the room?',end:'End your room session?'}[action]))return;
            await send({});
        }
        renderTV(){
            const d=this.data, fresh=this.baseline!=null?d.calls.filter(c=>c.call_id>this.baseline):[];
            if(fresh.length)this.chime();
            this.baseline=d.sequence;
            this.$root.find('.opd-content').html(`<div class="opd-tv-grid">${d.calls.map(c=>`<article class="opd-tv-card ${fresh.some(x=>x.call_id===c.call_id)?'fresh':''}"><div class="opd-badge">${esc(c.stage)}</div><div class="opd-tv-token">${esc(c.token)}</div><div class="opd-tv-name">${esc(c.patient_name)}</div><div class="opd-tv-room">Please go to <strong>OPD ${esc(c.room)}</strong></div>${c.visit_date!==d.date?`<small>Visit date: ${esc(c.visit_date)}</small>`:''}</article>`).join('')||'<div class="opd-empty"><h2>Please take a seat</h2><p>Your token and room will appear here when called.</p></div>'}</div>`);
        }
        chime(){
            if(!this.audio || this.audio.state!=='running')return;
            const oscillator=this.audio.createOscillator(),gain=this.audio.createGain();
            oscillator.connect(gain);gain.connect(this.audio.destination);oscillator.frequency.value=660;
            const at=this.audio.currentTime;gain.gain.setValueAtTime(0,at);gain.gain.linearRampToValueAtTime(.15,at+.03);gain.gain.exponentialRampToValueAtTime(.001,at+.8);oscillator.start(at);oscillator.stop(at+.85);
        }
        async action(action,button){
            if(this.busy)return;
            this.$root.find('.opd-branch-menu').prop('open',false);
            try{
                if(['queue','room','setup','calendar','tv','refresh'].includes(action)&&this.dirty&&!await confirm('Discard unsaved OPD setup changes?'))return;
                if(['queue','room','setup'].includes(action)){this.selectedRoom=null;if(this.host && frappe.get_route()[1]==='room')frappe.set_route('doctor-clinical');this.section=action;this.dirty=false;this.shell();await this.refresh();return;}
                if(action==='calendar'){if(this.host)this.host.switchSection('calendar');else frappe.set_route('doctor-clinical');return;}
                if(action==='tv'){window.open('/app/opd-display?clinic='+encodeURIComponent(this.clinic),'_blank','noopener');return;}
                if(action==='end-day'){
                    const preview=await read('preview_end_day',{clinic:this.clinic,date:this.queueDate});
                    const d=new frappe.ui.Dialog({title:'End day - '+preview.date,fields:[
                        {fieldtype:'HTML',fieldname:'preview'},
                        {fieldtype:'Check',fieldname:'close_visits',label:'Close unfinished visits as Withdrawn',default:1,description:'Includes earlier dates. Completed visits and token history are preserved.'},
                        {...reasonField}
                    ],primary_action_label:'Confirm end day',primary_action:async values=>{
                        d.disable_primary_action();
                        try{const result=await this.mutate('end_day',{date:preview.date,expected_sequence:preview.sequence,reason:values.reason,close_visits:values.close_visits});d.hide();frappe.show_alert(`${result.closed_rooms.length} room sessions closed; ${result.closed_visits.length} visits withdrawn.`);}
                        finally{d.enable_primary_action();}
                    }});
                    d.fields_dict.preview.$wrapper.html(`<p>${preview.rooms.length} empty room sessions will close. Closing unfinished visits also releases their called/in-progress patients and closes those room sessions.</p><p>${preview.visits.length} unfinished visits through this date:</p><div style="max-height:260px;overflow:auto">${preview.visits.map(v=>`<p><b>${esc(v.token)}</b> - ${esc(v.patient_name)} - ${esc(v.visit_date)} - ${esc(v.stages[v.stage_index].state)}</p>`).join('')||'<p>None</p>'}</div>${preview.blocked.length?'<div class="opd-notice">Called/in-progress patients will be released automatically when you close unfinished visits. Their treatment will not be marked completed.</div>':''}`);
                    d.show();return;
                }
                if(action==='refresh'){await this.refresh();return;}
                if(action==='sound'){this.audio ||= new (window.AudioContext||window.webkitAudioContext)();await this.audio.resume();button.text('Sound enabled');return;}
                if(action==='fullscreen'){await this.$root[0].requestFullscreen();return;}
                if(action==='walkin'){await checkin(this.clinic,null,()=>this.refresh());return;}
                if(action==='manage-visit'){await this.manageVisit(button.attr('data-id'));return;}
                if(action==='room-vitals'){
                    const session=button.attr('data-session-vitals');
                    let details=await read('room_vitals',{clinic:this.clinic,session});
                    const draft={...details.values};
                    const d=new frappe.ui.Dialog({title:'Vitals',fields:[{fieldname:'options',fieldtype:'HTML'}],primary_action_label:'Save vitals',primary_action:async()=>{
                        capture();if(this.busy)return;
                        if(!details.visit){frappe.msgprint('Start a patient in this room before saving values.');return;}
                        d.disable_primary_action();
                        try{await this.mutate('update_room_vitals',{session,expected_session_revision:details.session_revision,visit:details.visit,expected_revision:details.revision,values:Object.fromEntries(details.options.map(o=>[o.id,draft[o.id]??'']))});d.hide();frappe.show_alert('Vitals saved to encounter notes');}
                        finally{d.enable_primary_action();}
                    }});
                    const area=d.fields_dict.options.$wrapper;
                    const capture=()=>area.find('[data-vital-value]').each((_,el)=>draft[el.dataset.vitalValue]=el.value);
                    const render=()=>area.html(`<div class="opd-vital-toolbar"><span>Measurements</span><button type="button" class="opd-vital-add-btn" data-vital-add><span aria-hidden="true">+</span> Add vital</button></div><p>Options are shared within this branch. Deleting an option keeps previous notes and recorded values.</p>${details.options.length?details.options.map(o=>`<div class="opd-vital-row"><label>${esc(o.label)}${o.unit?' ('+esc(o.unit)+')':''}<input class="form-control" type="text" inputmode="decimal" data-vital-value="${esc(o.id)}" value="${esc(draft[o.id]??'')}" ${!details.visit?'disabled':''}></label><button type="button" class="opd-branch-delete-icon" data-vital-delete="${esc(o.id)}" aria-label="Delete ${esc(o.label)}" title="Delete ${esc(o.label)}">${trashIcon}</button></div>`).join(''):'<p>No vital options. Add a measurement to begin.</p>'}`);
                    area.on('click','[data-vital-add]',()=>{
                        capture();frappe.prompt([{fieldname:'label',label:'Measurement name',fieldtype:'Data',reqd:1},{fieldname:'unit',label:'Unit',fieldtype:'Data'}],async values=>{
                            const result=await this.mutate('add_vital_option',{session,...values});details.options=result.options;render();
                        },'Add vital option','Add');
                    });
                    area.on('click','[data-vital-delete]',async event=>{
                        const option=event.currentTarget.dataset.vitalDelete;
                        capture();if(!await confirm('Delete this vital option? Previously saved notes and values will remain.'))return;
                        const result=await this.mutate('delete_vital_option',{session,option});details.options=result.options;delete draft[option];render();
                    });
                    render();d.show();return;
                }
                if(action==='visit'){await this.openVisit(button.attr('data-id'));return;}
                if(action==='print'){receipt(await read('get_visit',{clinic:this.clinic,visit:button.attr('data-id')}),this.data.rooms);return;}
                if(action==='back-rooms'){this.selectedRoom=null;if(this.host)frappe.set_route('doctor-clinical');this.renderRoom();return;}
                if(action==='open-room'){
                    const roomId=button.attr('data-room');this.selectedRoom=roomId;this.section='room';
                    if(this.host)frappe.set_route('doctor-clinical','room',roomId);
                    this.renderRoom();
                    return;
                }
                if(action==='occupy'){
                    const room=this.data.rooms.find(r=>r.name===button.attr('data-room'));
                    const doctors=[...new Set(room.assignments.map(a=>a.practitioner).filter(Boolean))];
                    const open=practitioner=>this.mutate('start_session',{room:room.name,practitioner});
                    if(room.purpose==='Doctor Consultation' && doctors.length>1){frappe.prompt([{fieldname:'doctor',label:'Doctor seeing patients',fieldtype:'Select',options:doctors,reqd:1}],values=>open(values.doctor),'Open consultation room','Open OPD');return;}
                    await open(room.purpose==='Doctor Consultation'?doctors[0]:null);return;
                }
                if(button.attr('data-session')){await this.roomAction(action,button.attr('data-session'));return;}
                if(action==='manage-branches'||action==='delete-branch'){
                    const rows=await read('list_branches');
                    const d=new frappe.ui.Dialog({title:action==='delete-branch'?'Delete branch':'Manage branches',fields:[{fieldname:'branches',fieldtype:'HTML'}],primary_action_label:'Add branch',primary_action:()=>{d.hide();this.editBranch(false);}});
                    d.fields_dict.branches.$wrapper.html(`<table class="opd-table"><thead><tr><th>Branch</th><th>Status</th><th></th></tr></thead><tbody>${rows.map((r,i)=>`<tr><td>${esc(r.clinic_name)}</td><td>${r.enabled?'Active':'Inactive'}</td><td><button class="opd-btn opd-branch-delete-icon" data-delete-branch="${i}" aria-label="Delete ${esc(r.clinic_name)}" title="Delete ${esc(r.clinic_name)}">${trashIcon}</button></td></tr>`).join('')}</tbody></table><p>Branches with patient history must be deactivated in Branch settings.</p>`);
                    d.fields_dict.branches.$wrapper.on('click','[data-delete-branch]',event=>{
                        const row=rows[Number(event.currentTarget.dataset.deleteBranch)];
                        if(row.name===this.clinic){frappe.msgprint('Select another branch before deleting this branch.');return;}
                        frappe.prompt([reasonField],async values=>{
                            if(!await confirm(`Permanently delete empty branch ${esc(row.clinic_name)}?`))return;
                            await this.mutate('delete_branch',{branch:row.name,expected_revision:row.revision,reason:values.reason});
                            d.hide();this.boot=await read('bootstrap');this.shell();await this.refresh();frappe.show_alert('Branch deleted');
                        },'Delete '+row.clinic_name,'Delete branch');
                    });d.show();return;
                }
                if(action==='add-branch'||action==='edit-branch'){await this.editBranch(action==='edit-branch');return;}
                if(action==='branch-summary'){
                    const rows=await read('branch_summary',{date:this.queueDate||frappe.datetime.get_today()});
                    const d=new frappe.ui.Dialog({title:'Branch summary - '+(this.queueDate||frappe.datetime.get_today()),size:'large',fields:[{fieldname:'summary',fieldtype:'HTML'}]});
                    d.fields_dict.summary.$wrapper.html(`<table class="opd-table"><thead><tr><th>Branch</th><th>Total</th><th>Waiting</th><th>Called</th><th>In progress</th><th>On hold</th><th>Completed</th><th>Withdrawn</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.clinic_name)}</td>${['Total','Waiting','Called','In Progress','On Hold','Completed','Withdrawn'].map(k=>`<td>${r[k]}</td>`).join('')}</tr>`).join('')}</tbody></table>`);d.show();return;
                }
                if(action==='add-purpose'||action==='edit-purpose'){this.editPurpose(action==='add-purpose'?null:Number(button.attr('data-index')));return;}
                if(action==='delete-purpose'){
                    const index=Number(button.attr('data-index')),p=this.setup.purposes[index];
                    if(this.setup.rooms.some(r=>(r.purpose_id||r.purpose)===p.id)){frappe.msgprint('This purpose is assigned to a room. Change the room purpose and save before deleting it.');return;}
                    if(await confirm(`Delete purpose ${esc(p.name)}?`)){const next=structuredClone(this.setup);next.purposes.splice(index,1);await this.saveSetup(next);}return;
                }
                if(action==='delete-room'||action==='delete-route'){
                    const index=Number(button.attr('data-index')),isRoom=action==='delete-room';
                    const record=isRoom?this.setup.rooms[index]:this.setup.routes[index];
                    const label=isRoom?`OPD ${record.room_number}`:`department route ${record.department}`;
                    if(await confirm(`Delete ${esc(label)}? ${isRoom?'Rooms used by routes or active visits cannot be deleted.':'This removes the OPD route only; the department record and visit history are kept.'}`)){
                        const next=structuredClone(this.setup);
                        (isRoom?next.rooms:next.routes).splice(index,1);
                        await this.saveSetup(next);
                    }return;
                }
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
