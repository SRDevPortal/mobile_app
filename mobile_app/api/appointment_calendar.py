"""Permission-scoped appointment calendar and durable clinic queue transitions."""

import hashlib
from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import getdate, get_time, now_datetime, cint

WORKFLOW = "Mobile Appointment Workflow"
SOURCES = ("Patient Encounter", "Mobile App Appointment")
AGENT_ROLES = {"Agent", "Appointment Agent"}
DOCTOR_ROLES = {"Physician", "Healthcare Practitioner", "Mobile App Doctor"}
MANAGER_ROLES = {"System Manager", "Appointment Manager"}
RECEPTION_ROLE = "Appointment Receptionist"
RECEPTION_ROLES = {RECEPTION_ROLE, "OPD Staff"}
TRANSITIONS = {
    "approve": (("Pending",), "Approved"),
    "cancel": (("Pending", "Approved", "Checked In"), "Cancelled"),
    "complete": (("Checked In",), "Completed"),
    "check_in": (("Approved",), "Checked In"),
}
LEGACY_STATUSES = {"Rejected": "Cancelled", "In Consultation": "Checked In", "Completed": "Completed"}
DOCTOR_CHANGE_ROLES = {"Appointment Agent", "Appointment Manager"}
APPOINTMENT_STATUS_FIELD = "custom_appointment_status"
RESCHEDULE_DATE_FIELD = "custom_reschedule_date"
_CALENDAR_RESCHEDULE = object()



def _context():
    user = frappe.session.user
    roles = set(frappe.get_roles(user)) if user != "Guest" else set()
    manager = user == "Administrator" or bool(roles.intersection(MANAGER_ROLES))
    if not manager and not roles.intersection(AGENT_ROLES | DOCTOR_ROLES | RECEPTION_ROLES):
        frappe.throw(_("You do not have access to the appointment calendar."), frappe.PermissionError)
    return user, roles, manager


def _key(doctype, name):
    return hashlib.sha256(f"{doctype}:{name}".encode()).hexdigest()[:32]


def _workflow(doctype, name):
    return frappe.db.get_value(WORKFLOW, _key(doctype, name), "*", as_dict=True, for_update=bool(frappe.flags.in_opd_mutation or frappe.flags.opd_checkin)) or frappe._dict()


def _source(doctype, name, lock=False):
    if doctype not in SOURCES:
        frappe.throw(_("Unsupported appointment source."))
    if lock:
        # Lock the source even before a workflow exists, serializing first-time decisions.
        frappe.db.sql(f"SELECT name FROM `tab{doctype}` WHERE name=%s FOR UPDATE", (name,))
    doc = frappe.get_doc(doctype, name, for_update=lock)
    if doctype == "Patient Encounter" and doc.get("sr_encounter_type") != "Appointment":
        frappe.throw(_("This encounter is not an appointment."))
    return doc


def _doctor(doc):
    practitioner = (doc.get("pe_practitioner") or doc.get("practitioner")) if doc.doctype == "Patient Encounter" else doc.get("practitioner_id")
    user = doc.get("doctor_user")
    name = doc.get("doctor_name") or user or _("Unassigned doctor")
    if not practitioner and doc.doctype != "Patient Encounter":
        # Legacy mobile bookings have no Link. Resolve only unique identities;
        # never collapse different practitioners merely because names overlap.
        roster = getattr(frappe.local, "appointment_calendar_identity_roster", None)
        if roster is None:
            roster = frappe.local.appointment_calendar_identity_roster = frappe.get_all(
                "Healthcare Practitioner", fields=["name", "practitioner_name", "user_id"], limit_page_length=0)
        matches = [p for p in roster if user and p.user_id == user]
        if not matches and doc.get("doctor_name"):
            normalize = lambda value: " ".join((value or "").casefold().split())
            matches = [p for p in roster if normalize(p.practitioner_name) == normalize(doc.doctor_name)]
        if len(matches) == 1:
            practitioner = matches[0].name
    if practitioner:
        cache = getattr(frappe.local, "appointment_calendar_doctors", None)
        if cache is None:
            cache = frappe.local.appointment_calendar_doctors = {}
        if practitioner not in cache:
            cache[practitioner] = frappe.db.get_value("Healthcare Practitioner", practitioner,
                ["practitioner_name", "user_id"], as_dict=True) or {}
        data = cache[practitioner]
        return practitioner, data.get("practitioner_name") or name, user or data.get("user_id")
    if doc.doctype == "Patient Encounter":
        return "unassigned", _("Unassigned doctor"), None
    return user or doc.get("doctor_name") or "unassigned", name, user


def _agent(doc, workflow):
    assigned = workflow.get("assigned_agent") or doc.get("assigned_agent")
    creator = doc.get("created_by_agent")
    if not assigned and creator and set(frappe.get_roles(creator)).intersection(AGENT_ROLES):
        assigned = creator
    return assigned


def _can_read_original(doc, workflow, context):
    user, roles, manager = context
    if manager:
        return True
    if roles.intersection(RECEPTION_ROLES):
        return _status(doc, workflow) in ("Approved", "Checked In", "Completed")
    agent = _agent(doc, workflow)
    if roles.intersection(AGENT_ROLES) and (not agent or agent == user):
        return True
    return bool(roles.intersection(DOCTOR_ROLES) and _doctor(doc)[2] == user)


def _can_read(doc, workflow, context):
    if not _can_read_original(doc, workflow, context): return False
    from mobile_app.api.opd_queue import appointment_branch, _branch_admin
    from mobile_app import opd_store
    branch=appointment_branch(doc.doctype,doc.name)
    if branch and not _branch_admin():
        record=opd_store.get('Clinic',branch)
        if record.get('members') is not None and frappe.session.user not in record.members: return False
    return True


def _status(doc, workflow):
    # Clinical source fields never represent an appointment workflow decision.
    return LEGACY_STATUSES.get(workflow.get("workflow_status"), workflow.get("workflow_status")) or "Pending"


def _is_future_appointment(doc):
    date = doc.get("pe_appointment_date") or doc.get("appointment_date") or doc.get("encounter_date")
    return bool(date and getdate(date) > now_datetime().date())


def _require_current_or_past(doc):
    if _is_future_appointment(doc):
        frappe.throw(_("Future appointments can only be opened or rescheduled. Other actions are available on the appointment date."))


def _actions(doc, workflow, context):
    user, roles, manager = context
    status = _status(doc, workflow)
    if roles.intersection(RECEPTION_ROLES) and not manager:
        return ["check_in"] if status == "Approved" and not _is_future_appointment(doc) else []
    agent = _agent(doc, workflow)
    can_manage = manager or bool(roles.intersection(AGENT_ROLES) and agent == user)
    actions = []
    if not agent and roles.intersection(AGENT_ROLES) and status not in ("Checked In", "Completed", "Cancelled"):
        actions.append("claim")
    online = doc.get("sr_encounter_place") == "Online" if doc.doctype == "Patient Encounter" else bool(doc.get("is_online"))
    for action, (before, _) in TRANSITIONS.items():
        if status == "Checked In" and not online:
            continue
        if status in before and (bool(roles.intersection(RECEPTION_ROLES)) if action == "check_in" else can_manage):
            actions.append(action)
    if can_manage and status in ("Pending", "Approved") and not cint(doc.get("docstatus")):
        actions.append("reschedule")
    return [action for action in actions if action == "reschedule"] if _is_future_appointment(doc) else actions


def _serialize(doc, workflow, context, details=False):
    doctor_id, doctor_name, doctor_user = _doctor(doc)
    encounter = doc.name if doc.doctype == "Patient Encounter" else doc.get("patient_encounter")
    date = doc.get("pe_appointment_date") or doc.get("appointment_date") or doc.get("encounter_date")
    time = doc.get("pe_appointment_time") or doc.get("appointment_time") or doc.get("encounter_time")
    online = doc.get("sr_encounter_place") == "Online" if doc.doctype == "Patient Encounter" else bool(doc.get("is_online"))
    patient_name = doc.get("patient_name")
    phone = doc.get("sr_pe_mobile") or doc.get("mobile_number")
    if details and doc.doctype == "Patient Encounter" and doc.get("patient") and (not patient_name or not phone):
        patient = frappe.db.get_value("Patient", doc.patient, ["patient_name", "mobile"], as_dict=True) or {}
        patient_name = patient_name or patient.get("patient_name")
        phone = phone or patient.get("mobile")
    out = dict(id=f"{doc.doctype}:{doc.name}", source_doctype=doc.doctype, name=doc.name,
               patient_name=patient_name or doc.get("patient") or _("Unnamed patient"),
               phone=phone, patient=doc.get("patient") or doc.get("mobile_app_user"),
               booking_id=doc.get("booking_id"), email=doc.get("email"),
               doctor_id=doctor_id, doctor_name=doctor_name,
               date=str(date) if date else None, time=get_time(time or "09:00:00").strftime("%H:%M:%S"),
               duration=cint(workflow.get("duration_minutes")) or 30,
               status=_status(doc, workflow), online=online, assigned_agent=_agent(doc, workflow),
               is_future=_is_future_appointment(doc),
               can_change_doctor=bool(not _is_future_appointment(doc) and online and context[1].intersection(DOCTOR_CHANGE_ROLES) and
                   _status(doc, workflow) == "Pending"),
               actions=_actions(doc, workflow, context), encounter=encounter,
               checked_in_at=workflow.get("checked_in_at"),
               completed_at=workflow.get("checked_out_at"), completed_by=workflow.get("completed_by"))
    from mobile_app.api.opd_queue import appointment_branch
    from mobile_app import opd_store
    out['clinic']=None if online else appointment_branch(doc.doctype,doc.name)
    out['clinic_name']=opd_store.value('Clinic',out['clinic'],'clinic_name') if out['clinic'] else None
    if details:
        from mobile_app.api.opd_queue import find_visit
        visit = find_visit(doc.doctype, doc.name)
        from mobile_app import opd_store
        out['opd_visit'] = opd_store.value('Visit', visit,
            ['name', 'clinic', 'token_number', 'department', 'status', 'stage_index'], as_dict=True) if visit else None
    if details:
        out.update(notes=doc.get("sr_notes") or doc.get("page_url_disease") or "",
                   meet_link=doc.get("google_meet_link"), reason=workflow.get("decision_reason"),
                   decision_by=workflow.get("decision_by"), decision_at=workflow.get("decision_at"),
                   can_open_source=bool((context[2] or RECEPTION_ROLE not in context[1]) and
                       frappe.has_permission(doc.doctype, "read", doc=doc)),
                   history=frappe.get_all("Comment", filters={"reference_doctype": WORKFLOW,
                       "reference_name": _key(doc.doctype, doc.name), "comment_type": "Comment"},
                       fields=["content", "creation", "comment_by"], order_by="creation desc", limit=20))
    return out


CALENDAR_QUERY_TIMEOUT = 8


def _calendar_sql(query, values=(), as_dict=True):
    """Stop expensive MariaDB reads even after the HTTP client has disconnected.

    A browser timeout/abort does not cancel a database query. The statement-local
    limit deliberately leaves other requests, writes and session settings alone.
    """
    if not query.lstrip().upper().startswith("SELECT "):
        raise ValueError("Only calendar SELECT statements may use this helper")
    if frappe.db.db_type == "mariadb":
        query = "SET STATEMENT max_statement_time = %s FOR " + query
        values = (CALENDAR_QUERY_TIMEOUT, *values)
    try:
        return frappe.db.sql(query, values, as_dict=as_dict)
    except frappe.db.OperationalError as exc:
        # Some Frappe/PyMySQL combinations do not classify MariaDB error 1969.
        if exc.args and exc.args[0] == 1969:
            raise frappe.QueryTimeoutError("Appointment query exceeded the database time limit") from exc
        raise


def _calendar_dates(start, end):
    start, end = getdate(start), getdate(end)
    if not 0 < (end - start).days <= 62:
        frappe.throw(_("Choose a date range between 1 and 62 days."))
    return start, end


def _calendar_revisions(start, end, context):
    """Small change tokens, no patient payload or per-appointment document loading.

    Include linked records so edits, moved/deleted bookings, workflow decisions and
    doctor/patient changes invalidate the affected date section. Read before the
    corresponding payload so a concurrent write is seen on the next check.
    """
    stamps = []
    if frappe.db.exists("DocType", "Patient Encounter") and frappe.get_meta("Patient Encounter").has_field("sr_encounter_type"):
        stamps.extend(_calendar_sql("""SELECT 'Patient Encounter' AS source, e.name,
            COALESCE(e.pe_appointment_date, e.encounter_date) AS date,
            e.modified, e.docstatus, e.status, e.sr_encounter_status,
            w.workflow_status, w.assigned_agent, w.duration_minutes, w.checked_in_at, w.checked_out_at,
            p.modified AS patient_modified, h.modified AS doctor_modified
            FROM `tabPatient Encounter` e
            LEFT JOIN `tabMobile Appointment Workflow` w
                ON w.name=LEFT(SHA2(CONCAT('Patient Encounter:', e.name), 256), 32)
            LEFT JOIN `tabPatient` p ON p.name=e.patient
            LEFT JOIN `tabHealthcare Practitioner` h ON h.name=COALESCE(NULLIF(e.pe_practitioner, ''), e.practitioner)
            WHERE e.sr_encounter_type='Appointment'
                AND ((e.pe_appointment_date >= %s AND e.pe_appointment_date < %s)
                    OR (e.pe_appointment_date IS NULL AND e.encounter_date >= %s AND e.encounter_date < %s))
            """, (start, end, start, end), as_dict=True))
    stamps.extend(_calendar_sql("""SELECT 'Mobile App Appointment' AS source, a.name,
        a.appointment_date AS date, a.modified, a.docstatus, a.status,
        w.workflow_status, w.assigned_agent, w.duration_minutes, w.checked_in_at, w.checked_out_at
        FROM `tabMobile App Appointment` a
        LEFT JOIN `tabMobile Appointment Workflow` w
            ON w.name=LEFT(SHA2(CONCAT('Mobile App Appointment:', a.name), 256), 32)
        WHERE a.appointment_date >= %s AND a.appointment_date < %s
            AND COALESCE(a.patient_encounter, '')=''""", (start, end), as_dict=True))
    roster = frappe.get_all("Healthcare Practitioner", fields=["name", "modified", "status", "user_id"],
                            order_by="name", limit_page_length=0)
    scope = (context[0], sorted(context[1]), context[2], roster, frappe.utils.get_system_timezone())
    chunks = []
    while start < end:
        stop = min(start + timedelta(days=7), end)
        values = sorted((row for row in stamps if start <= getdate(row.date) < stop),
                        key=lambda row: (row.source, row.name))
        revision = hashlib.sha256(repr((scope, values)).encode()).hexdigest()
        chunks.append({"start": str(start), "end": str(stop), "revision": revision})
        start = stop
    return chunks


@frappe.whitelist()
def get_calendar_changes(start, end):
    """Permission-gated change manifest; clients fetch only changed date sections."""
    context = _context()
    start, end = _calendar_dates(start, end)
    return {"chunks": _calendar_revisions(start, end, context)}


@frappe.whitelist()
def get_calendar(start, end):
    """End-exclusive date range. Never return patient data outside the caller's scope."""
    context = _context()
    start, end = _calendar_dates(start, end)
    revisions = _calendar_revisions(start, end, context)
    rows = []
    if frappe.db.exists("DocType", "Patient Encounter") and frappe.get_meta("Patient Encounter").has_field("sr_encounter_type"):
        encounters = _calendar_sql("""SELECT name, patient_name, patient, sr_pe_mobile, sr_encounter_type,
            sr_encounter_place, pe_appointment_date, encounter_date, pe_appointment_time,
            encounter_time, pe_practitioner, practitioner, status, sr_encounter_status, docstatus, created_by_agent
            FROM `tabPatient Encounter`
            WHERE sr_encounter_type='Appointment'
            AND ((pe_appointment_date >= %s AND pe_appointment_date < %s)
                OR (pe_appointment_date IS NULL AND encounter_date >= %s AND encounter_date < %s))
            ORDER BY COALESCE(pe_appointment_date, encounter_date) LIMIT 2001""", (start, end, start, end), as_dict=True)
        missing_patients = {row.patient for row in encounters if row.patient and (not row.patient_name or not row.sr_pe_mobile)}
        patients = {p.name: p for p in frappe.get_all("Patient", filters={"name": ["in", list(missing_patients)]},
            fields=["name", "patient_name", "mobile"], limit_page_length=0)} if missing_patients else {}
        for row in encounters:
            row.doctype = "Patient Encounter"
            patient = patients.get(row.patient, {})
            row.patient_name = row.patient_name or patient.get("patient_name")
            row.sr_pe_mobile = row.sr_pe_mobile or patient.get("mobile")
            rows.append(row)
    mobile = frappe.get_all("Mobile App Appointment", filters={"appointment_date": ["between", [start, end - timedelta(days=1)]]},
        fields=["name", "patient_encounter", "patient_name", "doctor_user", "doctor_name", "practitioner_id",
                "mobile_number", "email", "mobile_app_user", "booking_id",
                "appointment_date", "appointment_time", "status", "docstatus", "is_online", "assigned_agent"],
        limit_page_length=2001)
    # A linked encounter is the canonical calendar entry; never show both copies.
    for row in mobile:
        if not row.patient_encounter:
            row.doctype = "Mobile App Appointment"
            rows.append(row)
    if len(rows) > 2000:
        frappe.throw(_("Too many appointments. Select a shorter date range."))
    result = []
    keys = [_key(row.doctype, row.name) for row in rows]
    workflows = {row.name: row for row in frappe.get_all(WORKFLOW,
        filters={"name": ["in", keys]}, fields=["*"], limit_page_length=0)} if keys else {}
    # Resolve practitioners in one query, including inactive doctors on older bookings.
    practitioner_ids = {row.get("pe_practitioner") or row.get("practitioner") or row.get("practitioner_id")
                        for row in rows}
    practitioner_ids.discard(None)
    practitioner_ids.discard("")
    frappe.local.appointment_calendar_doctors = {name: {} for name in practitioner_ids}
    if practitioner_ids:
        frappe.local.appointment_calendar_doctors.update({p.name: p for p in frappe.get_all(
            "Healthcare Practitioner", filters={"name": ["in", list(practitioner_ids)]},
            fields=["name", "practitioner_name", "user_id"], limit_page_length=0)})
    for doc in rows:
        workflow = workflows.get(_key(doc.doctype, doc.name), frappe._dict())
        if _can_read(doc, workflow, context):
            result.append(_serialize(doc, workflow, context))
    doctor_filters = {"status": "Active"}
    if not context[2] and not context[1].intersection(AGENT_ROLES | RECEPTION_ROLES):
        doctor_filters["user_id"] = context[0]
    profile_fields = ["name as id", "practitioner_name as name"]
    meta = frappe.get_meta("Healthcare Practitioner")
    for field in ("image", "department", "sr_qualification", "custom_accept_online_appointments"):
        if meta.has_field(field):
            profile_fields.append(field)
    doctors = frappe.get_all("Healthcare Practitioner", filters=doctor_filters,
        fields=profile_fields, order_by="practitioner_name", limit_page_length=0)
    disease_field = meta.get_field("sr_diseases")
    disease_tags = {}
    if doctors and disease_field and disease_field.options:
        for row in frappe.get_all(disease_field.options,
                filters={"parent": ["in", [d.id for d in doctors]],
                         "parenttype": "Healthcare Practitioner", "parentfield": "sr_diseases"},
                fields=["parent", "disease"], order_by="idx", limit_page_length=0):
            if row.disease:
                disease_tags.setdefault(row.parent, []).append(row.disease)
    for doctor in doctors:
        doctor["diseases"] = disease_tags.get(doctor.id, [])
    return {"appointments": result, "doctors": doctors, "revisions": revisions,
            "can_assign": context[2], "user": context[0],
            "reception_only": bool(context[1].intersection(RECEPTION_ROLES)) and not context[2],
            "timezone": frappe.utils.get_system_timezone()}


@frappe.whitelist()
def get_appointment(doctype, name):
    context = _context()
    doc, workflow = _source(doctype, name), _workflow(doctype, name)
    if not _can_read(doc, workflow, context):
        frappe.throw(_("This appointment is assigned to another team member."), frappe.PermissionError)
    return _serialize(doc, workflow, context, details=True)


@frappe.whitelist(methods=["POST"])
def update_appointment(doctype, name, action, expected_status, reason=None, agent=None,
                       clinic=None, department=None, doctor=None, patient=None, request_id=None, route_id=None,
                       reschedule_date=None, expected_date=None):
    if action == "check_in" and not frappe.flags.opd_checkin:
        source = _source(doctype, name)
        online = source.get("sr_encounter_place") == "Online" if doctype == "Patient Encounter" else bool(source.get("is_online"))
        if not online:
            if not all((clinic, route_id or department, doctor, request_id)):
                frappe.throw("Use OPD check-in to confirm the room route, doctor and patient before issuing a token.")
            from mobile_app.api.opd_queue import check_in
            return check_in(clinic, request_id, department, doctor, patient=patient, source_doctype=doctype,
                            source_name=name, expected_status=expected_status, reason=reason, route_id=route_id)
    action = "cancel" if action == "reject" else action  # Compatibility with already-open older clients.
    context = _context()
    doc = _source(doctype, name, lock=True)
    current = _workflow(doctype, name)
    if not _can_read(doc, current, context):
        frappe.throw(_("You cannot update this appointment."), frappe.PermissionError)
    before = _status(doc, current)
    if before != expected_status:
        frappe.throw(_("The appointment changed. Refresh and try again."), frappe.TimestampMismatchError)
    if action != "reschedule":
        _require_current_or_past(doc)
    if action == "assign":
        if not context[2]:
            frappe.throw(_("Only an Appointment Manager or System Manager can assign agents."), frappe.PermissionError)
        if before in ("Cancelled", "Checked In", "Completed"):
            frappe.throw(_("This appointment is already closed."))
        if not agent or not frappe.db.get_value("User", agent, "enabled") or not set(frappe.get_roles(agent)).intersection(AGENT_ROLES | MANAGER_ROLES):
            frappe.throw(_("Choose an enabled Agent, Appointment Agent, Appointment Manager or System Manager."))
    elif action not in _actions(doc, current, context):
        frappe.throw(_("This action is not allowed for the current appointment state or your role."), frappe.PermissionError)
    if action == "cancel" and not (reason or "").strip():
        frappe.throw(_("A reason is required to cancel an appointment."))
    if len(reason or "") > 1000:
        frappe.throw(_("Keep the reason under 1,000 characters."))
    state = frappe.get_doc(WORKFLOW, current.name) if current else frappe.new_doc(WORKFLOW)
    if not current:
        state.name = _key(doctype, name)
        state.appointment_key = state.name
        state.reference_doctype = doctype
        state.reference_name = name
        state.workflow_status = before
        state.assigned_agent = _agent(doc, current)
    now = now_datetime()
    if action == "reschedule":
        old_date = _reschedule(doc, reschedule_date, expected_date)
    if action in ("assign", "claim"):
        state.assigned_agent = agent if action == "assign" else context[0]
    elif action != "reschedule":
        state.workflow_status = TRANSITIONS[action][1]
        if action in ("approve", "cancel"):
            state.decision_by, state.decision_at = context[0], now
            state.decision_reason = (reason or "").strip()
        timestamp = {"check_in": "checked_in_at", "complete": "checked_out_at"}.get(action)
        if action == "complete":
            state.completed_by = context[0]
        if timestamp:
            state.set(timestamp, now)
    state.save(ignore_permissions=True)
    _sync_encounter_status(doc, state.workflow_status)
    message = f"{before} → {state.workflow_status}" if action not in ("assign", "claim") else f"Assigned to {state.assigned_agent}"
    if action == "reschedule":
        message = f"Appointment rescheduled: {old_date} to {getdate(reschedule_date)} (time unchanged)"
    if reason:
        message += f". Reason: {reason.strip()}"
    state.add_comment("Comment", text=frappe.utils.escape_html(message))
    # The workflow's on_change hook invalidates other sessions after commit.
    # This legacy event remains available for existing clients.
    frappe.publish_realtime("appointment_calendar_updated", user=context[0], after_commit=True)
    return _serialize(doc, state, context, details=True)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def agent_query(doctype, txt, searchfield, start, page_len, filters=None):
    """Let calendar managers select responsible staff without general User access."""
    if not _context()[2] or doctype != "User":
        frappe.throw(_("Only appointment managers can select responsible staff."), frappe.PermissionError)
    return frappe.db.sql("""SELECT u.name, u.full_name FROM `tabUser` u
        WHERE u.enabled=1 AND u.user_type='System User'
        AND (u.name='Administrator' OR EXISTS (
            SELECT 1 FROM `tabHas Role` r WHERE r.parent=u.name
            AND r.parenttype='User' AND r.role IN %(roles)s))
        AND (u.name LIKE %(text)s OR u.full_name LIKE %(text)s)
        ORDER BY u.full_name, u.name LIMIT %(start)s, %(length)s""", {
            "roles": tuple(AGENT_ROLES | MANAGER_ROLES), "text": f"%{txt or ''}%",
            "start": max(0, cint(start)), "length": min(50, max(1, cint(page_len))),
        })


def _sync_clinic_status(doc, status, *, skip_invalid_links=False):
    """Update the linked scheduling record in the same transaction as the queue.

    Clinical/billing encounter statuses are separate. A status-only database update
    avoids reverse-sync hooks and booking-time validations on an existing visit.
    Historical setup may skip stale links without changing their ownership.
    """
    if doc.doctype != "Patient Encounter" or not frappe.db.exists("DocType", "Clinic Appointment"):
        return
    if status == "Checked In":
        from mobile_app.api.opd_queue import find_visit
        visit = find_visit(doc.doctype, doc.name)
        from mobile_app import opd_store
        if visit and opd_store.value("Visit", visit, "status") != "Completed":
            return  # Check-in is not the end of an OPD consultation.
    mapped = {"Pending": "Draft", "Approved": "Confirmed", "Checked In": "Completed",
              "Cancelled": "Cancelled"}.get(status)
    if not mapped:
        return
    linked = doc.get("encounter_reference") or frappe.db.get_value(
        "Clinic Appointment", {"encounter_reference": doc.name}, "name")
    if not linked:
        return
    if skip_invalid_links and not frappe.db.exists("Clinic Appointment", linked):
        frappe.logger("mobile_app").warning(
            "Skipping clinic status sync for encounter %s: appointment %s is missing",
            doc.name, linked,
        )
        return
    # Require a reciprocal link before changing another application's record.
    clinic = frappe.get_doc("Clinic Appointment", linked)
    if clinic.encounter_reference != doc.name:
        if skip_invalid_links:
            frappe.logger("mobile_app").warning(
                "Skipping clinic status sync for encounter %s: appointment %s links to encounter %s",
                doc.name, linked, clinic.encounter_reference,
            )
            return
        frappe.throw(_("The linked Clinic Appointment belongs to another encounter."))
    if clinic.appointment_status != mapped:
        frappe.db.set_value("Clinic Appointment", linked, "appointment_status", mapped)
        clinic.add_comment("Comment", text=_("Appointment calendar: {0}").format(mapped))



def _reschedule(doc, date, expected_date):
    """Move the canonical booking date through normal scheduling validation."""
    if doc.doctype == "Mobile App Appointment" and doc.get("patient_encounter"):
        frappe.throw(_("Reschedule the linked encounter appointment."))
    old_date = doc.get("pe_appointment_date") or doc.get("appointment_date") or doc.get("encounter_date")
    if not expected_date or getdate(expected_date) != getdate(old_date):
        frappe.throw(_("The appointment date changed. Refresh and try again."), frappe.TimestampMismatchError)
    if not date:
        frappe.throw(_("Choose a reschedule date."))
    date = getdate(date)
    if date < getdate(now_datetime()) or date == getdate(old_date):
        frappe.throw(_("Choose a different date, today or later."))
    time = doc.get("pe_appointment_time") or doc.get("appointment_time") or doc.get("encounter_time") or "09:00:00"
    if datetime.combine(date, get_time(time)) <= now_datetime():
        frappe.throw(_("The appointment time on the selected date has already passed."))
    if doc.doctype == "Patient Encounter":
        if not doc.meta.has_field(RESCHEDULE_DATE_FIELD):
            frappe.throw(_("Run Mobile App migration before rescheduling encounters."))
        doc.flags.calendar_reschedule = _CALENDAR_RESCHEDULE
        doc.set(RESCHEDULE_DATE_FIELD, date)
        doc.pe_appointment_date = date
    else:
        doc.appointment_date = date
        doc.practitioner_schedule = None
        from mobile_app.api.practitioners import _ONLINE_DOCTOR_CHANGE
        doc.flags._online_doctor_change = _ONLINE_DOCTOR_CHANGE
    doc.save(ignore_permissions=True)
    return str(old_date)


def protect_reschedule_date(doc, method=None):
    """Only the authorized calendar action may write this encounter field."""
    if not doc.meta.has_field(RESCHEDULE_DATE_FIELD):
        return
    if doc.flags.get("calendar_reschedule") is _CALENDAR_RESCHEDULE:
        return
    stored = None if doc.is_new() else frappe.db.get_value(doc.doctype, doc.name, RESCHEDULE_DATE_FIELD)
    doc.set(RESCHEDULE_DATE_FIELD, stored)


def sync_encounter_status(doc, method=None):
    """Maintain the read-only encounter field; form/API saves cannot bypass decisions."""
    if not doc.meta.has_field(APPOINTMENT_STATUS_FIELD):
        return
    status = _status(doc, _workflow(doc.doctype, doc.name)) if doc.get("sr_encounter_type") == "Appointment" else ""
    doc.set(APPOINTMENT_STATUS_FIELD, status)


def _sync_encounter_status(doc, status):
    if doc.doctype == "Patient Encounter" and doc.meta.has_field(APPOINTMENT_STATUS_FIELD):
        if frappe.db.get_value(doc.doctype, doc.name, APPOINTMENT_STATUS_FIELD) != status:
            frappe.db.set_value(doc.doctype, doc.name, APPOINTMENT_STATUS_FIELD, status)
        doc.set(APPOINTMENT_STATUS_FIELD, status)


def _doctor_change_context():
    context = _context()
    if not context[1].intersection(DOCTOR_CHANGE_ROLES):
        frappe.throw(_('Only Appointment Agents and Appointment Managers can change appointment doctors.'), frappe.PermissionError)
    return context


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def online_doctor_query(doctype, txt, searchfield, start, page_len, filters=None):
    _doctor_change_context()
    if doctype != 'Healthcare Practitioner':
        frappe.throw(_('Unsupported doctor lookup.'), frappe.PermissionError)
    conditions = {'status':'Active'}
    if frappe.get_meta(doctype).has_field('custom_accept_online_appointments'):
        conditions['custom_accept_online_appointments'] = 1
    return frappe.get_all(doctype, filters=conditions,
        or_filters={'name':['like','%'+(txt or '')+'%'],'practitioner_name':['like','%'+(txt or '')+'%']},
        fields=['name','practitioner_name'], as_list=True, order_by='practitioner_name',
        limit_start=max(0,cint(start)), limit_page_length=min(50,max(1,cint(page_len))))


@frappe.whitelist(methods=['POST'])
def change_online_doctor(doctype, name, doctor, expected_doctor, expected_status, reason):
    context = _doctor_change_context()
    doc = _source(doctype, name, lock=True)
    current = _workflow(doctype, name)
    if not _can_read(doc, current, context):
        frappe.throw(_('You cannot change this appointment.'), frappe.PermissionError)
    _require_current_or_past(doc)
    if doctype == 'Mobile App Appointment' and doc.get('patient_encounter'):
        frappe.throw(_('Change the doctor on the linked encounter appointment.'))
    online = doc.get('sr_encounter_place') == 'Online' if doctype == 'Patient Encounter' else bool(doc.get('is_online'))
    if not online:
        frappe.throw(_('Use OPD visit management for in-clinic appointments.'))
    status = _status(doc, current)
    old_doctor, old_label, _user = _doctor(doc)
    if status != expected_status or old_doctor != expected_doctor:
        frappe.throw(_('This appointment changed. Refresh before changing its doctor.'), frappe.TimestampMismatchError)
    if status != 'Pending':
        frappe.throw(_('The doctor can only be changed before the appointment is approved.'))
    reason = str(reason or '').strip()
    if not reason or len(reason) > 1000:
        frappe.throw(_('Enter a reason of 1 to 1,000 characters.'))
    practitioner = frappe.get_doc('Healthcare Practitioner', doctor)
    if practitioner.status != 'Active':
        frappe.throw(_('Choose an active doctor.'))
    if practitioner.meta.has_field('custom_accept_online_appointments') and not cint(practitioner.get('custom_accept_online_appointments')):
        frappe.throw(_('This doctor does not accept online appointments.'))
    if old_doctor == doctor:
        frappe.throw(_('Choose a different doctor.'))
    if doctype == 'Patient Encounter':
        doc.pe_practitioner = doctor
    else:
        doc.practitioner_id = doctor
        doc.doctor_name = practitioner.practitioner_name or practitioner.name
        doc.doctor_user = practitioner.get('user_id')
        # The new doctor must have an available slot at the existing booking time.
        doc.practitioner_schedule = None
        from mobile_app.api.practitioners import _ONLINE_DOCTOR_CHANGE
        doc.flags._online_doctor_change = _ONLINE_DOCTOR_CHANGE
    # Preserve normal booking validation and linked scheduling integrations.
    doc.save(ignore_permissions=True)
    state = frappe.get_doc(WORKFLOW, current.name) if current else frappe.get_doc({
        'doctype':WORKFLOW,'appointment_key':_key(doctype,name),
        'reference_doctype':doctype,'reference_name':name,'workflow_status':status,
        'assigned_agent':_agent(doc,current)})
    state.save(ignore_permissions=True)
    message = f'Doctor changed: {old_label} ({old_doctor}) → {practitioner.practitioner_name or doctor} ({doctor}). Reason: {reason}'
    state.add_comment('Comment',text=frappe.utils.escape_html(message))
    # A prior identity lookup in this request must not return stale doctor details.
    frappe.local.appointment_calendar_doctors = {}
    return _serialize(doc,state,context,details=True)


def normalize_clinic_appointment_status(doc, method=None):
    """Match Select capitalization without changing clinical or workflow status."""
    field = doc.meta.get_field("appointment_status")
    if not field or field.fieldtype != "Select":
        return
    value = doc.get("appointment_status")
    if not isinstance(value, str):
        return
    options = {option.strip().casefold(): option.strip()
               for option in (field.options or "").splitlines() if option.strip()}
    canonical = options.get(value.strip().casefold())
    if canonical is not None:
        doc.set("appointment_status", canonical)
    # Unknown statuses remain untouched so normal Select validation rejects them.
