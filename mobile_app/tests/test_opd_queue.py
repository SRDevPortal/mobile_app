"""Transactional integration tests; run against an initialized local Frappe site."""
import unittest
from unittest.mock import patch
import frappe
from mobile_app.api import opd_queue as api
from mobile_app import opd_store as store

class TestOPDQueue(unittest.TestCase):
    def setUp(self):
        self.original_user = frappe.session.user
        frappe.set_user("Administrator")
        self.suffix = frappe.generate_hash(length=10)
        self.roles = {"Administrator": ["System Manager"], "Guest": []}
        self.role_patch = patch.object(frappe, "get_roles", side_effect=lambda user=None: self.roles.get(user or frappe.session.user, []))
        self.role_patch.start()
        self.departments = []
        self.doctors = []
        for i in range(2):
            dep = "OPD Test Department " + str(i) + self.suffix
            frappe.get_doc({"doctype":"Medical Department", "name":dep, "department":dep}).db_insert()
            self.departments.append(dep)
            doctor = "OPD Test Doctor " + str(i) + self.suffix
            frappe.get_doc({"doctype":"Healthcare Practitioner", "name":doctor, "first_name":doctor, "practitioner_name":doctor, "status":"Active", "department":dep}).db_insert()
            self.doctors.append(doctor)
        self.clinic = "OPD-test-" + self.suffix
        frappe.flags.in_opd_mutation=True
        store.lock()
        store.save(store.Record("Clinic", {"name":self.clinic,"clinic_name":self.clinic,"enabled":1}))
        config=store.configuration();config['default_clinic']=self.clinic;store.write_configuration(config)
        frappe.flags.in_opd_mutation=False
        rooms = []
        for number,purpose,doctor in [(1,api.STAGES[0],None),(3,api.STAGES[1],None),(4,api.STAGES[1],None),(5,api.STAGES[2],self.doctors[1]),(6,api.STAGES[2],self.doctors[0])]:
            rooms.append({"client_id":str(number),"room_number":str(number),"purpose":purpose,"enabled":1,"assignments":[{"user":"Administrator","practitioner":doctor}]})
        routes = [{"department":self.departments[0],"vitals_room":"1","history_room":"4","consultation_room":"6"}, {"department":self.departments[1],"vitals_room":"1","history_room":"3","consultation_room":"5"}]
        api.save_setup(self.clinic,self.req(),0,rooms,routes)
        self.setup = api.get_setup(self.clinic)
        self.rooms = {r['room_number']:r['name'] for r in self.setup['rooms']}

    def tearDown(self):
        frappe.db.rollback()
        self.role_patch.stop()
        frappe.set_user(self.original_user)

    def req(self): return frappe.generate_hash(length=20)
    def visit(self,index=0,request=None):
        patient="OPD Test Patient " + self.req()
        frappe.get_doc({"doctype":"Patient","name":patient,"first_name":patient,"patient_name":patient}).db_insert()
        return api.check_in(self.clinic,request or self.req(),self.departments[index],self.doctors[index],patient=patient)
    def act(self,session,action,**kwargs):
        revision=api._value('Session',session,'revision')
        return api.room_action(self.clinic,self.req(),session,revision,action,**kwargs)
    def open_room(self,number,index=None):
        return api.start_session(self.clinic,self.req(),self.rooms[str(number)],self.doctors[index] if index is not None else None)['session']
    def stage(self,visit): return api.get_visit(self.clinic,visit['name'])

    def test_shared_queue_and_complete_journey(self):
        kidney,skin=self.visit(),self.visit(1)
        self.assertEqual((kidney['token'],skin['token']),('001','002'))
        session=self.open_room(1)
        self.assertEqual(self.act(session,'call_next')['visit'],kidney['name'])
        calls=api.display_snapshot(self.clinic)['calls']; self.assertEqual(len(calls),1)
        old=calls[0]['call_id']; self.act(session,'recall')
        self.assertGreater(api.display_snapshot(self.clinic)['calls'][0]['call_id'],old)
        self.act(session,'start'); self.assertEqual(api.display_snapshot(self.clinic)['calls'],[])
        self.act(session,'complete')
        self.assertEqual(self.stage(kidney)['stages'][1]['room'],self.rooms['4'])
        self.assertEqual(self.act(session,'call_next')['visit'],skin['name'])
        self.act(session,'start'); self.act(session,'complete'); self.act(session,'end')
        self.assertEqual(self.stage(skin)['stages'][1]['room'],self.rooms['3'])
        for room,index in [(4,None),(6,0)]:
            session=self.open_room(room,index); self.act(session,'call_next'); self.act(session,'start'); self.act(session,'complete'); self.act(session,'end')
        self.assertEqual(self.stage(kidney)['status'],'Completed')
        self.assertTrue(all(s['state']=='Completed' for s in self.stage(kidney)['stages']))

    def test_idempotency_and_stale_versions(self):
        visit=self.visit(); args=dict(clinic=self.clinic,request_id=self.req(),room=self.rooms['1'])
        a=api.start_session(**args); b=api.start_session(**args); self.assertEqual(a,b)
        with self.assertRaises(frappe.ValidationError): api.start_session(**dict(args,room=self.rooms['4']))
        self.act(a['session'],'call_next')
        with self.assertRaises(frappe.TimestampMismatchError): api.room_action(self.clinic,self.req(),a['session'],a['revision'],'start')
        self.assertEqual(self.stage(visit)['stages'][0]['state'],'Called')

    def test_absent_priority_and_requeue(self):
        a,b=self.visit(),self.visit(1); session=self.open_room(1)
        self.act(session,'call_next'); self.act(session,'absent')
        a=self.stage(a); self.assertEqual(a['stages'][0]['state'],'On Hold')
        a=api.visit_action(self.clinic,self.req(),a['name'],a['revision'],'requeue','Patient returned')
        self.assertEqual(self.act(session,'call_next')['visit'],b['name'])
        self.act(session,'absent')
        self.assertEqual(self.act(session,'call_next')['visit'],a['name'])
        with self.assertRaises(frappe.ValidationError): api.visit_action(self.clinic,self.req(),a['name'],self.stage(a)['revision'],'withdraw','Leaving')

    def test_doctor_mismatch_and_atomic_setup(self):
        with self.assertRaises(frappe.ValidationError): api.check_in(self.clinic,self.req(),self.departments[0],self.doctors[1],patient='unused')
        self.assertEqual(len(store.records('Visit',{'clinic':self.clinic})),0)
        setup=api.get_setup(self.clinic); setup['rooms'][0]['room_label']='Must rollback'; setup['routes'][0]['vitals_room']=self.rooms['4']
        with self.assertRaises(frappe.ValidationError): api.save_setup(self.clinic,self.req(),setup['config_version'],setup['rooms'],setup['routes'])
        self.assertFalse(api._value('Room',setup['rooms'][0]['name'],'room_label'))

    def test_routes_are_snapshotted_and_apply_is_explicit(self):
        old=self.visit(); queued=old['stages'][0]['queued_at']
        setup=api.get_setup(self.clinic)
        next(r for r in setup['routes'] if r['department']==self.departments[0])['history_room']=self.rooms['3']
        api.save_setup(self.clinic,self.req(),setup['config_version'],setup['rooms'],setup['routes'])
        self.assertEqual(self.stage(old)['stages'][1]['room'],self.rooms['4'])
        self.assertEqual(self.visit()['stages'][1]['room'],self.rooms['3'])
        preview=api.preview_reroute(self.clinic)
        api.apply_routes(self.clinic,self.req(),preview['config_version'],[{'visit':old['name'],'revision':old['revision']}],'Room moved')
        current=self.stage(old); self.assertEqual(current['token'],old['token']); self.assertEqual(current['stages'][0]['queued_at'],queued)
        self.assertEqual(current['stages'][1]['room'],self.rooms['3'])
        session=self.open_room(1); self.act(session,'call_next'); current=self.stage(old)
        with self.assertRaises(frappe.ValidationError): api.visit_action(self.clinic,self.req(),old['name'],current['revision'],'reroute','Department change',self.departments[1],self.doctors[1])

    def test_permissions_and_direct_write_protection(self):
        with self.assertRaises(frappe.PermissionError): api._doc('Clinic',self.clinic).save(ignore_permissions=True)
        frappe.set_user('Guest')
        for fn in (api.get_setup,api.snapshot,api.display_snapshot):
            with self.assertRaises(frappe.PermissionError): fn(self.clinic)

    def test_busy_room_assignment_cannot_change(self):
        self.visit(); session=self.open_room(1); self.act(session,'call_next')
        setup=api.get_setup(self.clinic); next(r for r in setup['rooms'] if r['room_number']=='1')['enabled']=0
        with self.assertRaises(frappe.ValidationError): api.save_setup(self.clinic,self.req(),setup['config_version'],setup['rooms'],setup['routes'])
        self.assertEqual(api._value('Room',self.rooms['1'],'enabled'),1)

    def test_linked_booking_single_visit_and_completion(self):
        from mobile_app.api import appointment_calendar as cal
        patient='opd-patient-'+self.suffix
        encounter='opd-encounter-'+self.suffix
        booking='opd-booking-'+self.suffix
        mobile='opd-mobile-'+self.suffix
        frappe.get_doc({'doctype':'Patient','name':patient,'patient_name':'OPD integration patient'}).db_insert()
        frappe.get_doc({'doctype':'Patient Encounter','name':encounter,'patient':patient,
            'practitioner':self.doctors[0],'sr_encounter_type':'Appointment','sr_encounter_place':'OPD',
            'sr_encounter_status':'Draft','encounter_reference':booking}).db_insert()
        frappe.get_doc({'doctype':'Clinic Appointment','name':booking,'encounter_reference':encounter,'appointment_status':'Draft'}).db_insert()
        frappe.get_doc({'doctype':'Mobile App Appointment','name':mobile,'patient_encounter':encounter,'status':'Booked'}).db_insert()
        cal.update_appointment('Patient Encounter',encounter,'approve','Pending')
        args=dict(clinic=self.clinic,request_id=self.req(),department=self.departments[0],doctor=self.doctors[0],source_doctype='Mobile App Appointment',source_name=mobile)
        visit=api.check_in(**args)
        self.assertEqual(frappe.as_json(api.check_in(**args)),frappe.as_json(visit))
        repeat=api.check_in(**dict(args,request_id=self.req(),source_doctype='Patient Encounter',source_name=encounter))
        self.assertEqual(visit['name'],repeat['name'])
        self.assertEqual(frappe.db.get_value('Clinic Appointment',booking,'appointment_status'),'Confirmed')
        doc=frappe.get_doc('Clinic Appointment',booking); doc.appointment_status='Completed'
        api.preserve_clinic_progress(doc); self.assertEqual(doc.appointment_status,'Confirmed')
        for number,doctor in [(1,None),(4,None),(6,0)]:
            session=self.open_room(number,doctor)
            self.act(session,'call_next');self.act(session,'start');self.act(session,'complete');self.act(session,'end')
        self.assertEqual(frappe.db.get_value('Clinic Appointment',booking,'appointment_status'),'Completed')
        self.assertEqual(len(store.records('Visit',{'clinic':self.clinic})),1)

    def user(self, role):
        user=role.replace(' ','-').lower()+'-'+self.req()+'@example.invalid'
        frappe.get_doc({'doctype':'User','name':user,'email':user,'first_name':'OPD Test','enabled':1,'user_type':'System User'}).db_insert()
        self.roles[user]=[role]
        return user

    def test_display_restricted_and_opd_staff_have_full_controls(self):
        display=self.user('OPD Display');staff=self.user('OPD Staff')
        setup=api.get_setup(self.clinic)
        next(r for r in setup['rooms'] if r['room_number']=='1')['assignments'].append({'user':staff,'practitioner':None})
        api.save_setup(self.clinic,self.req(),setup['config_version'],setup['rooms'],setup['routes'],[display])
        self.visit();session=self.open_room(1);self.act(session,'call_next')
        frappe.set_user(display)
        snapshot=api.display_snapshot(self.clinic)
        self.assertEqual(len(snapshot['calls']),1)
        self.assertEqual(set(snapshot['calls'][0]),{'call_id','token','visit_date','patient_name','room','stage'})
        for fn in [api.get_setup,api.snapshot]:
            with self.assertRaises(frappe.PermissionError):fn(self.clinic)
        frappe.set_user(staff)
        self.assertEqual(len(api.snapshot(self.clinic)['rooms']),5)
        self.act(session,'start')
        self.assertTrue(api.bootstrap()['manager'])
        self.assertTrue(api.get_setup(self.clinic)['departments'])
        self.act(session,'complete')
        current=api.get_setup(self.clinic)
        api.save_setup(self.clinic,self.req(),current['config_version'],current['rooms'],current['routes'])

    def test_priority_precedes_arrival_and_walkin_retry_returns_token(self):
        first,second=self.visit(),self.visit(1)
        second=api.visit_action(self.clinic,self.req(),second['name'],second['revision'],'priority','Reception triage',priority=1)
        retry=api.check_in(self.clinic,self.req(),self.departments[1],self.doctors[1],patient=second['patient'])
        self.assertEqual(retry['name'],second['name'])
        session=self.open_room(1)
        self.assertEqual(self.act(session,'call_next')['visit'],second['name'])

    def test_consultation_room_filters_by_doctor(self):
        other=self.user('OPD Staff');setup=api.get_setup(self.clinic)
        room5=next(r for r in setup['rooms'] if r['room_number']=='5')
        room5['assignments']=[{'user':other,'practitioner':self.doctors[1]},{'user':'Administrator','practitioner':self.doctors[0]}]
        room6=next(r for r in setup['rooms'] if r['room_number']=='6');room6['enabled']=0;room6['assignments']=[]
        for route in setup['routes']:route['consultation_room']=self.rooms['5'];route['history_room']=self.rooms['3']
        api.save_setup(self.clinic,self.req(),setup['config_version'],setup['rooms'],setup['routes'])
        skin,kidney=self.visit(1),self.visit(0)
        for room in [1,3]:
            session=self.open_room(room)
            for visit in [skin,kidney]:
                self.assertEqual(self.act(session,'call_next')['visit'],visit['name'])
                self.act(session,'start');self.act(session,'complete')
            self.act(session,'end')
        session=self.open_room(5,0)
        self.assertEqual(self.act(session,'call_next')['visit'],kidney['name'])
        self.assertEqual(self.stage(skin)['stages'][2]['state'],'Waiting')

    def test_reroute_preserves_completed_stages(self):
        visit=self.visit();session=self.open_room(1)
        self.act(session,'call_next');self.act(session,'start');self.act(session,'complete');self.act(session,'end')
        before=self.stage(visit)
        after=api.visit_action(self.clinic,self.req(),visit['name'],before['revision'],'reroute','Department corrected',self.departments[1],self.doctors[1])
        self.assertEqual(before['stages'][0],after['stages'][0])
        self.assertEqual(before['stages'][1]['queued_at'],after['stages'][1]['queued_at'])
        self.assertEqual(after['stages'][1]['room'],self.rooms['3'])
        self.assertEqual(before['token'],after['token'])

    def test_new_walkin_patient_uses_existing_patient_validation(self):
        gender=frappe.db.get_value('Gender',{},'name')
        visit=api.check_in(self.clinic,self.req(),self.departments[0],self.doctors[0],patient_details={'first_name':'OPD Walkin Test '+self.suffix,'sex':gender,'mobile':'7700'+str(int(self.suffix,16))[-6:].zfill(6)})
        self.assertTrue(frappe.db.exists('Patient',visit['patient']))
        self.assertEqual(visit['token'],'001')

    def test_existing_records_only_and_direct_payload_edit_is_denied(self):
        self.assertFalse(frappe.get_all('DocType',filters={'name':['like','OPD %']},pluck='name'))
        visit=self.visit()
        workflow=frappe.db.get_value(store.WORKFLOW,{'opd_visit_id':visit['name']},'name')
        self.assertTrue(workflow)
        doc=frappe.get_doc(store.WORKFLOW,workflow);doc.opd_status='Completed'
        with self.assertRaises(frappe.PermissionError):doc.save(ignore_permissions=True)
        self.assertEqual(store.value('Visit',visit['name'],'status'),'Active')
        settings=frappe.get_doc(store.SETTINGS);settings.set(store.FIELD,'{}')
        store.protect_settings(settings)
        self.assertIn(self.clinic,frappe.parse_json(settings.get(store.FIELD))['clinics'])

    def test_setup_lists_departments_and_genders_without_filters(self):
        setup=api.get_setup(self.clinic)
        self.assertTrue(set(self.departments).issubset(set(setup['departments'])))
        self.assertEqual(set(setup['departments']),set(frappe.get_all('Medical Department',pluck='name')))
        self.assertEqual(set(setup['genders']),set(frappe.get_all('Gender',pluck='name')))

    def test_staff_share_controls_without_duplicate_room_sessions(self):
        staff=self.user('OPD Staff');other=self.user('OPD Staff')
        frappe.set_user(staff)
        session=self.open_room(1)
        self.assertEqual(api._value('Session',session,'controller'),staff)
        frappe.set_user(other)
        with self.assertRaises(frappe.ValidationError): self.open_room(1)
        self.act(session,'pause')
        frappe.set_user(staff);self.act(session,'end')
        frappe.set_user(other);self.open_room(1)

    def test_finish_and_call_next_is_atomic_and_retry_safe(self):
        first,second=self.visit(),self.visit(1)
        session=self.open_room(1);self.act(session,'call_next');self.act(session,'start')
        args=dict(clinic=self.clinic,request_id=self.req(),session=session,expected_version=api._value('Session',session,'revision'),action='complete_next')
        result=api.room_action(**args)
        self.assertEqual(api.room_action(**args),result)
        self.assertEqual(result['visit'],second['name'])
        self.assertEqual(self.stage(first)['stages'][0]['state'],'Completed')
        self.assertEqual(self.stage(first)['stages'][1]['state'],'Waiting')
        self.assertEqual(self.stage(second)['stages'][0]['state'],'Called')
        self.assertEqual(result['audit']['next_call']['affected_visit'],second['name'])
        self.assertEqual(len(api.display_snapshot(self.clinic)['calls']),1)

    def test_finish_with_empty_queue_keeps_room_open(self):
        visit=self.visit();session=self.open_room(1);self.act(session,'call_next');self.act(session,'start')
        self.act(session,'pause')
        with self.assertRaises(frappe.ValidationError):self.act(session,'complete_next')
        self.assertEqual(self.stage(visit)['stages'][0]['state'],'In Progress')
        self.act(session,'resume');result=self.act(session,'complete_next')
        self.assertIsNone(result['visit'])
        self.assertIsNone(result['audit']['next_call'])
        self.assertEqual(api._value('Session',session,'status'),'Available')
        self.assertEqual(self.stage(visit)['stages'][0]['state'],'Completed')

    def test_same_user_controls_multiple_rooms_with_active_patient(self):
        patient=self.visit()
        first=self.open_room(1)
        self.act(first,'call_next');self.act(first,'start')
        second=self.open_room(4)
        self.assertNotEqual(first,second)
        self.assertEqual(self.stage(patient)['stages'][0]['state'],'In Progress')
        self.assertEqual(api._value('Session',first,'current_visit'),patient['name'])

    def test_walkin_syncs_calendar_and_retries(self):
        from mobile_app.api import appointment_calendar as cal
        patient='walkin-patient-'+self.suffix
        frappe.get_doc({'doctype':'Patient','name':patient,'patient_name':'Walkin test'}).db_insert()
        encounter='walkin-encounter-'+self.suffix
        frappe.get_doc({'doctype':'Patient Encounter','name':encounter,'patient':patient,
            'practitioner':self.doctors[0],'sr_encounter_type':'Appointment',
            'sr_encounter_place':'OPD','pe_appointment_date':frappe.utils.today()}).db_insert()
        cal.update_appointment('Patient Encounter',encounter,'approve','Pending')
        visit=api.check_in(self.clinic,self.req(),self.departments[0],self.doctors[0],patient=patient)
        self.assertEqual(visit['source_name'],encounter)
        doc=frappe.get_doc('Patient Encounter',encounter)
        self.assertEqual(cal._status(doc,cal._workflow(doc.doctype,doc.name)),'Checked In')
        again=api.check_in(self.clinic,self.req(),self.departments[0],self.doctors[0],patient=patient)
        self.assertEqual(again['name'],visit['name'])

    def test_existing_walkin_is_linked_without_resetting_progress(self):
        from mobile_app.api import appointment_calendar as cal
        visit=self.visit()
        session=self.open_room(1)
        self.act(session,'call_next');self.act(session,'start');self.act(session,'complete')
        stages=self.stage(visit)['stages']
        encounter='legacy-walkin-'+self.suffix
        frappe.get_doc({'doctype':'Patient Encounter','name':encounter,'patient':visit['patient'],
            'practitioner':self.doctors[0],'sr_encounter_type':'Appointment',
            'sr_encounter_place':'OPD','pe_appointment_date':frappe.utils.today()}).db_insert()
        cal.update_appointment('Patient Encounter',encounter,'approve','Pending')
        args=dict(clinic=self.clinic,department=self.departments[0],doctor=self.doctors[0],
            patient=visit['patient'],source_doctype='Patient Encounter',source_name=encounter)
        linked=api.check_in(request_id=self.req(),**args)
        self.assertEqual(linked['name'],visit['name'])
        self.assertEqual(linked['token'],visit['token'])
        self.assertEqual(linked['stages'],stages)
        self.assertEqual(linked['source_name'],encounter)
        doc=frappe.get_doc('Patient Encounter',encounter)
        self.assertEqual(cal._status(doc,cal._workflow(doc.doctype,doc.name)),'Checked In')
        again=api.check_in(request_id=self.req(),**args)
        self.assertEqual(again['name'],visit['name'])
        self.assertEqual(len(store.records('Visit',{'clinic':self.clinic})),1)

    def test_display_missing_clinic_resolves_only_authorized_single_clinic(self):
        with patch.object(api, 'bootstrap', return_value={'clinics':[{'name':self.clinic}]}):
            self.assertEqual(api.display_snapshot(),api.display_snapshot(self.clinic))
        for clinics in [[],[{'name':self.clinic},{'name':'another'}]]:
            with patch.object(api, 'bootstrap', return_value={'clinics':clinics}):
                with self.assertRaises(frappe.PermissionError):api.display_snapshot()
        frappe.set_user('Guest')
        with self.assertRaises(frappe.PermissionError):api.display_snapshot()

    def test_end_day_preserves_history_and_retry(self):
        visit=self.visit();session=self.open_room(1)
        day=frappe.utils.today()
        before=api.daily_visits(self.clinic,day)
        self.assertEqual(before['counts']['Waiting'],1)
        preview=api.preview_end_day(self.clinic,day)
        args=dict(clinic=self.clinic,request_id=self.req(),date=day,
            expected_sequence=preview['sequence'],reason='Clinic closed',close_visits=1)
        result=api.end_day(**args)
        self.assertEqual(result,api.end_day(**args))
        self.assertEqual(result['closed_visits'],[visit['name']])
        self.assertFalse(store.records('Session',{'clinic':self.clinic}))
        after=api.daily_visits(self.clinic,day)
        self.assertEqual(after['counts']['Withdrawn'],1)
        self.assertEqual(after['visits'][0]['token'],visit['token'])

    def test_end_day_releases_claimed_visits_and_rejects_stale_preview(self):
        visit=self.visit();session=self.open_room(1)
        preview=api.preview_end_day(self.clinic,frappe.utils.today())
        self.act(session,'call_next')
        with self.assertRaises(frappe.TimestampMismatchError):
            api.end_day(self.clinic,self.req(),preview['date'],preview['sequence'],'Closing',1)
        preview=api.preview_end_day(self.clinic,preview['date'])
        result=api.end_day(self.clinic,self.req(),preview['date'],preview['sequence'],'Closing',1)
        self.assertEqual(self.stage(visit)['status'],'Withdrawn')
        self.assertEqual(result['released_patients'][0]['previous_state'],'Called')
        self.assertFalse(store.records('Session',{'clinic':self.clinic}))
        self.assertEqual(api.display_snapshot(self.clinic)['calls'],[])

    def test_daily_history_date_and_guest_access(self):
        visit=self.visit()
        self.assertEqual(api.daily_visits(self.clinic,frappe.utils.add_days(frappe.utils.today(),-1))['counts']['Total'],0)
        frappe.set_user('Guest')
        with self.assertRaises(frappe.PermissionError):api.daily_visits(self.clinic)
        with self.assertRaises(frappe.PermissionError):api.preview_end_day(self.clinic,frappe.utils.today())

    def test_end_day_releases_in_progress_without_completing_treatment(self):
        visit=self.visit();session=self.open_room(1)
        self.act(session,'call_next');self.act(session,'start')
        preview=api.preview_end_day(self.clinic,frappe.utils.today())
        result=api.end_day(self.clinic,self.req(),preview['date'],preview['sequence'],'Clinic closed',1)
        self.assertEqual(result['released_patients'][0]['previous_state'],'In Progress')
        current=self.stage(visit)
        self.assertEqual(current['status'],'Withdrawn')
        self.assertFalse(current['stages'][0]['completed_at'])
        self.assertEqual(current['stages'][1]['state'],'Not Ready')

    def test_room_purpose_create_rename_delete_and_in_use_guard(self):
        setup=api.get_setup(self.clinic)
        purposes=setup['purposes']+[{'id':'triage','name':'Triage','stage':'Vitals'}]
        api.save_setup(self.clinic,self.req(),setup['config_version'],setup['rooms'],setup['routes'],purposes=purposes)
        setup=api.get_setup(self.clinic)
        purposes[-1]['name']='Initial assessment'
        api.save_setup(self.clinic,self.req(),setup['config_version'],setup['rooms'],setup['routes'],purposes=purposes)
        setup=api.get_setup(self.clinic)
        self.assertEqual(setup['purposes'][-1]['name'],'Initial assessment')
        api.save_setup(self.clinic,self.req(),setup['config_version'],setup['rooms'],setup['routes'],purposes=purposes[:-1])
        setup=api.get_setup(self.clinic)
        with self.assertRaises(frappe.ValidationError):
            api.save_setup(self.clinic,self.req(),setup['config_version'],setup['rooms'],setup['routes'],purposes=setup['purposes'][1:])

    def test_custom_room_purpose_keeps_routing_stage(self):
        setup=api.get_setup(self.clinic)
        purposes=setup['purposes']+[{'id':'triage','name':'Triage','stage':'Vitals'}]
        next(r for r in setup['rooms'] if r['room_number']=='1')['purpose_id']='triage'
        for route in setup['routes']:
            route['vitals_room']=None
            route['steps'][0]['purpose_id']='triage'
        api.save_setup(self.clinic,self.req(),setup['config_version'],setup['rooms'],setup['routes'],purposes=purposes)
        room=next(r for r in api.get_setup(self.clinic)['rooms'] if r['room_number']=='1')
        self.assertEqual(room['purpose_name'],'Triage')
        self.assertEqual(room['purpose'],'Vitals')
        self.assertEqual(next(s['room'] for s in self.visit()['stages'] if s['purpose_id']=='triage'),room['name'])

    def test_added_purpose_is_a_real_fourth_step_and_old_visit_stays_saved(self):
        old=self.visit()
        setup=api.get_setup(self.clinic)
        purposes=setup['purposes']+[{'id':'lab','name':'Lab Test','stage':'Vitals'}]
        setup['rooms'].append({'client_id':'lab-room','room_number':'7','purpose_id':'lab','purpose':'Vitals','enabled':1,'assignments':[]})
        for route in setup['routes']:
            route['steps'].append({'purpose_id':'lab','stage':'Lab Test','room':'lab-room'})
        api.save_setup(self.clinic,self.req(),setup['config_version'],setup['rooms'],setup['routes'],purposes=purposes)
        visit=self.visit()
        self.assertEqual([s['stage'] for s in visit['stages']],['Vitals','Medical History','Lab Test','Doctor Consultation'])
        self.assertEqual(len(self.stage(old)['stages']),3)
        # Withdraw the older unclaimed visit so the new visit is next in each queue.
        api.visit_action(self.clinic,self.req(),old['name'],self.stage(old)['revision'],'withdraw','Test isolates new route')
        rooms={r['name']:r for r in api.get_setup(self.clinic)['rooms']}
        for i,stage in enumerate(visit['stages']):
            room=rooms[stage['room']]
            session=api.start_session(self.clinic,self.req(),room['name'],self.doctors[0] if room['purpose']=='Doctor Consultation' else None)['session']
            self.act(session,'call_next');self.act(session,'start');self.act(session,'complete')
            self.assertEqual(self.stage(visit)['status'],'Completed' if i==3 else 'Active')
            self.act(session,'end')

    def test_delete_unused_room_and_department_route(self):
        setup=api.get_setup(self.clinic)
        # Remove a route and its exclusive rooms together.
        remaining=[r for r in setup['routes'] if r['department']==self.departments[0]]
        rooms=[r for r in setup['rooms'] if r['room_number'] not in ('3','5')]
        api.save_setup(self.clinic,self.req(),setup['config_version'],rooms,remaining)
        after=api.get_setup(self.clinic)
        self.assertEqual(len(after['routes']),1)
        self.assertEqual(len(after['rooms']),3)
        self.assertTrue(frappe.db.exists('Medical Department',self.departments[1]))

    def test_cannot_delete_room_in_route_or_department_with_active_visit(self):
        setup=api.get_setup(self.clinic)
        rooms=[r for r in setup['rooms'] if r['room_number']!='1']
        with self.assertRaises(frappe.ValidationError):
            api.save_setup(self.clinic,self.req(),setup['config_version'],rooms,setup['routes'])
        self.visit()
        with self.assertRaises(frappe.ValidationError):
            api.save_setup(self.clinic,self.req(),setup['config_version'],setup['rooms'],[])
        self.assertEqual(len(api.get_setup(self.clinic)['routes']),2)

    def test_branch_copy_and_access_isolation(self):
        staff=self.user('OPD Staff')
        result=api.save_branch(self.clinic,self.req(),'Noida '+self.suffix,'N'+self.suffix[:5],members=[staff],copy_setup=1)
        branch=result['branch']
        copied=api.get_setup(branch)
        self.assertEqual(len(copied['rooms']),5)
        self.assertTrue(all(r['name'] not in self.rooms.values() for r in copied['rooms']))
        self.assertEqual(api.daily_visits(branch)['counts']['Total'],0)
        other=self.user('OPD Staff')
        frappe.set_user(other)
        with self.assertRaises(frappe.PermissionError):api.snapshot(branch)
        frappe.set_user(staff)
        self.assertEqual(len(api.snapshot(branch)['rooms']),5)
        with self.assertRaises(frappe.PermissionError):api.save_branch(branch,self.req(),'Forbidden','NO',members=[])
        frappe.set_user('Administrator')
        self.open_room(6,0)
        consult=next(r for r in copied['rooms'] if r['room_number']=='6')
        with self.assertRaises(frappe.ValidationError):api.start_session(branch,self.req(),consult['name'],self.doctors[0])

    def test_branch_prefix_and_end_day_isolation(self):
        result=api.save_branch(self.clinic,self.req(),'Noida '+self.suffix,'N'+self.suffix[:5],members=[],copy_setup=1)
        branch=result['branch'];first=self.visit()
        patient='branch-patient-'+self.suffix
        frappe.get_doc({'doctype':'Patient','name':patient,'patient_name':patient}).db_insert()
        visit=api.check_in(branch,self.req(),self.departments[0],self.doctors[0],patient=patient)
        self.assertTrue(visit['token'].endswith('-001'))
        preview=api.preview_end_day(branch,frappe.utils.today())
        api.end_day(branch,self.req(),preview['date'],preview['sequence'],'Closed branch',1)
        self.assertEqual(self.stage(first)['status'],'Active')

    def test_appointment_branch_binding_and_wrong_branch_checkin(self):
        from mobile_app.api import appointment_calendar as cal
        branch=api.save_branch(self.clinic,self.req(),'Noida '+self.suffix,'N'+self.suffix[:5],members=[],copy_setup=1)['branch']
        patient='branch-booking-patient-'+self.suffix
        frappe.get_doc({'doctype':'Patient','name':patient,'patient_name':patient}).db_insert()
        encounter='branch-booking-'+self.suffix
        frappe.get_doc({'doctype':'Patient Encounter','name':encounter,'patient':patient,'practitioner':self.doctors[0],
            'sr_encounter_type':'Appointment','sr_encounter_place':'OPD','pe_appointment_date':frappe.utils.today()}).db_insert()
        cal.update_appointment('Patient Encounter',encounter,'approve','Pending')
        api.assign_appointment_branch(branch,self.req(),'Patient Encounter',encounter,'Patient chose Noida')
        self.assertEqual(api.appointment_branch('Patient Encounter',encounter),branch)
        with self.assertRaises(frappe.ValidationError):
            api.check_in(self.clinic,self.req(),self.departments[0],self.doctors[0],source_doctype='Patient Encounter',source_name=encounter)
        result=api.check_in(branch,self.req(),self.departments[0],self.doctors[0],source_doctype='Patient Encounter',source_name=encounter)
        self.assertEqual(result['clinic'],branch)

    def test_delete_empty_branch_and_protect_configured_branch(self):
        branch=api.save_branch(self.clinic,self.req(),'Empty '+self.suffix,'E'+self.suffix[:5],members=[])['branch']
        revision=api.branch_details(branch)['revision']
        args=dict(clinic=self.clinic,request_id=self.req(),branch=branch,expected_revision=revision,reason='Test branch no longer needed')
        self.assertEqual(api.delete_branch(**args),api.delete_branch(**args))
        self.assertFalse(store.records('Clinic',{'name':branch}))
        other=api.save_branch(self.clinic,self.req(),'Configured '+self.suffix,'C'+self.suffix[:5],members=[],copy_setup=1)['branch']
        with self.assertRaises(frappe.ValidationError):
            api.delete_branch(self.clinic,self.req(),other,api.branch_details(other)['revision'],'Remove')
        staff=self.user('OPD Staff');frappe.set_user(staff)
        with self.assertRaises(frappe.PermissionError):api.list_branches()

    def test_readonly_patient_view_and_record_tabs(self):
        visit=self.visit()
        result=api.patient_overview(self.clinic,visit['name'])
        self.assertEqual(result['patient']['name'],visit['patient'])
        self.assertTrue(all(f['type'] not in ('Password','HTML','Button') for f in result['patient']['fields']))
        for tab in ('invoice','prescription','history','dispatch'):
            result=api.patient_records(self.clinic,visit['name'],tab)
            self.assertIn('records',result)
        self.assertEqual(self.stage(visit)['revision'],visit['revision'])
        with self.assertRaises(frappe.ValidationError):api.patient_records(self.clinic,visit['name'],'anything')
        frappe.set_user('Guest')
        with self.assertRaises(frappe.PermissionError):api.patient_overview(self.clinic,visit['name'])

    def test_patient_view_respects_patient_read_permission(self):
        visit=self.visit()
        staff=self.user('OPD Staff');frappe.set_user(staff)
        with patch.object(frappe,'has_permission',return_value=False):
            with self.assertRaises(frappe.PermissionError):api.patient_overview(self.clinic,visit['name'])

    def test_room_vitals_append_encounter_notes_and_safe_retry(self):
        visit=self.visit();session=self.open_room(1)
        self.act(session,'call_next');self.act(session,'start')
        options=api.add_vital_option(self.clinic,self.req(),session,'Temperature','C')['options']
        custom=options[-1]['id']
        context=api.room_vitals(self.clinic,session)
        args=dict(clinic=self.clinic,request_id=self.req(),session=session,expected_session_revision=context['session_revision'],visit=visit['name'],expected_revision=context['revision'],values={'pulse':72,custom:36.5})
        result=api.update_room_vitals(**args)
        notes=frappe.db.get_value('Patient Encounter',result['encounter'],'sr_notes')
        self.assertIn('Temperature: 36.5 C',notes)
        self.assertEqual(result,api.update_room_vitals(**args))
        self.assertEqual(notes,frappe.db.get_value('Patient Encounter',result['encounter'],'sr_notes'))
        summary=api.patient_overview(self.clinic,visit['name'])
        self.assertTrue(any(f['label']=='Heart Rate / Pulse' and f['value']==72 for f in summary['vitals']['fields']))
        context=api.room_vitals(self.clinic,session)
        api.update_room_vitals(self.clinic,self.req(),session,context['session_revision'],visit['name'],context['revision'],{'pulse':74})
        self.assertIn(notes,frappe.db.get_value('Patient Encounter',result['encounter'],'sr_notes'))

    def test_room_vitals_require_started_patient(self):
        visit=self.visit();session=self.open_room(1);self.act(session,'call_next')
        context=api.room_vitals(self.clinic,session)
        with self.assertRaises(frappe.ValidationError):
            api.update_room_vitals(self.clinic,self.req(),session,context['session_revision'],visit['name'],context['revision'],{'pulse':72})

    def test_delete_vital_options_stays_empty_and_preserves_notes(self):
        visit=self.visit();session=self.open_room(1)
        self.act(session,'call_next');self.act(session,'start')
        context=api.room_vitals(self.clinic,session)
        result=api.update_room_vitals(self.clinic,self.req(),session,context['session_revision'],visit['name'],context['revision'],{'pulse':72})
        notes=frappe.db.get_value('Patient Encounter',result['encounter'],'sr_notes')
        for option in context['options']:
            api.delete_vital_option(self.clinic,self.req(),session,option['id'])
        self.assertEqual(api.room_vitals(self.clinic,session)['options'],[])
        self.assertEqual(frappe.db.get_value('Patient Encounter',result['encounter'],'sr_notes'),notes)
