"""Doctor Clinical OPD operations backed by existing settings and workflow records."""
import functools
import hashlib
import inspect
import json

import frappe
from mobile_app import opd_store as store
from frappe.utils import cint, getdate, now_datetime

STAGES = ("Vitals", "Medical History", "Doctor Consultation")
ROOM_FIELDS = ("vitals_room", "history_room", "consultation_room")
MANAGERS = {"System Manager", "Appointment Manager"}
STAFF = {"OPD Staff", "Appointment Receptionist", "Physician", "Healthcare Practitioner", "Mobile App Doctor"}


def _doc(*args, **kwargs):
    if args and isinstance(args[0], dict) and args[0].get('doctype') in store.KINDS:
        data=dict(args[0]);kind=data.pop('doctype');return store.Record(kind,data)
    if args and isinstance(args[0], str) and args[0] in store.KINDS: return store.get(args[0],args[1])
    if len(args) > 1 and isinstance(args[0], str):
        kwargs['for_update'] = bool(frappe.flags.in_opd_mutation)
    return frappe.get_doc(*args, **kwargs)


def _value(*args, **kwargs):
    if args[0] in store.KINDS: return store.value(*args, **kwargs)
    kwargs.setdefault('for_update', bool(frappe.flags.in_opd_mutation))
    return frappe.db.get_value(*args, **kwargs)


def _exists(doctype, filters):
    return _value(doctype, filters, 'name')


def _all(doctype, filters=None, fields=None, pluck=None, order_by=None):
    if doctype in store.KINDS: return store.all(doctype,filters,fields,pluck,order_by)
    # Current reads matter under MariaDB REPEATABLE READ after waiting for the clinic lock.
    return frappe.db.get_values(doctype, {} if filters is None else filters, pluck or fields or ['name'],
        as_dict=not bool(pluck), pluck=bool(pluck), order_by=order_by,
        for_update=bool(frappe.flags.in_opd_mutation)) or []


def _sql(query, values=None, **kwargs):
    if frappe.flags.in_opd_mutation and not query.rstrip().endswith('FOR UPDATE'):
        query += ' FOR UPDATE'
    return frappe.db.sql(query, values, **kwargs)


def _roles():
    return set(frappe.get_roles()) if frappe.session.user != "Guest" else set()


def _manager():
    return frappe.session.user == "Administrator" or bool(_roles() & (MANAGERS | STAFF))


def _reception():
    return _manager() or "Appointment Receptionist" in _roles()


def _require_manager():
    if not _manager(): frappe.throw("Only an appointment manager can change OPD setup.", frappe.PermissionError)


def _require_reception():
    if not _reception(): frappe.throw("Reception or manager access is required.", frappe.PermissionError)


def _assigned_rooms(clinic):
    # Every authorized OPD operator can select an enabled room; no staff mapping.
    return {r['name'] for r in store.records('Room', {'clinic':clinic,'enabled':1})}


def _branch_admin():
    return frappe.session.user == 'Administrator' or bool(_roles() & MANAGERS)


def appointment_branch(doctype, name):
    if doctype == 'Mobile App Appointment':
        encounter = _value(doctype,name,'patient_encounter')
        if encounter: doctype,name='Patient Encounter',encounter
    config=store.configuration()
    return config.get('appointment_branches',{}).get(f'{doctype}:{name}') or config.get('default_clinic')


def _access(clinic, display=False):
    doc = _doc("Clinic", clinic)
    if not doc.enabled or frappe.session.user == "Guest": frappe.throw("OPD clinic access denied.", frappe.PermissionError)
    if _branch_admin(): return doc
    if (_reception() or _roles() & STAFF) and (doc.get('members') is None or frappe.session.user in doc.members): return doc
    if display and "OPD Display" in _roles() and any(x.user == frappe.session.user for x in doc.display_users): return doc
    frappe.throw("OPD staff or manager access is required.", frappe.PermissionError)


def _json(value, kind):
    result = frappe.parse_json(value) if isinstance(value, str) else value
    if not isinstance(result, kind): frappe.throw("Invalid OPD request format.")
    return result


def _reason(reason):
    value = str(reason or "").strip()
    if not value or len(value) > 1000: frappe.throw("Enter a reason of 1 to 1,000 characters.")
    return value


def _version(doc, expected):
    if expected is None or str(doc.revision) != str(expected):
        frappe.throw("This record changed. Refresh and try again.", frappe.TimestampMismatchError)


def _save(doc):
    if isinstance(doc, store.Record) or doc.meta.has_field("revision"): doc.revision = cint(doc.revision) + 1
    doc.save(ignore_permissions=True)
    return doc


def _get(dt, name, clinic):
    doc = _doc(dt, name)
    if doc.clinic != clinic: frappe.throw("The record belongs to another clinic.", frappe.PermissionError)
    return doc


def _mutation(fn):
    signature = inspect.signature(fn)
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        values = signature.bind(*args, **kwargs); values.apply_defaults(); data = values.arguments
        clinic, request_id = data['clinic'], str(data.get('request_id') or '')
        _access(clinic)
        if not request_id or len(request_id) > 100: frappe.throw("A request ID is required. Refresh and try again.")
        store.lock()
        key = hashlib.sha256((clinic + ':' + frappe.session.user + ':' + request_id).encode()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps(dict(data), sort_keys=True, default=str).encode()).hexdigest()
        existing = store.event_for_request(key)
        if existing:
            if existing.operation != fn.__name__ or existing.payload_hash != fingerprint: frappe.throw("This request ID was already used for a different action.")
            return json.loads(existing.response_json)
        previous = frappe.flags.in_opd_mutation; frappe.flags.in_opd_mutation = True
        frappe.db.savepoint('opd_action')
        try:
            _access(clinic)  # Recheck current assignments after acquiring the clinic lock.
            result = fn(*args, **kwargs)
            clinic_doc = _doc("Clinic", clinic)
            clinic_doc.event_sequence = cint(clinic_doc.event_sequence) + 1
            clinic_doc.save(ignore_permissions=True)
            _doc({"doctype": "Event", "clinic": clinic,
                "sequence": clinic_doc.event_sequence, "actor": frappe.session.user,
                "operation": fn.__name__, "request_key": key, "payload_hash": fingerprint,
                "reason": data.get('reason'), "response_json": frappe.as_json(result),
                "details_json": frappe.as_json({k: v for k, v in data.items() if k not in ('patient_details', 'request_id')})}).insert(ignore_permissions=True)
            frappe.publish_realtime('opd_queue_changed', {'clinic': clinic, 'sequence': clinic_doc.event_sequence}, room='all', after_commit=True)
            return result
        except Exception:
            frappe.db.rollback(save_point='opd_action'); raise
        finally: frappe.flags.in_opd_mutation = previous
    return wrapped


def _purposes(clinic):
    configured = _doc('Clinic', clinic).get('purposes')
    return configured if configured is not None else [{'id':stage,'name':stage,'stage':stage} for stage in STAGES]


def _room_data(doc):
    return {k: doc.get(k) for k in ('name', 'room_number', 'room_label', 'purpose', 'purpose_id', 'enabled', 'revision')} | {
        'purpose_name': next((p['name'] for p in _purposes(doc.clinic) if p['id'] == (doc.get('purpose_id') or doc.purpose)), doc.purpose) if doc.clinic else doc.purpose,
        'assignments': [{'practitioner': p} for p in dict.fromkeys(a.practitioner for a in doc.assignments if a.practitioner)]}


def _route_steps(doc):
    if doc.get('steps') is not None: return doc.steps
    return [frappe._dict(purpose_id=purpose, stage=purpose, room=doc.get(field)) for purpose,field in zip(STAGES,ROOM_FIELDS)]


def _route_data(doc):
    return {k: doc.get(k) for k in ('name', 'department', *ROOM_FIELDS, 'booking_labels', 'revision')} | {'steps':_route_steps(doc)}


def _visit_data(doc):
    result = {k: doc.get(k) for k in ('name', 'patient', 'patient_name', 'department', 'original_doctor',
        'consultation_doctor', 'visit_date', 'status', 'stage_index', 'priority', 'revision', 'encounter',
        'source_doctype', 'source_name', 'route_version', 'vitals')} | {
        'clinic':doc.clinic, 'clinic_name':_value('Clinic',doc.clinic,'clinic_name'),
        'consultation_doctor_name': _value('Healthcare Practitioner', doc.consultation_doctor, 'practitioner_name') or doc.consultation_doctor,
        'token': (doc.get('token_prefix')+'-' if doc.get('token_prefix') else '')+str(doc.token_number).zfill(3), 'stages': [{k: s.get(k) for k in (
            'stage', 'purpose_id', 'room', 'state', 'queued_at', 'called_at', 'started_at', 'completed_at', 'session')} for s in doc.stages]}

    return frappe.parse_json(frappe.as_json(result))


@frappe.whitelist()
def bootstrap(room=None):
    if frappe.session.user == 'Guest': frappe.throw('Sign in to use OPD.', frappe.PermissionError)
    clinics = []
    for row in _all('Clinic', filters={'enabled': 1}, fields=['name', 'clinic_name', 'code', 'address', 'timezone', 'revision']):
        try: _access(row.name, display=True); clinics.append(row)
        except frappe.PermissionError: frappe.clear_messages()
    room_clinic=None
    if room:
        room_clinic=_doc('Room',room).clinic;_access(room_clinic)
    return {'clinics': clinics, 'room_clinic':room_clinic, 'branch_admin': _branch_admin(), 'manager': _manager(), 'reception': _reception(), 'user': frappe.session.user,
        'display_only': 'OPD Display' in _roles() and not (_roles() & STAFF or _manager())}


@frappe.whitelist(methods=['POST'])
@_mutation
def save_branch(clinic,request_id,branch_name,code,address='',members=None,branch=None,expected_revision=None,enabled=1,copy_setup=0):
    if not _branch_admin(): frappe.throw('Only branch administrators can manage branches.',frappe.PermissionError)
    name=str(branch_name or '').strip();code=str(code or '').strip().upper()
    if not name or not code.isalnum() or len(code)>10: frappe.throw('Enter a branch name and a short alphanumeric token prefix.')
    for row in store.records('Clinic'):
        if row.name!=branch and (row.clinic_name.casefold()==name.casefold() or row.get('code')==code): frappe.throw('Branch name and token prefix must be unique.')
    users=_json(members or [],list)
    for user in users:
        if not _value('User',user,'enabled') or not set(frappe.get_roles(user)) & (STAFF|MANAGERS): frappe.throw('Choose enabled OPD users for branch access.')
    target=_doc('Clinic',branch) if branch else store.new('Clinic')
    if branch:
        _version(target,expected_revision)
        if not cint(enabled) and not any(c.name!=branch for c in store.records('Clinic',{'enabled':1})): frappe.throw('Keep at least one active branch.')
        if not cint(enabled) and (_exists('Visit',{'clinic':branch,'status':'Active'}) or _exists('Session',{'clinic':branch})): frappe.throw('Close active visits and sessions before disabling the branch.')
    target.update(clinic_name=name,code=code,address=str(address)[:1000],members=users,enabled=cint(enabled),timezone=frappe.utils.get_system_timezone())
    _save(target)
    if not branch and cint(copy_setup):
        source=_access(clinic);mapping={}
        target.set('purposes',_purposes(clinic));target.save()
        for room in store.records('Room',{'clinic':clinic}):
            data=dict(room);old=data.pop('name');data.pop('creation',None);data.update(clinic=target.name,revision=0)
            copied=store.Record('Room',data);_save(copied);mapping[old]=copied.name
        for route in store.records('Route',{'clinic':clinic}):
            data=dict(route);data.pop('name');data.pop('creation',None);data.update(clinic=target.name,revision=0)
            for field in ROOM_FIELDS:data[field]=mapping.get(data.get(field))
            data['steps']=[dict(step,room=mapping[step['room']]) for step in _route_steps(route)]
            _save(store.Record('Route',data))
    return {'branch':target.name}


@frappe.whitelist(methods=['POST'])
@_mutation
def delete_branch(clinic,request_id,branch,expected_revision,reason):
    if not _branch_admin(): frappe.throw('Only branch administrators can delete branches.',frappe.PermissionError)
    _reason(reason)
    if branch==clinic: frappe.throw('Select another branch before deleting this branch.')
    target=_doc('Clinic',branch);_version(target,expected_revision)
    config=store.configuration()
    if branch==config.get('default_clinic'): frappe.throw('The original branch holds legacy appointments. Deactivate it instead.')
    if any(config.get('appointment_branches',{}).get(k)==branch for k in config.get('appointment_branches',{})):
        frappe.throw('This branch has linked appointments. Deactivate it instead.')
    if any(store.records(kind,{'clinic':branch}) for kind in ('Room','Route','Session','Visit')):
        frappe.throw('This branch has rooms, routes or patient history. Deactivate it instead.')
    store.delete('Clinic',branch)
    return {'deleted':branch}


@frappe.whitelist()
def list_branches():
    if not _branch_admin(): frappe.throw('Only branch administrators can manage branches.',frappe.PermissionError)
    return [{k:c.get(k) for k in ('name','clinic_name','code','enabled','revision')} for c in store.records('Clinic')]


@frappe.whitelist()
def branch_details(clinic):
    doc=_access(clinic)
    if not _branch_admin(): frappe.throw('Branch administrator access required.',frappe.PermissionError)
    return {'name':doc.name,'clinic_name':doc.clinic_name,'code':doc.get('code'),'address':doc.get('address'),'members':doc.get('members') or [],'revision':doc.revision,'enabled':doc.enabled}


@frappe.whitelist(methods=['POST'])
@_mutation
def assign_appointment_branch(clinic,request_id,doctype,name,reason):
    _require_reception();_reason(reason)
    source=_canonical(doctype,name)
    previous=appointment_branch(source.doctype,source.name)
    if previous:_access(previous)
    if find_visit(source.doctype,source.name): frappe.throw('This appointment already has an OPD visit. Finish or withdraw it before arranging a new branch visit.')
    config=store.configuration();config.setdefault('appointment_branches',{})[f'{source.doctype}:{source.name}']=clinic;store.write_configuration(config)
    return {'clinic':clinic}


@frappe.whitelist()
def branch_summary(date=None):
    return [{'clinic':c['name'],'clinic_name':c['clinic_name'],**daily_visits(c['name'],date)['counts']} for c in bootstrap()['clinics']]


@frappe.whitelist()
def get_setup(clinic):
    doc = _access(clinic); _require_reception()
    rooms = [_room_data(_doc('Room', n)) for n in _all('Room', filters={'clinic': clinic}, pluck='name')]
    routes = [_route_data(_doc('Route', n)) for n in _all('Route', filters={'clinic': clinic}, pluck='name')]
    users = _sql("""SELECT DISTINCT u.name, u.full_name FROM `tabUser` u
        LEFT JOIN `tabHas Role` h ON h.parent=u.name AND h.parenttype='User'
        WHERE u.enabled=1 AND u.user_type='System User' AND (u.name='Administrator' OR h.role IN %s)
        ORDER BY u.full_name""", (tuple(STAFF | MANAGERS | {'OPD Display'}),), as_dict=True)
    return {'clinic': doc.name, 'clinic_name': doc.clinic_name, 'config_version': doc.config_version,
        'rooms': rooms, 'routes': routes, 'purposes': _purposes(clinic), 'display_users': [x.user for x in doc.display_users] if _manager() else [],
        'departments': _all('Medical Department', pluck='name', order_by='name'),
        'practitioners': _all('Healthcare Practitioner', filters={'status': 'Active'}, fields=['name', 'practitioner_name', 'user_id', 'department'], order_by='practitioner_name'),
        'genders': _all('Gender', pluck='name'),
        'users': [u for u in users if u.name == 'Administrator' or set(frappe.get_roles(u.name)) & (STAFF | MANAGERS)] if _manager() else [],
        'display_accounts': [u.name for u in users if 'OPD Display' in frappe.get_roles(u.name)] if _manager() else []}


def _check_assignments(assignments, purpose):
    if purpose == 'Doctor Consultation' and not assignments:
        frappe.throw('Select at least one doctor for a consultation room.')
    for a in assignments:
        if not a.get('practitioner') or _value('Healthcare Practitioner', a['practitioner'], 'status') != 'Active':
            frappe.throw('Choose an active practitioner.')


@frappe.whitelist(methods=['POST'])
@_mutation
def save_setup(clinic, request_id, expected_version, rooms, routes, display_users=None, purposes=None):
    _require_manager(); clinic_doc = _doc('Clinic', clinic)
    if str(clinic_doc.config_version) != str(expected_version): frappe.throw('Setup changed. Refresh before saving.', frappe.TimestampMismatchError)
    room_inputs, route_inputs = _json(rooms, list), _json(routes, list)
    if len(room_inputs) > 100 or len(route_inputs) > 100: frappe.throw('Maximum 100 rooms and routes per clinic.')
    purpose_rows = _json(purposes, list) if purposes is not None else _purposes(clinic)
    purpose_map, purpose_names = {}, set()
    for item in purpose_rows:
        key = str(item.get('id') or '').strip()
        label = str(item.get('name') or '').strip()
        stage = item.get('stage')
        if not key or len(key)>100 or key in purpose_map or not label or len(label)>100 or label.casefold() in purpose_names or stage not in STAGES:
            frappe.throw('Purposes need unique names and identifiers and a valid routing stage.')
        purpose_map[key] = {'id':key,'name':label,'stage':stage}; purpose_names.add(label.casefold())
    previous_purposes = {item['id']:item for item in _purposes(clinic)}
    for room in store.records('Room', {'clinic':clinic}):
        key = room.get('purpose_id') or room.purpose
        if key not in purpose_map:
            frappe.throw('This purpose is assigned to a room. Change the room purpose and save before deleting it.')
        if previous_purposes.get(key, {}).get('stage') != purpose_map[key]['stage']:
            frappe.throw('Cannot change the routing stage of a purpose assigned to a room. Create a new purpose instead.')
    existing_rooms = set(_all('Room', filters={'clinic': clinic}, pluck='name'))
    removed_rooms = existing_rooms - {r.get('name') for r in room_inputs}
    active_visits = store.records('Visit', {'clinic':clinic,'status':'Active'})
    for room_id in removed_rooms:
        if _exists('Session', {'clinic':clinic,'room':room_id}):
            frappe.throw('Close the room session before deleting this room.')
        if any(stage.room == room_id for visit in active_visits for stage in visit.stages):
            frappe.throw('This room belongs to an active visit. Finish or reroute those visits before deleting it.')
        if any(room_id == row.get(field) for row in route_inputs for field in ROOM_FIELDS) or any(step.get('room') == room_id for row in route_inputs for step in row.get('steps',[])):
            frappe.throw('Remove this room from department routes before deleting it.')
    ids, room_docs, numbers, practitioner_rooms = {}, {}, set(), {}
    for row in room_inputs:
        number = str(row.get('room_number') or '').strip()
        purpose_id = row.get('purpose_id') or row.get('purpose')
        if purpose_id not in purpose_map: frappe.throw('Select a configured room purpose.')
        purpose = purpose_map[purpose_id]['stage']
        key = row.get('name') or row.get('client_id')
        if not key or key in ids or not number or len(number) > 30 or number.casefold() in numbers or purpose not in STAGES: frappe.throw('Each room needs a unique number, purpose and identifier.')
        numbers.add(number.casefold()); enabled = int(bool(cint(row.get('enabled')))); assignments = [{'practitioner': p} for p in dict.fromkeys(a.get('practitioner') for a in _json(row.get('assignments', []), list) if a.get('practitioner'))]
        if enabled: _check_assignments(assignments, purpose)
        for a in assignments:
            if enabled and purpose == 'Doctor Consultation':
                doctor = a.get('practitioner')
                if doctor in practitioner_rooms and practitioner_rooms[doctor] != key: frappe.throw('A consultation doctor cannot be assigned to two enabled rooms.')
                practitioner_rooms[doctor] = key
        doc = _get('Room', row['name'], clinic) if row.get('name') else store.new('Room')
        old = _room_data(doc) if not doc.is_new() else None
        changed_control = old and (old['purpose'] != purpose or bool(old['enabled']) != bool(enabled) or old['assignments'] != assignments)
        if changed_control and _exists('Session', {'room': doc.name, 'status': ['!=', 'Closed']}): frappe.throw('End the active room session before changing its purpose, staff or enabled status.')
        if old and (not enabled or old['purpose'] != purpose):
            if any(stage.room == doc.name and stage.state != 'Completed'
                   for visit in store.records('Visit', {'clinic':clinic,'status':'Active'}) for stage in visit.stages):
                frappe.throw('Reroute unfinished patients before disabling a room or changing its purpose.')
        doc.update({'clinic': clinic, 'room_number': number, 'room_label': str(row.get('room_label') or '')[:140], 'purpose': purpose, 'purpose_id': purpose_id, 'enabled': enabled})
        doc.set('assignments', assignments); _save(doc); ids[key] = doc.name; room_docs[doc.name] = doc
    departments, retained = set(), set()
    for row in route_inputs:
        department = row.get('department')
        if row.get('name') in retained: frappe.throw('A department route may appear only once.')
        if department in departments or not _exists('Medical Department', department): frappe.throw('Choose a distinct valid department for each route.')
        departments.add(department); values = {}
        steps=[]
        supplied={x.get('purpose_id'):x.get('room') for x in row.get('steps',[])}
        for field,purpose in zip(ROOM_FIELDS,STAGES):
            if row.get(field): supplied[purpose]=row[field]
        # Preserve purpose order, with the consultation as the last step.
        ordered=sorted(purpose_map.values(),key=lambda p:p['stage']=='Doctor Consultation')
        for item in ordered:
            selection=supplied.get(item['id'])
            if not selection: continue
            room_id=ids.get(selection)
            if not room_id or not room_docs[room_id].enabled or room_docs[room_id].purpose_id != item['id']:
                frappe.throw(f"{department}: select an enabled {item['name']} room.")
            steps.append({'purpose_id':item['id'],'stage':item['name'],'room':room_id})
        if not steps: frappe.throw(f'{department}: select at least one room.')
        for field,purpose in zip(ROOM_FIELDS,STAGES):
            values[field]=next((x['room'] for x in steps if x['purpose_id']==purpose),None)
        if not values['consultation_room']:
            values['consultation_room']=next((x['room'] for x in steps if room_docs[x['room']].purpose=='Doctor Consultation'),None)
        if not values['consultation_room']: frappe.throw(f'{department}: select a consultation room for the booked doctor.')
        values['steps']=steps
        doc = _get('Route', row['name'], clinic) if row.get('name') else store.new('Route')
        doc.update({'clinic': clinic, 'department': department, 'booking_labels': str(row.get('booking_labels') or '')[:1000], **values})
        _save(doc); retained.add(doc.name)
    for name in _all('Route', filters={'clinic': clinic}, pluck='name'):
        if name not in retained:
            route = _get('Route',name,clinic)
            if any(v.department == route.department for v in active_visits):
                frappe.throw('This department route has active visits. Finish or reroute them before deleting it.')
            store.delete('Route',name)
    for room_id in removed_rooms:
        store.delete('Room',room_id)
    for v in _all('Visit', filters={'clinic': clinic, 'status': 'Active'}, pluck='name'):
        visit = _doc('Visit', v)
        for consultation in visit.stages:
            if consultation.state == 'Completed': continue
            room = room_docs.get(consultation.room)
            if not room or room.purpose != 'Doctor Consultation': continue
            if room and not any(a.practitioner == visit.consultation_doctor for a in room.assignments): frappe.throw('Reassign waiting patients before removing their consultation doctor from a room.')
    if display_users is not None:
        viewers = _json(display_users, list)
        for user in viewers:
            if not _value('User', user, 'enabled') or 'OPD Display' not in frappe.get_roles(user): frappe.throw('Display accounts must be enabled and have the OPD Display role.')
        clinic_doc.set('display_users', [{'user': u} for u in sorted(set(viewers))])
    clinic_doc.set('purposes', list(purpose_map.values()))
    clinic_doc.config_version = cint(clinic_doc.config_version) + 1; clinic_doc.save(ignore_permissions=True)
    return {'config_version': clinic_doc.config_version}



def _route(clinic, department, doctor):
    name = _value('Route', {'clinic': clinic, 'department': department}, 'name')
    if not name: frappe.throw('Configure a complete room route for this department first.')
    route = _doc('Route', name)
    for step in _route_steps(route):
        room = _get('Room', step['room'], clinic)
        if not room.enabled or (room.get('purpose_id') or room.purpose) != step['purpose_id']:
            frappe.throw('The department route contains an unavailable room.')
    room = _doc('Room', route.consultation_room)
    if _value('Healthcare Practitioner', doctor, 'status') != 'Active' or not any(a.practitioner == doctor for a in room.assignments):
        frappe.throw('The consultation doctor is not assigned to this department\'s consultation room. Reassign the doctor or update setup.')
    return route


def _canonical(doctype, name):
    from mobile_app.api.appointment_calendar import _source
    doc = _source(doctype, name, lock=bool(frappe.flags.in_opd_mutation))
    if doctype == 'Mobile App Appointment' and doc.get('patient_encounter'): return _source('Patient Encounter', doc.patient_encounter, lock=bool(frappe.flags.in_opd_mutation))
    return doc


def find_visit(doctype, name):
    if not frappe.get_meta(store.WORKFLOW).has_field('opd_visit_id'): return None
    doc = _canonical(doctype, name)
    return _value('Visit', {'source_doctype': doc.doctype, 'source_name': doc.name}, 'name')


@frappe.whitelist()
def checkin_context(clinic, doctype, name):
    _access(clinic); _require_reception()
    from mobile_app.api import appointment_calendar as cal
    doc = _canonical(doctype, name)
    if not cal._can_read(doc, cal._workflow(doc.doctype, doc.name), cal._context()): frappe.throw('Appointment access denied.', frappe.PermissionError)
    direct = doc.get('medical_department'); label = str(doc.get('page_url_disease') or '').strip().casefold()
    matches = [r.department for r in _all('Route', filters={'clinic': clinic}, fields=['department', 'booking_labels'])
        if label and label in [x.strip().casefold() for x in (r.booking_labels or '').splitlines()]]
    department = direct or (matches[0] if len(matches) == 1 else None)
    return {'department': department, 'doctor': cal._doctor(doc)[0], 'patient': doc.get('patient'),
        'patient_name': doc.get('patient_name'), 'visit': find_visit(doc.doctype, doc.name)}


def _walkin_appointment(patient, doctor, clinic=None):
    from mobile_app.api import appointment_calendar as cal
    matches = []
    for name in _all('Patient Encounter', filters={'patient': patient, 'docstatus': ['!=', 2]}, pluck='name'):
        doc = _doc('Patient Encounter', name)
        if clinic and appointment_branch(doc.doctype,doc.name) not in (None,clinic): continue
        date = doc.get('pe_appointment_date') or doc.get('encounter_date')
        if not date or getdate(date) != getdate() or doc.get('sr_encounter_type') != 'Appointment':
            continue
        if doc.get('sr_encounter_place') == 'Online' or cal._doctor(doc)[0] != doctor:
            continue
        if cal._status(doc, cal._workflow(doc.doctype, doc.name)) == 'Approved':
            matches.append(doc)
    if len(matches) > 1:
        frappe.throw('Multiple approved appointments match this patient and doctor today. Check in the intended appointment from the calendar.')
    return matches[0] if matches else None


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def patient_query(doctype, txt, searchfield, start, page_len, filters=None):
    _require_reception(); clinic = _json(filters or {}, dict).get('clinic'); _access(clinic)
    return _sql("""SELECT name, patient_name, mobile FROM `tabPatient`
        WHERE name LIKE %(q)s OR patient_name LIKE %(q)s OR mobile LIKE %(q)s
        ORDER BY modified DESC LIMIT %(start)s, %(length)s""",
        {'q': '%' + (txt or '') + '%', 'start': max(0,cint(start)), 'length': min(30,max(1,cint(page_len)))})


@frappe.whitelist(methods=['POST'])
@_mutation
def check_in(clinic, request_id, department, doctor, patient=None, patient_details=None,
             source_doctype=None, source_name=None, expected_status='Approved', reason=None):
    _require_reception()
    from mobile_app.api import appointment_calendar as cal
    source = _canonical(source_doctype, source_name) if source_doctype else None
    if not source and patient:
        source = _walkin_appointment(patient, doctor, clinic)
    if source:
        assigned_branch=appointment_branch(source.doctype,source.name)
        if assigned_branch and assigned_branch!=clinic: frappe.throw('This appointment belongs to another branch. Select its branch or reassign the appointment first.')
        if not cal._can_read(source, cal._workflow(source.doctype, source.name), cal._context()): frappe.throw('Appointment access denied.', frappe.PermissionError)
        existing = find_visit(source.doctype, source.name)
        if existing: return _visit_data(_get('Visit', existing, clinic))
        online = source.get('sr_encounter_place') == 'Online' if source.doctype == 'Patient Encounter' else bool(source.get('is_online'))
        if online: frappe.throw('Online appointments do not receive OPD tokens.')
        if cal._status(source, cal._workflow(source.doctype, source.name)) != 'Approved' or expected_status != 'Approved': frappe.throw('Only approved appointments can be checked in.')
        booked_doctor = cal._doctor(source)[0]
        if booked_doctor != doctor: _reason(reason)
        if source.get('patient'):
            if patient and patient != source.patient: frappe.throw('Use the patient linked to this encounter.')
            patient = source.patient
    route = _route(clinic, department, doctor)
    if not patient:
        details = _json(patient_details, dict)
        if not all(str(details.get(k) or '').strip() for k in ('first_name','sex','mobile')): frappe.throw('New patients require name, gender and mobile number.')
        person = _doc({'doctype': 'Patient', 'first_name': details['first_name'],
            'sex': details['sex'], 'mobile': details['mobile'], 'sr_medical_department': department})
        person.insert(ignore_permissions=True); patient = person.name
    person = _doc('Patient', patient)
    if any(v.clinic != clinic for v in store.records('Visit',{'patient':patient,'status':'Active'})):
        frappe.throw('This patient has an active visit at another branch. Close that visit with a reason before checking in here.')
    active = _value('Visit', {'clinic': clinic, 'patient': patient, 'status': 'Active'}, 'name')
    if active:
        existing_visit = _get('Visit', active, clinic)
        same_route = existing_visit.department == department and existing_visit.consultation_doctor == doctor
        if not source and same_route:
            return _visit_data(existing_visit)
        if source and same_route and not existing_visit.source_doctype:
            appointment_date = source.get('pe_appointment_date') or source.get('appointment_date') or source.get('encounter_date')
            if appointment_date and getdate(appointment_date) == getdate(existing_visit.visit_date):
                # Adopt the walk-in visit without changing its token, stages or active room.
                existing_visit.source_doctype = source.doctype
                existing_visit.source_name = source.name
                existing_visit.source_key = hashlib.sha256(f'{source.doctype}:{source.name}'.encode()).hexdigest()
                if source.doctype == 'Patient Encounter': existing_visit.encounter = source.name
                _save(existing_visit)
                previous = frappe.flags.opd_checkin; frappe.flags.opd_checkin = True
                try: cal.update_appointment(source.doctype, source.name, 'check_in', 'Approved', reason=reason)
                finally: frappe.flags.opd_checkin = previous
                return _visit_data(existing_visit)
        frappe.throw(f'This patient already has active OPD token {str(existing_visit.token_number).zfill(3)} for {existing_visit.department} with {existing_visit.consultation_doctor}. Open that visit to continue or change its department or doctor.')
    clinic_doc = _doc('Clinic', clinic); today = getdate()
    if str(clinic_doc.token_date or '') != str(today): clinic_doc.token_date, clinic_doc.last_token = today, 0
    clinic_doc.last_token = cint(clinic_doc.last_token) + 1; clinic_doc.save(ignore_permissions=True)
    source_key = f'{source.doctype}:{source.name}' if source else f'walkin:{request_id}:{frappe.session.user}'
    visit = _doc({'doctype': 'Visit', 'clinic': clinic, 'patient': patient,
        'patient_name': person.patient_name, 'department': department,
        'original_doctor': booked_doctor if source and _exists('Healthcare Practitioner', booked_doctor) else doctor,
        'consultation_doctor': doctor, 'token_prefix':clinic_doc.get('code'), 'visit_date': today, 'token_number': clinic_doc.last_token,
        'token_key': f'{clinic}:{today}:{clinic_doc.last_token}', 'source_key': hashlib.sha256(source_key.encode()).hexdigest(),
        'source_doctype': source.doctype if source else None, 'source_name': source.name if source else None,
        'encounter': source.name if source and source.doctype == 'Patient Encounter' else None,
        'status': 'Active', 'stage_index': 0, 'route_version': route.revision,
        'stages': [{'stage': step['stage'], 'purpose_id':step['purpose_id'], 'room': step['room'], 'state': 'Waiting' if i == 0 else 'Not Ready',
                    'queued_at': now_datetime() if i == 0 else None} for i,step in enumerate(_route_steps(route))]})
    _save(visit)
    if source:
        config=store.configuration();config.setdefault('appointment_branches',{})[f'{source.doctype}:{source.name}']=clinic;store.write_configuration(config)
        previous = frappe.flags.opd_checkin; frappe.flags.opd_checkin = True
        try: cal.update_appointment(source.doctype, source.name, 'check_in', 'Approved', reason=reason)
        finally: frappe.flags.opd_checkin = previous
    return _visit_data(visit)


def _session(clinic, name, expected=None, require_controller=True):
    session = _get('Session', name, clinic)
    if session.status == 'Closed': frappe.throw('This room session has ended.')
    if expected is not None: _version(session, expected)
    return session


def _room_assignment(room, user, practitioner):
    if not room.enabled: frappe.throw('This room is disabled.')
    if not _value('User', user, 'enabled') or not (user == 'Administrator' or set(frappe.get_roles(user)) & (STAFF | MANAGERS)):
        frappe.throw('An enabled OPD staff or manager account is required.', frappe.PermissionError)
    if room.purpose == 'Doctor Consultation' and not any(a.practitioner == practitioner for a in room.assignments):
        frappe.throw('Choose a consultation doctor configured for this room.')


@frappe.whitelist(methods=['POST'])
@_mutation
def start_session(clinic, request_id, room, practitioner=None):
    doc = _get('Room', room, clinic)
    if doc.purpose != 'Doctor Consultation': practitioner = None
    _room_assignment(doc, frappe.session.user, practitioner)
    if _exists('Session', {'clinic': clinic, 'room': room, 'status': ['!=','Closed']}): frappe.throw('This room already has an active controller.')
    if practitioner and _exists('Session', {'practitioner': practitioner, 'status': ['!=','Closed']}): frappe.throw('This doctor is already working in another room.')
    session = _doc({'doctype':'Session','clinic':clinic,'room':room,
        'controller':frappe.session.user,'practitioner':practitioner,'status':'Available','started_at':now_datetime()})
    _save(session); return {'session':session.name, 'revision':session.revision}


def _claim_next(clinic,room,room_session,required=False):
    candidates=[]
    for item in store.records('Visit', {'clinic':clinic,'status':'Active'}):
        stage=item.stages[item.stage_index]
        if stage.room==room.name and stage.state=='Waiting' and (room.purpose!='Doctor Consultation' or item.consultation_doctor==room_session.practitioner):
            candidates.append(item)
    candidates.sort(key=lambda v:(-cint(v.priority),str(v.stages[v.stage_index].queued_at),str(v.creation),v.name))
    rows=[v.name for v in candidates[:1]]
    if not rows:
        if required: frappe.throw('No eligible patients are waiting for this room.')
        return None
    visit = _get('Visit',rows[0],clinic); stage = visit.stages[visit.stage_index]
    transition={'affected_visit': visit.name, 'stage': stage.stage, 'from_state': stage.state, 'to_state': 'Called'}
    stage.state, stage.called_at, stage.session = 'Called', now_datetime(), room_session.name
    room_session.current_visit = visit.name
    room_session.call_sequence = cint(_value('Clinic',clinic,'event_sequence')) + 1
    _save(visit)
    return transition


@frappe.whitelist(methods=['POST'])
@_mutation
def room_action(clinic, request_id, session, expected_version, action, reason=None, controller=None, practitioner=None):
    handover = action == 'handover'
    if handover: _require_manager(); _reason(reason)
    room_session = _session(clinic, session, expected_version, require_controller=not handover)
    room = _get('Room', room_session.room, clinic)
    audit = {'room': room.name, 'previous_controller': room_session.controller, 'previous_practitioner': room_session.practitioner}
    if handover:
        _room_assignment(room, controller, practitioner)
        if room.purpose == 'Doctor Consultation' and room_session.current_visit and practitioner != room_session.practitioner: frappe.throw('Release the current patient before changing the consultation doctor.')
        room_session.controller, room_session.practitioner = controller, practitioner
    elif action in ('pause','resume'): room_session.status = 'Paused' if action == 'pause' else 'Available'
    elif action == 'end':
        if room_session.current_visit: frappe.throw('Complete or release the current patient first.')
        room_session.status, room_session.ended_at = 'Closed', now_datetime()
    elif action == 'call_next':
        if room_session.status != 'Available' or room_session.current_visit: frappe.throw('The room must be available and empty before calling the next patient.')
        audit.update(_claim_next(clinic,room,room_session,required=True))
    elif action in ('recall','start','complete','complete_next','absent','release'):
        if not room_session.current_visit: frappe.throw('This room has no current patient.')
        visit = _get('Visit',room_session.current_visit,clinic); stage = visit.stages[visit.stage_index]
        audit.update({'affected_visit': visit.name, 'stage': stage.stage, 'from_state': stage.state})
        if stage.session != session or stage.state not in ('Called','In Progress'): frappe.throw('The patient state changed. Refresh before continuing.')
        if action == 'recall':
            if stage.state != 'Called': frappe.throw('Only a called patient can be recalled.')
            stage.called_at = now_datetime(); room_session.call_sequence = cint(_value('Clinic',clinic,'event_sequence')) + 1
        elif action == 'start':
            if stage.state != 'Called': frappe.throw('The patient has already started this stage.')
            stage.state, stage.started_at = 'In Progress', now_datetime()
        elif action in ('complete','complete_next'):
            if action == 'complete_next' and room_session.status != 'Available': frappe.throw('Resume the room before finishing and calling the next patient.')
            if stage.state != 'In Progress': frappe.throw('Start the patient\'s stage before completing it.')
            stage.state, stage.completed_at = 'Completed', now_datetime(); room_session.current_visit = None
            if visit.stage_index == len(visit.stages)-1: visit.status, visit.completed_at = 'Completed', now_datetime()
            else:
                visit.stage_index += 1; nxt = visit.stages[visit.stage_index]; nxt.state, nxt.queued_at = 'Waiting', now_datetime()
        else:
            if action == 'absent' and stage.state != 'Called': frappe.throw('Use release with a reason for a patient already in progress.')
            if action == 'release': _reason(reason)
            stage.state = 'On Hold'; room_session.current_visit = None
        audit['to_state'] = stage.state
        _save(visit)
        if visit.status == 'Completed' and visit.source_doctype:
            from mobile_app.api import appointment_calendar as cal
            cal._sync_clinic_status(_canonical(visit.source_doctype,visit.source_name),'Checked In')
        if action == 'complete_next':
            audit['next_call'] = _claim_next(clinic,room,room_session)
    else: frappe.throw('Unknown room action.')
    _save(room_session); return {'session':session, 'revision':room_session.revision, 'visit':room_session.current_visit, 'audit':audit}



def _reroute_visit(visit, department, doctor):
    route = _route(visit.clinic,department,doctor)
    if any(s.state in ('Called','In Progress') for s in visit.stages): frappe.throw('Release the called/in-progress patient before rerouting.')
    completed=[s for s in visit.stages if s.state=='Completed']
    completed_ids={s.get('purpose_id') or s.stage for s in completed}
    old={s.get('purpose_id') or s.stage:s for s in visit.stages if s.state!='Completed'}
    remaining=[]
    for step in _route_steps(route):
        if step['purpose_id'] in completed_ids: continue
        previous=old.get(step['purpose_id'])
        row=frappe._dict(dict(previous)) if previous else frappe._dict(queued_at=None)
        row.update(stage=step['stage'],purpose_id=step['purpose_id'],room=step['room'],session=None)
        row.state=('On Hold' if previous and previous.state=='On Hold' else 'Waiting') if not remaining else 'Not Ready'
        if not remaining and not row.queued_at: row.queued_at=now_datetime()
        remaining.append(row)
    if not remaining: frappe.throw('The new route has no unfinished stages. Complete the current visit instead.')
    visit.set('stages',completed+remaining);visit.stage_index=len(completed)
    visit.department,visit.consultation_doctor,visit.route_version = department,doctor,route.revision
    _save(visit)


@frappe.whitelist(methods=['POST'])
@_mutation
def visit_action(clinic, request_id, visit, expected_version, action, reason, department=None, doctor=None, priority=None):
    _require_reception(); _reason(reason)
    doc = _get('Visit',visit,clinic); _version(doc,expected_version)
    if doc.status != 'Active': frappe.throw('This visit has already ended.')
    stage = doc.stages[doc.stage_index]
    if action == 'reroute': _reroute_visit(doc,department or doc.department,doctor or doc.consultation_doctor)
    elif action == 'priority': doc.priority = int(bool(cint(priority))); _save(doc)
    elif action == 'requeue':
        if stage.state != 'On Hold': frappe.throw('Only a patient on hold can rejoin the queue.')
        stage.state,stage.queued_at,stage.session = 'Waiting',now_datetime(),None; _save(doc)
    elif action == 'withdraw':
        if stage.state in ('Called','In Progress'): frappe.throw('Release the patient from their room before withdrawing.')
        doc.status = 'Withdrawn'; _save(doc)
    else: frappe.throw('Unknown visit action.')
    return _visit_data(doc)


@frappe.whitelist()
def preview_reroute(clinic):
    _access(clinic); _require_manager(); result=[]
    for name in _all('Visit',filters={'clinic':clinic,'status':'Active'},pluck='name'):
        visit=_doc('Visit',name)
        route_name=_value('Route',{'clinic':clinic,'department':visit.department},'name')
        if not route_name: continue
        route=_doc('Route',route_name)
        if [(s.get('purpose_id') or s.stage,s.room) for s in visit.stages if s.state!='Completed'] == [(x['purpose_id'],x['room']) for x in _route_steps(route) if x['purpose_id'] not in {s.get('purpose_id') or s.stage for s in visit.stages if s.state=='Completed'}]: continue
        blocked=any(s.state in ('Called','In Progress') for s in visit.stages)
        room=_doc('Room',route.consultation_room)
        mismatch=not any(a.practitioner==visit.consultation_doctor for a in room.assignments)
        result.append({'visit':visit.name,'token':_visit_data(visit)['token'],'patient_name':visit.patient_name,
            'revision':visit.revision,'route_revision':route.revision,'blocked':blocked or mismatch,
            'reason':'Release the patient first' if blocked else 'Reassign consultation doctor first' if mismatch else '',
            'rooms':[x['room'] for x in _route_steps(route)]})
    return {'patients':result,'config_version':_value('Clinic',clinic,'config_version')}


@frappe.whitelist(methods=['POST'])
@_mutation
def apply_routes(clinic,request_id,expected_version,patients,reason):
    _require_manager(); _reason(reason)
    if str(_value('Clinic',clinic,'config_version'))!=str(expected_version): frappe.throw('Setup changed. Preview again.',frappe.TimestampMismatchError)
    changes=_json(patients,list)
    if not changes: frappe.throw('No waiting patients selected.')
    for row in changes:
        visit=_get('Visit',row['visit'],clinic);_version(visit,row.get('revision'))
        if visit.status!='Active':frappe.throw('A visit changed. Preview again.')
        _reroute_visit(visit,visit.department,visit.consultation_doctor)
    return {'updated':len(changes)}


def _day_state(clinic, date):
    visits = [_doc('Visit', n) for n in _all('Visit', filters={'clinic':clinic}, pluck='name')]
    selected = [v for v in visits if str(v.visit_date) == str(date)]
    unfinished = [v for v in visits if v.status == 'Active' and getdate(v.visit_date) <= date]
    sessions = [_doc('Session', n) for n in _all('Session', filters={'clinic':clinic}, pluck='name')]
    return selected, unfinished, sessions


@frappe.whitelist()
def daily_visits(clinic, date=None):
    _access(clinic)
    date = getdate(date)
    visits, unfinished, sessions = _day_state(clinic, date)
    counts = {'Total':len(visits), 'Waiting':0, 'Called':0, 'In Progress':0, 'On Hold':0, 'Completed':0, 'Withdrawn':0}
    for visit in visits:
        status = visit.stages[visit.stage_index].state if visit.status == 'Active' else visit.status
        counts[status] = counts.get(status, 0) + 1
    return {'date':str(date), 'visits':[_visit_data(v) for v in visits], 'counts':counts,
        'previous_active':sum(getdate(v.visit_date)<date for v in unfinished), 'open_rooms':len(sessions)}


@frappe.whitelist()
def preview_end_day(clinic, date):
    _access(clinic); _require_manager()
    date = getdate(date)
    if date > getdate(): frappe.throw('Cannot close a future day.')
    _, unfinished, sessions = _day_state(clinic, date)
    blocked = [v for v in unfinished if v.stages[v.stage_index].state in ('Called','In Progress')]
    return {'sequence':_value('Clinic',clinic,'event_sequence'), 'date':str(date),
        'visits':[_visit_data(v) for v in unfinished], 'blocked':[v.name for v in blocked],
        'rooms':[{'name':s.name,'room':s.room} for s in sessions if not s.current_visit]}


@frappe.whitelist(methods=['POST'])
@_mutation
def end_day(clinic, request_id, date, expected_sequence, reason, close_visits=0):
    _require_manager(); _reason(reason)
    preview = preview_end_day(clinic,date)
    if str(preview['sequence']) != str(expected_sequence):
        frappe.throw('The queue changed. Review the end-of-day preview again.',frappe.TimestampMismatchError)
    closed=[]; released=[]
    if cint(close_visits):
        for row in preview['visits']:
            visit=_get('Visit',row['name'],clinic)
            stage=visit.stages[visit.stage_index]
            if stage.state in ('Called','In Progress'):
                released.append({'visit':visit.name,'stage':stage.stage,'previous_state':stage.state,'session':stage.session})
                stage.state='On Hold';stage.released_at=now_datetime();stage.release_reason=reason
                stage.session=None
            visit.status='Withdrawn';visit.closed_at=now_datetime();visit.close_reason=reason
            _save(visit);closed.append(visit.name)
    closed_rooms=[]
    for session in [_doc('Session', n) for n in _all('Session',filters={'clinic':clinic},pluck='name')]:
        if session.current_visit and session.current_visit not in closed: continue
        session.current_visit=None;session.status='Closed';_save(session)
        closed_rooms.append(session.name)
    return {'closed_visits':closed,'closed_rooms':closed_rooms,'released_patients':released,
        'date':preview['date']}


@frappe.whitelist()
def snapshot(clinic):
    clinic_doc=_access(clinic); allowed=None if _reception() else _assigned_rooms(clinic)
    room_ids=_all('Room',filters={'clinic':clinic},pluck='name')
    rooms=[_room_data(_doc('Room',n)) for n in room_ids if allowed is None or n in allowed]
    practitioner_ids = list({a['practitioner'] for room in rooms for a in room['assignments']})
    fields = ['name', 'practitioner_name', 'image', 'department', 'designation']
    if frappe.get_meta('Healthcare Practitioner').has_field('sr_qualification'):
        fields.append('sr_qualification')
    profiles = {p.name: dict(p) for p in _all('Healthcare Practitioner',
        filters={'name': ['in', practitioner_ids]}, fields=fields)} if practitioner_ids else {}
    if practitioner_ids and frappe.get_meta('Healthcare Practitioner').has_field('sr_diseases'):
        for row in _all('SR Practitioner Disease', filters={'parent': ['in', practitioner_ids],
                'parenttype': 'Healthcare Practitioner', 'parentfield': 'sr_diseases'},
                fields=['parent', 'disease'], order_by='idx'):
            if row.parent in profiles:
                profiles[row.parent].setdefault('diseases', []).append(row.disease)
    for room in rooms:
        room['doctors'] = [profiles.get(a['practitioner'], {'name': a['practitioner'],
            'practitioner_name': a['practitioner']}) for a in room['assignments']]
    sessions=_all('Session',filters={'clinic':clinic,'status':['!=','Closed']}, fields=['name','room','controller','practitioner','status','current_visit','revision','call_sequence'])
    sessions=[s for s in sessions if allowed is None or s.room in allowed]; visits=[]
    for name in _all('Visit',filters={'clinic':clinic,'status':'Active'},pluck='name',order_by='creation'):
        doc=_doc('Visit',name)
        if allowed is None or doc.stages[doc.stage_index].room in allowed: visits.append(_visit_data(doc))
    return {'sequence':clinic_doc.event_sequence,'rooms':rooms,'sessions':sessions,'visits':visits,
        'reception':_reception(),'manager':_manager(),'user':frappe.session.user,
        'controllers': [u.name for u in get_setup(clinic)['users']] if _manager() else []}


@frappe.whitelist()
def get_visit(clinic, visit):
    _access(clinic); doc=_get('Visit',visit,clinic)
    if not _reception() and doc.stages[doc.stage_index].room not in _assigned_rooms(clinic): frappe.throw('Visit access denied.',frappe.PermissionError)
    return _visit_data(doc)


@frappe.whitelist()
def display_snapshot(clinic=None):
    if not clinic:
        clinics = bootstrap()['clinics']
        if len(clinics) != 1:
            frappe.throw('Select an authorized clinic before loading the waiting-room display.', frappe.PermissionError)
        clinic = clinics[0]['name']
    doc=_access(clinic,display=True); calls=[]
    for session in _all('Session',filters={'clinic':clinic,'status':['!=','Closed'],'current_visit':['is','set']},fields=['room','current_visit','call_sequence']):
        visit=_doc('Visit',session.current_visit); stage=visit.stages[visit.stage_index]
        if stage.state!='Called':continue
        room=_doc('Room',session.room)
        calls.append({'call_id':session.call_sequence,'token':_visit_data(visit)['token'],
            'visit_date':str(visit.visit_date),'patient_name':visit.patient_name,'room':room.room_number,'stage':stage.stage})
    return {'clinic_name':doc.clinic_name,'sequence':doc.event_sequence,'calls':calls,'date':str(getdate())}


def preserve_clinic_progress(doc, method=None):
    """Encounter resync must not prematurely complete a token-based visit."""
    if not doc.get('encounter_reference') or not frappe.get_meta(store.WORKFLOW).has_field('opd_visit_id'):
        return
    status = _value('Visit', {'source_doctype': 'Patient Encounter',
        'source_name': doc.encounter_reference}, 'status')
    if status == 'Active' and doc.appointment_status == 'Completed':
        doc.appointment_status = 'Confirmed'
    elif status == 'Completed':
        doc.appointment_status = 'Completed'


# Read-only patient viewer. Never expose password fields or bypass native record permissions.
def _viewer_record(doc, only=None):
    doc.check_permission('read')
    doc.apply_fieldlevel_read_permissions()
    excluded={'Section Break','Column Break','Tab Break','HTML','Button','Password','Fold','Heading'}
    def fields_for(record, meta, allow=None):
        rows=[]
        for field in meta.fields:
            if field.hidden or field.fieldtype in excluded or (allow is not None and field.fieldname not in allow): continue
            if field.permlevel and field.permlevel not in doc.get_permlevel_access('read'): continue
            value=record.get(field.fieldname)
            if field.fieldtype in ('Table','Table MultiSelect'):
                value=[fields_for(child,frappe.get_meta(field.options)) for child in (value or [])]
            rows.append({'fieldname':field.fieldname,'label':field.label or field.fieldname,'type':field.fieldtype,'value':value})
        return rows
    return {'doctype':doc.doctype,'name':doc.name,'fields':fields_for(doc,doc.meta,only)}


@frappe.whitelist()
def patient_overview(clinic,visit):
    _access(clinic);current=_get('Visit',visit,clinic)
    patient=_viewer_record(frappe.get_doc('Patient',current.patient))
    vitals=None
    if frappe.has_permission('Vital Signs','read'):
        rows=frappe.get_list('Vital Signs',filters={'patient':current.patient,'signs_date':getdate()},fields=['name'],order_by='signs_time desc, creation desc',limit_page_length=1)
        if rows:
            vitals=_viewer_record(frappe.get_doc('Vital Signs',rows[0].name),{'bp','bp_systolic','bp_diastolic','pulse','oxygen_saturation','spo2'})
    captured=current.get('vitals')
    if captured and getdate(captured['recorded_at'])==getdate():
        values=captured['values'];labels={'systolic':'Blood Pressure (systolic)','diastolic':'Blood Pressure (diastolic)','pulse':'Heart Rate / Pulse','spo2':'SpO2'}
        vitals={'fields':[{'label':labels.get(k,next((o['label'] for o in captured['options'] if o['id']==k),k)),'value':v} for k,v in values.items()]}
    return {'visit':_visit_data(current),'patient':patient,'vitals':vitals,'room_vitals':captured}


@frappe.whitelist()
def patient_records(clinic,visit,tab,offset=0):
    _access(clinic);current=_get('Visit',visit,clinic)
    frappe.get_doc('Patient',current.patient).check_permission('read')
    sources={
        'invoice':[('Sales Invoice',None)],
        'prescription':[('Patient Encounter',{'encounter_date','pe_appointment_date','practitioner','pe_practitioner','medical_department','drug_prescription','sr_homeopathy_drug_prescription','sr_allopathy_drug_prescription','lab_test_prescription','procedure_prescription','therapies','diet_chart'})],
        'history':[('Patient Encounter',None),('Vital Signs',None),('Patient Medical Record',None)],
        'dispatch':[('Shipment Tracking Shipment',{'sales_invoice','patient_encounter','shipkia_order_id','shipkia_awb_number','shipkia_tracking_id','normalized_status','shipkia_status','shipkia_status_detail','delivery_partner','delivery_location','shipkia_estimated_delivery','shipkia_delivered_on','events'})]
    }
    if tab not in sources: frappe.throw('Unknown patient details tab.')
    start=max(0,cint(offset));result=[];restricted=[];more=False
    for doctype,fields in sources[tab]:
        if not frappe.db.exists('DocType',doctype): continue
        if not frappe.has_permission(doctype,'read'):
            restricted.append(doctype);continue
        rows=frappe.get_list(doctype,filters={'patient':current.patient},fields=['name'],order_by='creation desc',limit_start=start,limit_page_length=21)
        more=more or len(rows)>20
        for row in rows[:20]:
            doc=frappe.get_doc(doctype,row.name)
            if not frappe.has_permission(doctype,'read',doc=doc):continue
            if doctype=='Patient Encounter':
                branch=appointment_branch(doctype,doc.name)
                if branch:
                    try:_access(branch)
                    except frappe.PermissionError:
                        frappe.clear_messages();continue
            if tab=='prescription' and not any(doc.get(f) for f in ('drug_prescription','sr_homeopathy_drug_prescription','sr_allopathy_drug_prescription','lab_test_prescription','procedure_prescription','therapies','diet_chart')):continue
            result.append(_viewer_record(doc,fields))
    return {'records':result,'restricted':restricted,'has_more':more,'next_offset':start+20}


DEFAULT_VITAL_OPTIONS = [
    {'id':'systolic','label':'Systolic blood pressure','unit':'mmHg'},
    {'id':'diastolic','label':'Diastolic blood pressure','unit':'mmHg'},
    {'id':'pulse','label':'Pulse','unit':'bpm'},
    {'id':'spo2','label':'SpO2','unit':'%'}]


def _vital_options(clinic):
    options=_doc('Clinic',clinic).get('vital_options')
    return DEFAULT_VITAL_OPTIONS if options is None else options


@frappe.whitelist()
def room_vitals(clinic,session):
    _access(clinic);room_session=_session(clinic,session)
    visit=_get('Visit',room_session.current_visit,clinic) if room_session.current_visit else None
    return {'options':_vital_options(clinic),'visit':visit.name if visit else None,
        'revision':visit.revision if visit else None,'values':visit.get('vitals',{}).get('values',{}) if visit else {},
        'session_revision':room_session.revision}


@frappe.whitelist(methods=['POST'])
@_mutation
def add_vital_option(clinic,request_id,session,label,unit=''):
    _session(clinic,session)
    label=str(label or '').strip();unit=str(unit or '').strip()
    options=[dict(x) for x in _vital_options(clinic)]
    if not label or len(label)>80 or len(unit)>30 or len(options)>=40: frappe.throw('Enter a measurement name (up to 80 characters) and unit (up to 30 characters). Maximum 40 measurements.')
    if any(x['label'].casefold()==label.casefold() for x in options):frappe.throw('This vital measurement already exists.')
    options.append({'id':frappe.generate_hash(length=16),'label':label,'unit':unit})
    doc=_doc('Clinic',clinic);doc.set('vital_options',options);doc.config_version=cint(doc.config_version)+1;_save(doc)
    return {'options':options}


@frappe.whitelist(methods=['POST'])
@_mutation
def update_room_vitals(clinic,request_id,session,expected_session_revision,visit,expected_revision,values):
    import math
    room_session=_session(clinic,session,expected_session_revision)
    if room_session.current_visit!=visit:frappe.throw('The patient in this room changed. Reopen the vitals form.')
    doc=_get('Visit',visit,clinic);_version(doc,expected_revision)
    stage=doc.stages[doc.stage_index]
    if doc.status!='Active' or stage.state!='In Progress' or stage.session!=session:
        frappe.throw('Start the patient in this room before recording vitals.')
    options=_vital_options(clinic);known={x['id']:x for x in options};submitted=_json(values,dict);clean={}
    for key,value in submitted.items():
        if key not in known:frappe.throw('Unknown vital measurement. Reopen the form.')
        if value is None or str(value).strip()=='':continue
        try:number=float(value)
        except (ValueError,TypeError):frappe.throw('Vital measurements must be numeric.')
        if not math.isfinite(number):frappe.throw('Enter finite numeric values.')
        clean[key]=number
    if not clean:frappe.throw('Enter at least one vital measurement.')
    stamp=now_datetime()
    encounter=doc.encounter or (doc.source_name if doc.source_doctype=='Patient Encounter' else None)
    if not encounter:
        company=frappe.defaults.get_global_default('company')
        if not company:frappe.throw('Set the default company before recording walk-in vitals.')
        # A walk-in clinical record only: no scheduling or external notification hooks.
        record=frappe.get_doc({'doctype':'Patient Encounter','patient':doc.patient,'patient_name':doc.patient_name,
            'company':company,'sr_encounter_type':'Followup','sr_encounter_place':'OPD','encounter_date':getdate(),
            'encounter_time':stamp.time(),'practitioner':doc.consultation_doctor,'medical_department':doc.department,'status':'Open'})
        record.db_insert();encounter=record.name;doc.encounter=encounter
    record=_doc('Patient Encounter',encounter)
    if record.patient!=doc.patient or record.docstatus==2:frappe.throw('The linked encounter is not available for this patient.')
    lines=[f"OPD Vitals | {stamp.strftime('%d %b %Y %H:%M:%S')} ({frappe.utils.get_system_timezone()}) | {frappe.session.user}"]
    lines += [f"{known[key]['label']}: {value:g} {known[key]['unit']}".rstrip() for key,value in clean.items()]
    notes=(record.get('sr_notes') or '').rstrip()
    frappe.db.set_value('Patient Encounter',encounter,'sr_notes',(notes+'\n\n' if notes else '')+'\n'.join(lines))
    doc.set('vitals',{'values':clean,'recorded_at':str(stamp),'recorded_by':frappe.session.user,'options':options})
    _save(doc)
    return {'visit':doc.name,'encounter':encounter,'recorded_at':str(stamp),'values':clean}


@frappe.whitelist(methods=['POST'])
@_mutation
def delete_vital_option(clinic,request_id,session,option):
    _session(clinic,session)
    options=[dict(x) for x in _vital_options(clinic)]
    if not any(x['id']==option for x in options):frappe.throw('This option has already been removed. Reopen Vitals.')
    doc=_doc('Clinic',clinic);doc.set('vital_options',[x for x in options if x['id']!=option]);doc.config_version=cint(doc.config_version)+1;_save(doc)
    return {'options':doc.vital_options}
