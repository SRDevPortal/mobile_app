"""Permission-scoped appointment calendar and durable clinic queue transitions."""

import hashlib
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import getdate, get_time, now_datetime, cint

WORKFLOW = "Mobile Appointment Workflow"
SOURCES = ("Patient Encounter", "Mobile App Appointment")
AGENT_ROLES = {"Agent", "Appointment Agent"}
DOCTOR_ROLES = {"Physician", "Healthcare Practitioner", "Mobile App Doctor"}
MANAGER_ROLES = {"System Manager", "Appointment Manager"}
RECEPTION_ROLE = "Appointment Receptionist"
TRANSITIONS = {
    "approve": (("Pending",), "Approved"),
    "cancel": (("Pending", "Approved"), "Cancelled"),
    "check_in": (("Approved",), "Checked In"),
}
LEGACY_STATUSES = {"Rejected": "Cancelled", "In Consultation": "Checked In", "Completed": "Checked In"}
APPOINTMENT_STATUS_FIELD = "custom_appointment_status"



def _context():
    user = frappe.session.user
    roles = set(frappe.get_roles(user)) if user != "Guest" else set()
    manager = user == "Administrator" or bool(roles.intersection(MANAGER_ROLES))
    if not manager and not roles.intersection(AGENT_ROLES | DOCTOR_ROLES | {RECEPTION_ROLE}):
        frappe.throw(_("You do not have access to the appointment calendar."), frappe.PermissionError)
    return user, roles, manager


def _key(doctype, name):
    return hashlib.sha256(f"{doctype}:{name}".encode()).hexdigest()[:32]


def _workflow(doctype, name):
    return frappe.db.get_value(WORKFLOW, _key(doctype, name), "*", as_dict=True) or frappe._dict()


def _source(doctype, name, lock=False):
    if doctype not in SOURCES:
        frappe.throw(_("Unsupported appointment source."))
    if lock:
        # Lock the source even before a workflow exists, serializing first-time decisions.
        frappe.db.sql(f"SELECT name FROM `tab{doctype}` WHERE name=%s FOR UPDATE", (name,))
    doc = frappe.get_doc(doctype, name)
    if doctype == "Patient Encounter" and doc.get("sr_encounter_type") != "Appointment":
        frappe.throw(_("This encounter is not an appointment."))
    return doc


def _doctor(doc):
    if doc.doctype == "Patient Encounter":
        practitioner = doc.get("pe_practitioner") or doc.get("practitioner")
        if practitioner:
            cache = getattr(frappe.local, "appointment_calendar_doctors", None)
            if cache is None:
                cache = frappe.local.appointment_calendar_doctors = {}
            if practitioner not in cache:
                cache[practitioner] = frappe.db.get_value("Healthcare Practitioner", practitioner,
                    ["practitioner_name", "user_id"], as_dict=True) or {}
            data = cache[practitioner]
            return practitioner, data.get("practitioner_name") or practitioner, data.get("user_id")
        return "unassigned", _("Unassigned doctor"), None
    user = doc.get("doctor_user")
    name = doc.get("doctor_name") or user or _("Unassigned doctor")
    return user or doc.get("doctor_name") or "unassigned", name, user


def _agent(doc, workflow):
    assigned = workflow.get("assigned_agent") or doc.get("assigned_agent")
    creator = doc.get("created_by_agent")
    if not assigned and creator and set(frappe.get_roles(creator)).intersection(AGENT_ROLES):
        assigned = creator
    return assigned


def _can_read(doc, workflow, context):
    user, roles, manager = context
    if manager:
        return True
    if RECEPTION_ROLE in roles:
        return _status(doc, workflow) in ("Approved", "Checked In")
    agent = _agent(doc, workflow)
    if roles.intersection(AGENT_ROLES) and (not agent or agent == user):
        return True
    return bool(roles.intersection(DOCTOR_ROLES) and _doctor(doc)[2] == user)


def _status(doc, workflow):
    source_status = (doc.get("status") or "").lower()
    encounter_status = (doc.get("sr_encounter_status") or "").lower()
    if doc.docstatus == 2 or source_status in ("cancelled", "canceled") or encounter_status in ("cancelled", "canceled"):
        return "Cancelled"
    if source_status in ("completed", "done") or encounter_status == "completed":
        return "Checked In"
    if workflow.get("workflow_status"):
        return LEGACY_STATUSES.get(workflow.workflow_status, workflow.workflow_status)
    return "Pending"


def _actions(doc, workflow, context):
    user, roles, manager = context
    status = _status(doc, workflow)
    if RECEPTION_ROLE in roles and not manager:
        return ["check_in"] if status == "Approved" else []
    agent = _agent(doc, workflow)
    can_manage = manager or bool(roles.intersection(AGENT_ROLES) and agent == user)
    actions = []
    if not agent and roles.intersection(AGENT_ROLES) and status not in ("Checked In", "Cancelled"):
        actions.append("claim")
    for action, (before, _) in TRANSITIONS.items():
        if status in before and can_manage:
            actions.append(action)
    return actions


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
               actions=_actions(doc, workflow, context), encounter=encounter,
               checked_in_at=workflow.get("checked_in_at"))
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


@frappe.whitelist()
def get_calendar(start, end):
    """End-exclusive date range. Never return patient data outside the caller's scope."""
    context = _context()
    start, end = getdate(start), getdate(end)
    if not 0 < (end - start).days <= 62:
        frappe.throw(_("Choose a date range between 1 and 62 days."))
    rows = []
    if frappe.db.exists("DocType", "Patient Encounter") and frappe.get_meta("Patient Encounter").has_field("sr_encounter_type"):
        encounters = frappe.db.sql("""SELECT name, patient_name, patient, sr_pe_mobile, sr_encounter_type,
            sr_encounter_place, pe_appointment_date, encounter_date, pe_appointment_time,
            encounter_time, pe_practitioner, practitioner, status, sr_encounter_status, docstatus, created_by_agent
            FROM `tabPatient Encounter`
            WHERE sr_encounter_type='Appointment'
            AND COALESCE(pe_appointment_date, encounter_date) >= %s
            AND COALESCE(pe_appointment_date, encounter_date) < %s
            ORDER BY COALESCE(pe_appointment_date, encounter_date) LIMIT 2001""", (start, end), as_dict=True)
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
        fields=["name", "patient_encounter", "patient_name", "doctor_user", "doctor_name",
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
    for doc in rows:
        workflow = workflows.get(_key(doc.doctype, doc.name), frappe._dict())
        if _can_read(doc, workflow, context):
            result.append(_serialize(doc, workflow, context))
    doctor_filters = {"status": "Active"}
    if not context[2] and not context[1].intersection(AGENT_ROLES | {RECEPTION_ROLE}):
        doctor_filters["user_id"] = context[0]
    doctors = frappe.get_all("Healthcare Practitioner", filters=doctor_filters,
        fields=["name as id", "practitioner_name as name"], order_by="practitioner_name", limit_page_length=0)
    return {"appointments": result, "doctors": doctors, "can_assign": context[2], "user": context[0],
            "reception_only": RECEPTION_ROLE in context[1] and not context[2],
            "timezone": frappe.utils.get_system_timezone()}


@frappe.whitelist()
def get_appointment(doctype, name):
    context = _context()
    doc, workflow = _source(doctype, name), _workflow(doctype, name)
    if not _can_read(doc, workflow, context):
        frappe.throw(_("This appointment is assigned to another team member."), frappe.PermissionError)
    return _serialize(doc, workflow, context, details=True)


@frappe.whitelist(methods=["POST"])
def update_appointment(doctype, name, action, expected_status, reason=None, agent=None):
    action = "cancel" if action == "reject" else action  # Compatibility with already-open older clients.
    context = _context()
    doc = _source(doctype, name, lock=True)
    current = _workflow(doctype, name)
    if not _can_read(doc, current, context):
        frappe.throw(_("You cannot update this appointment."), frappe.PermissionError)
    before = _status(doc, current)
    if before != expected_status:
        frappe.throw(_("The appointment changed. Refresh and try again."), frappe.TimestampMismatchError)
    if action == "assign":
        if not context[2]:
            frappe.throw(_("Only an Appointment Manager or System Manager can assign agents."), frappe.PermissionError)
        if before in ("Cancelled", "Checked In"):
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
    if action in ("assign", "claim"):
        state.assigned_agent = agent if action == "assign" else context[0]
    else:
        state.workflow_status = TRANSITIONS[action][1]
        if action in ("approve", "cancel"):
            state.decision_by, state.decision_at = context[0], now
            state.decision_reason = (reason or "").strip()
        timestamp = {"check_in": "checked_in_at"}.get(action)
        if timestamp:
            state.set(timestamp, now)
    state.save(ignore_permissions=True)
    _sync_encounter_status(doc, state.workflow_status)
    _sync_clinic_status(doc, state.workflow_status)
    message = f"{before} → {state.workflow_status}" if action not in ("assign", "claim") else f"Assigned to {state.assigned_agent}"
    if reason:
        message += f". Reason: {reason.strip()}"
    state.add_comment("Comment", text=frappe.utils.escape_html(message))
    # Publish only to this user's session; other users poll within their own permission scope.
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


def _sync_clinic_status(doc, status):
    """Update the linked scheduling record in the same transaction as the queue.

    Clinical/billing encounter statuses are separate. A status-only database update
    avoids reverse-sync hooks and booking-time validations on an existing visit.
    """
    if doc.doctype != "Patient Encounter" or not frappe.db.exists("DocType", "Clinic Appointment"):
        return
    mapped = {"Pending": "Draft", "Approved": "Confirmed", "Checked In": "Completed",
              "Cancelled": "Cancelled"}.get(status)
    if not mapped:
        return
    linked = doc.get("encounter_reference") or frappe.db.get_value(
        "Clinic Appointment", {"encounter_reference": doc.name}, "name")
    if not linked:
        return
    # Require a reciprocal link before changing another application's record.
    clinic = frappe.get_doc("Clinic Appointment", linked)
    if clinic.encounter_reference != doc.name:
        frappe.throw(_("The linked Clinic Appointment belongs to another encounter."))
    if clinic.appointment_status != mapped:
        frappe.db.set_value("Clinic Appointment", linked, "appointment_status", mapped)
        clinic.add_comment("Comment", text=_("Appointment calendar: {0}").format(mapped))


def sync_encounter_status(doc, method=None):
    """Maintain the read-only encounter field; form/API saves cannot bypass decisions."""
    if not doc.meta.has_field(APPOINTMENT_STATUS_FIELD):
        return
    status = _status(doc, _workflow(doc.doctype, doc.name)) if doc.get("sr_encounter_type") == "Appointment" else ""
    doc.set(APPOINTMENT_STATUS_FIELD, status)
    if method == "on_cancel":
        _sync_encounter_status(doc, status)
        _sync_clinic_status(doc, status)


def _sync_encounter_status(doc, status):
    if doc.doctype == "Patient Encounter" and doc.meta.has_field(APPOINTMENT_STATUS_FIELD):
        if frappe.db.get_value(doc.doctype, doc.name, APPOINTMENT_STATUS_FIELD) != status:
            frappe.db.set_value(doc.doctype, doc.name, APPOINTMENT_STATUS_FIELD, status)
        doc.set(APPOINTMENT_STATUS_FIELD, status)
