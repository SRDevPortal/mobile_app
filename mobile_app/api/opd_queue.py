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
    return frappe.db.get_values(doctype, filters, pluck or fields or ['name'],
        as_dict=not bool(pluck), pluck=bool(pluck), order_by=order_by,
        for_update=bool(frappe.flags.in_opd_mutation)) or []


def _sql(query, values=None, **kwargs):
    if frappe.flags.in_opd_mutation and not query.rstrip().endswith('FOR UPDATE'):
        query += ' FOR UPDATE'
    return frappe.db.sql(query, values, **kwargs)


def _roles():
    return set(frappe.get_roles()) if frappe.session.user != "Guest" else set()


def _manager():
    return frappe.session.user == "Administrator" or bool(_roles() & MANAGERS)


def _reception():
    return _manager() or "Appointment Receptionist" in _roles()


def _require_manager():
    if not _manager(): frappe.throw("Only an appointment manager can change OPD setup.", frappe.PermissionError)


def _require_reception():
    if not _reception(): frappe.throw("Reception or manager access is required.", frappe.PermissionError)


def _assigned_rooms(clinic):
    return {r['name'] for r in store.records('Room', {'clinic':clinic,'enabled':1})
        if any(a.user == frappe.session.user for a in r.assignments)}


def _access(clinic, display=False):
    doc = _doc("Clinic", clinic)
    if not doc.enabled or frappe.session.user == "Guest": frappe.throw("OPD clinic access denied.", frappe.PermissionError)
    if _reception() or (_roles() & STAFF and _assigned_rooms(clinic)): return doc
    if display and "OPD Display" in _roles() and any(x.user == frappe.session.user for x in doc.display_users): return doc
    frappe.throw("You are not assigned to this clinic.", frappe.PermissionError)


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


def _room_data(doc):
    return {k: doc.get(k) for k in ('name', 'room_number', 'room_label', 'purpose', 'enabled', 'revision')} | {
        'assignments': [{'user': a.user, 'practitioner': a.practitioner} for a in doc.assignments]}


def _route_data(doc):
    return {k: doc.get(k) for k in ('name', 'department', *ROOM_FIELDS, 'booking_labels', 'revision')}


def _visit_data(doc):
    result = {k: doc.get(k) for k in ('name', 'patient', 'patient_name', 'department', 'original_doctor',
        'consultation_doctor', 'visit_date', 'status', 'stage_index', 'priority', 'revision', 'encounter',
        'source_doctype', 'source_name', 'route_version')} | {
        'consultation_doctor_name': _value('Healthcare Practitioner', doc.consultation_doctor, 'practitioner_name') or doc.consultation_doctor,
        'token': str(doc.token_number).zfill(3), 'stages': [{k: s.get(k) for k in (
            'stage', 'room', 'state', 'queued_at', 'called_at', 'started_at', 'completed_at', 'session')} for s in doc.stages]}

    return frappe.parse_json(frappe.as_json(result))


@frappe.whitelist()
def bootstrap():
    if frappe.session.user == 'Guest': frappe.throw('Sign in to use OPD.', frappe.PermissionError)
    clinics = []
    for row in _all('Clinic', filters={'enabled': 1}, fields=['name', 'clinic_name']):
        try: _access(row.name, display=True); clinics.append(row)
        except frappe.PermissionError: frappe.clear_messages()
    return {'clinics': clinics, 'manager': _manager(), 'reception': _reception(), 'user': frappe.session.user,
        'display_only': 'OPD Display' in _roles() and not (_roles() & STAFF or _manager())}


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
        'rooms': rooms, 'routes': routes, 'display_users': [x.user for x in doc.display_users] if _manager() else [],
        'departments': _all('Medical Department', pluck='name', order_by='name'),
        'practitioners': _all('Healthcare Practitioner', filters={'status': 'Active'}, fields=['name', 'practitioner_name', 'user_id', 'department'], order_by='practitioner_name'),
        'genders': _all('Gender', pluck='name'),
        'users': [u for u in users if u.name == 'Administrator' or set(frappe.get_roles(u.name)) & (STAFF | MANAGERS)] if _manager() else [],
        'display_accounts': [u.name for u in users if 'OPD Display' in frappe.get_roles(u.name)] if _manager() else []}


def _check_assignments(assignments, purpose):
    if not assignments: frappe.throw('Assign at least one staff member to every enabled room.')
    seen = set()
    for a in assignments:
        user = str(a.get('user') or '')
        if user in seen: frappe.throw('A user may appear only once in a room.')
        seen.add(user)
        if not _value('User', user, 'enabled') or not set(frappe.get_roles(user)) & (STAFF | MANAGERS):
            if user != 'Administrator': frappe.throw('Choose an enabled OPD staff member.')
        practitioner = a.get('practitioner')
        if purpose == 'Doctor Consultation' and not practitioner: frappe.throw('Each consultation room assignment requires a practitioner.')
        if practitioner and _value('Healthcare Practitioner', practitioner, 'status') != 'Active': frappe.throw('Choose an active practitioner.')


@frappe.whitelist(methods=['POST'])
@_mutation
def save_setup(clinic, request_id, expected_version, rooms, routes, display_users=None):
    _require_manager(); clinic_doc = _doc('Clinic', clinic)
    if str(clinic_doc.config_version) != str(expected_version): frappe.throw('Setup changed. Refresh before saving.', frappe.TimestampMismatchError)
    room_inputs, route_inputs = _json(rooms, list), _json(routes, list)
    if len(room_inputs) > 100 or len(route_inputs) > 100: frappe.throw('Maximum 100 rooms and routes per clinic.')
    existing_rooms = set(_all('Room', filters={'clinic': clinic}, pluck='name'))
    if not existing_rooms.issubset({r.get('name') for r in room_inputs}): frappe.throw('Keep existing rooms in setup; disable them instead of deleting.')
    ids, room_docs, numbers, practitioner_rooms = {}, {}, set(), {}
    for row in room_inputs:
        number, purpose = str(row.get('room_number') or '').strip(), row.get('purpose')
        key = row.get('name') or row.get('client_id')
        if not key or key in ids or not number or len(number) > 30 or number.casefold() in numbers or purpose not in STAGES: frappe.throw('Each room needs a unique number, purpose and identifier.')
        numbers.add(number.casefold()); enabled = int(bool(cint(row.get('enabled')))); assignments = _json(row.get('assignments', []), list)
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
        doc.update({'clinic': clinic, 'room_number': number, 'room_label': str(row.get('room_label') or '')[:140], 'purpose': purpose, 'enabled': enabled})
        doc.set('assignments', assignments); _save(doc); ids[key] = doc.name; room_docs[doc.name] = doc
    departments, retained = set(), set()
    for row in route_inputs:
        department = row.get('department')
        if row.get('name') in retained: frappe.throw('A department route may appear only once.')
        if department in departments or not _exists('Medical Department', department): frappe.throw('Choose a distinct valid department for each route.')
        departments.add(department); values = {}
        for field, purpose in zip(ROOM_FIELDS, STAGES):
            room_id = ids.get(row.get(field))
            if not room_id or not room_docs[room_id].enabled or room_docs[room_id].purpose != purpose: frappe.throw(f'{department}: select an enabled {purpose} room.')
            values[field] = room_id
        doc = _get('Route', row['name'], clinic) if row.get('name') else store.new('Route')
        doc.update({'clinic': clinic, 'department': department, 'booking_labels': str(row.get('booking_labels') or '')[:1000], **values})
        _save(doc); retained.add(doc.name)
    for name in _all('Route', filters={'clinic': clinic}, pluck='name'):
        if name not in retained: store.delete('Route',name)
    for v in _all('Visit', filters={'clinic': clinic, 'status': 'Active'}, pluck='name'):
        visit = _doc('Visit', v); consultation = visit.stages[2]
        if consultation.state != 'Completed':
            room = room_docs.get(consultation.room)
            if room and not any(a.practitioner == visit.consultation_doctor for a in room.assignments): frappe.throw('Reassign waiting patients before removing their consultation doctor from a room.')
    if display_users is not None:
        viewers = _json(display_users, list)
        for user in viewers:
            if not _value('User', user, 'enabled') or 'OPD Display' not in frappe.get_roles(user): frappe.throw('Display accounts must be enabled and have the OPD Display role.')
        clinic_doc.set('display_users', [{'user': u} for u in sorted(set(viewers))])
    clinic_doc.config_version = cint(clinic_doc.config_version) + 1; clinic_doc.save(ignore_permissions=True)
    return {'config_version': clinic_doc.config_version}



def _route(clinic, department, doctor):
    name = _value('Route', {'clinic': clinic, 'department': department}, 'name')
    if not name: frappe.throw('Configure a complete room route for this department first.')
    route = _doc('Route', name)
    for f, purpose in zip(ROOM_FIELDS, STAGES):
        room = _get('Room', route.get(f), clinic)
        if not room.enabled or room.purpose != purpose: frappe.throw('The department route contains an unavailable room.')
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
    if source:
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
    active = _value('Visit', {'clinic': clinic, 'patient': patient, 'status': 'Active'}, 'name')
    if active:
        existing_visit = _get('Visit', active, clinic)
        if not source and existing_visit.department == department and existing_visit.consultation_doctor == doctor:
            return _visit_data(existing_visit)
        frappe.throw('This patient already has an active OPD token. Open their existing visit to change its department or doctor.')
    clinic_doc = _doc('Clinic', clinic); today = getdate()
    if str(clinic_doc.token_date or '') != str(today): clinic_doc.token_date, clinic_doc.last_token = today, 0
    clinic_doc.last_token = cint(clinic_doc.last_token) + 1; clinic_doc.save(ignore_permissions=True)
    source_key = f'{source.doctype}:{source.name}' if source else f'walkin:{request_id}:{frappe.session.user}'
    visit = _doc({'doctype': 'Visit', 'clinic': clinic, 'patient': patient,
        'patient_name': person.patient_name, 'department': department,
        'original_doctor': booked_doctor if source and _exists('Healthcare Practitioner', booked_doctor) else doctor,
        'consultation_doctor': doctor, 'visit_date': today, 'token_number': clinic_doc.last_token,
        'token_key': f'{clinic}:{today}:{clinic_doc.last_token}', 'source_key': hashlib.sha256(source_key.encode()).hexdigest(),
        'source_doctype': source.doctype if source else None, 'source_name': source.name if source else None,
        'encounter': source.name if source and source.doctype == 'Patient Encounter' else None,
        'status': 'Active', 'stage_index': 0, 'route_version': route.revision,
        'stages': [{'stage': stage, 'room': route.get(field), 'state': 'Waiting' if i == 0 else 'Not Ready',
                    'queued_at': now_datetime() if i == 0 else None} for i,(stage,field) in enumerate(zip(STAGES,ROOM_FIELDS))]})
    _save(visit)
    if source:
        previous = frappe.flags.opd_checkin; frappe.flags.opd_checkin = True
        try: cal.update_appointment(source.doctype, source.name, 'check_in', 'Approved', reason=reason)
        finally: frappe.flags.opd_checkin = previous
    return _visit_data(visit)


def _session(clinic, name, expected=None, require_controller=True):
    session = _get('Session', name, clinic)
    if session.status == 'Closed': frappe.throw('This room session has ended.')
    if require_controller and session.controller != frappe.session.user: frappe.throw('Only the active room controller can perform this action.', frappe.PermissionError)
    if expected is not None: _version(session, expected)
    return session


def _room_assignment(room, user, practitioner):
    if not room.enabled: frappe.throw('This room is disabled.')
    if not any(a.user == user and (room.purpose != 'Doctor Consultation' or a.practitioner == practitioner) for a in room.assignments):
        frappe.throw('Choose an assigned room and consultation doctor.', frappe.PermissionError)
    if not _value('User', user, 'enabled'): frappe.throw('This staff account is disabled.')


@frappe.whitelist(methods=['POST'])
@_mutation
def start_session(clinic, request_id, room, practitioner=None):
    doc = _get('Room', room, clinic); _room_assignment(doc, frappe.session.user, practitioner)
    if _exists('Session', {'clinic': clinic, 'room': room, 'status': ['!=','Closed']}): frappe.throw('This room already has an active controller.')
    if _exists('Session', {'clinic': clinic, 'controller': frappe.session.user, 'status': ['!=','Closed']}): frappe.throw('End your current room session before selecting another room.')
    if practitioner and _exists('Session', {'clinic': clinic, 'practitioner': practitioner, 'status': ['!=','Closed']}): frappe.throw('This doctor is already working in another room.')
    session = _doc({'doctype':'Session','clinic':clinic,'room':room,
        'controller':frappe.session.user,'practitioner':practitioner,'status':'Available','started_at':now_datetime()})
    _save(session); return {'session':session.name, 'revision':session.revision}


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
        if _exists('Session', {'clinic':clinic,'controller':controller,'status':['!=','Closed'],'name':['!=',session]}): frappe.throw('The new controller already occupies another room.')
        if room_session.current_visit and practitioner != room_session.practitioner: frappe.throw('Release the current patient before changing the consultation doctor.')
        room_session.controller, room_session.practitioner = controller, practitioner
    elif action in ('pause','resume'): room_session.status = 'Paused' if action == 'pause' else 'Available'
    elif action == 'end':
        if room_session.current_visit: frappe.throw('Complete or release the current patient first.')
        room_session.status, room_session.ended_at = 'Closed', now_datetime()
    elif action == 'call_next':
        if room_session.status != 'Available' or room_session.current_visit: frappe.throw('The room must be available and empty before calling the next patient.')
        candidates=[]
        for item in store.records('Visit', {'clinic':clinic,'status':'Active'}):
            stage=item.stages[item.stage_index]
            if stage.room==room.name and stage.state=='Waiting' and (room.purpose!='Doctor Consultation' or item.consultation_doctor==room_session.practitioner):
                candidates.append(item)
        candidates.sort(key=lambda v:(-cint(v.priority),str(v.stages[v.stage_index].queued_at),str(v.creation),v.name))
        rows=[v.name for v in candidates[:1]]
        if not rows: frappe.throw('No eligible patients are waiting for this room.')
        visit = _get('Visit',rows[0],clinic); stage = visit.stages[visit.stage_index]
        audit.update({'affected_visit': visit.name, 'stage': stage.stage, 'from_state': stage.state, 'to_state': 'Called'})
        stage.state, stage.called_at, stage.session = 'Called', now_datetime(), room_session.name
        room_session.current_visit = visit.name
        room_session.call_sequence = cint(_value('Clinic',clinic,'event_sequence')) + 1
        _save(visit)
    elif action in ('recall','start','complete','absent','release'):
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
        elif action == 'complete':
            if stage.state != 'In Progress': frappe.throw('Start the patient\'s stage before completing it.')
            stage.state, stage.completed_at = 'Completed', now_datetime(); room_session.current_visit = None
            if visit.stage_index == 2: visit.status, visit.completed_at = 'Completed', now_datetime()
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
    else: frappe.throw('Unknown room action.')
    _save(room_session); return {'session':session, 'revision':room_session.revision, 'visit':room_session.current_visit, 'audit':audit}



def _reroute_visit(visit, department, doctor):
    route = _route(visit.clinic,department,doctor)
    if any(s.state in ('Called','In Progress') for s in visit.stages): frappe.throw('Release the called/in-progress patient before rerouting.')
    for stage,field in zip(visit.stages,ROOM_FIELDS):
        if stage.state != 'Completed': stage.room = route.get(field)
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
        if not any(s.state!='Completed' and s.room!=route.get(f) for s,f in zip(visit.stages,ROOM_FIELDS)): continue
        blocked=any(s.state in ('Called','In Progress') for s in visit.stages)
        room=_doc('Room',route.consultation_room)
        mismatch=not any(a.practitioner==visit.consultation_doctor for a in room.assignments)
        result.append({'visit':visit.name,'token':str(visit.token_number).zfill(3),'patient_name':visit.patient_name,
            'revision':visit.revision,'route_revision':route.revision,'blocked':blocked or mismatch,
            'reason':'Release the patient first' if blocked else 'Reassign consultation doctor first' if mismatch else '',
            'rooms':[route.get(f) for f in ROOM_FIELDS]})
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


@frappe.whitelist()
def snapshot(clinic):
    clinic_doc=_access(clinic); allowed=None if _reception() else _assigned_rooms(clinic)
    room_ids=_all('Room',filters={'clinic':clinic},pluck='name')
    rooms=[_room_data(_doc('Room',n)) for n in room_ids if allowed is None or n in allowed]
    sessions=_all('Session',filters={'clinic':clinic,'status':['!=','Closed']}, fields=['name','room','controller','practitioner','status','current_visit','revision','call_sequence'])
    sessions=[s for s in sessions if allowed is None or s.room in allowed]; visits=[]
    for name in _all('Visit',filters={'clinic':clinic,'status':'Active'},pluck='name',order_by='creation'):
        doc=_doc('Visit',name)
        if allowed is None or doc.stages[doc.stage_index].room in allowed: visits.append(_visit_data(doc))
    return {'sequence':clinic_doc.event_sequence,'rooms':rooms,'sessions':sessions,'visits':visits,
        'reception':_reception(),'manager':_manager(),'user':frappe.session.user}


@frappe.whitelist()
def get_visit(clinic, visit):
    _access(clinic); doc=_get('Visit',visit,clinic)
    if not _reception() and doc.stages[doc.stage_index].room not in _assigned_rooms(clinic): frappe.throw('Visit access denied.',frappe.PermissionError)
    return _visit_data(doc)


@frappe.whitelist()
def display_snapshot(clinic):
    doc=_access(clinic,display=True); calls=[]
    for session in _all('Session',filters={'clinic':clinic,'status':['!=','Closed'],'current_visit':['is','set']},fields=['room','current_visit','call_sequence']):
        visit=_doc('Visit',session.current_visit); stage=visit.stages[visit.stage_index]
        if stage.state!='Called':continue
        room=_doc('Room',session.room)
        calls.append({'call_id':session.call_sequence,'token':str(visit.token_number).zfill(3),
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
