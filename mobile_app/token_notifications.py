"""Durable token notifications. No network requests inside check-in transactions."""
import frappe
from functools import partial
from frappe.utils import now_datetime, add_to_date, cint
from mobile_app import opd_store as store

DT = "OPD Token Notification"


def queue_token(visit):
    if frappe.db.exists(DT, visit.name):
        return
    config = store.get("Clinic", visit.clinic).get("token_whatsapp") or {}
    enabled = cint(frappe.conf.get("opd_token_whatsapp_enabled")) and config.get("enabled")
    phone = frappe.db.get_value("Patient", visit.patient, "mobile")
    reason = "" if enabled else "Token WhatsApp sending is disabled."
    if enabled and not phone:
        reason = "Patient mobile number is missing."
    rooms = [f"{step['stage']}: OPD {store.get('Room', step['room']).room_number}" for step in visit.stages]
    token = f"{visit.token_prefix}-" if visit.get("token_prefix") else ""
    token += str(visit.token_number).zfill(3)
    date = frappe.utils.formatdate(visit.visit_date, "dd MMM yyyy")
    appointment_time = None
    if visit.get("source_doctype") and visit.get("source_name"):
        appointment = frappe.get_doc(visit.source_doctype, visit.source_name)
        appointment_time = appointment.get("pe_appointment_time") or appointment.get("appointment_time")
    if appointment_time:
        date += ", " + frappe.utils.get_time(appointment_time).strftime("%I:%M %p")
    doctor = frappe.db.get_value("Healthcare Practitioner", visit.consultation_doctor, "practitioner_name") or visit.consultation_doctor
    details = f"{date}, {doctor}, Room sequence: " + " \u2192 ".join(rooms)
    values = [visit.patient_name, token, details]
    frappe.get_doc({"doctype": DT, "visit": visit.name, "clinic": visit.clinic,
        "patient": visit.patient, "status": "Skipped" if reason else "Queued",
        "last_error": reason, "channel": config.get("channel"), "template_name": config.get("template_name"),
        "language": config.get("language") or "en", "payload": frappe.as_json({"phone": phone, "values": values}),
        "attempts": 0}).insert(ignore_permissions=True)
    if not reason:
        schedule_delivery(visit.name)


def schedule_delivery(name):
    # Commit first: rollback must never dispatch a notification.
    frappe.db.after_commit.add(partial(dispatch_now, name))


def dispatch_now(name):
    try:
        frappe.enqueue("mobile_app.token_notifications.deliver", name=name, queue="short", at_front=True)
    except Exception:
        # The persisted outbox remains eligible for the scheduler recovery scan.
        frappe.log_error(frappe.get_traceback(), "OPD immediate notification dispatch failed")


def process_pending():
    fields = ["name", "status", "modified", "next_attempt", "retryable", "attempts"]
    rows = frappe.get_all(DT, filters={"status": ["in", ["Queued", "Sending", "Sent", "Delivered"]]},
        fields=fields, limit_page_length=100, order_by="modified asc")
    rows += frappe.get_all(DT, filters={"status": "Failed", "retryable": 1, "attempts": ["<", 3]},
        fields=fields, limit_page_length=100, order_by="modified asc")
    for row in rows:
        if row.status == "Sending":
            if (now_datetime() - row.modified).total_seconds() > 300:
                frappe.db.set_value(DT, row.name, {"status": "Unknown", "last_error": "Worker interrupted; verify delivery before resending."})
            continue
        if row.status in ("Sent", "Delivered"):
            sync_delivery(row.name)
            frappe.db.set_value(DT, row.name, "modified", now_datetime())
            continue
        if row.status == "Failed" and (not row.retryable or row.attempts >= 3):
            continue
        if row.next_attempt and row.next_attempt > now_datetime():
            continue
        frappe.enqueue("mobile_app.token_notifications.deliver", name=row.name, queue="short", enqueue_after_commit=True)


def sync_delivery(name):
    doc = frappe.get_doc(DT, name)
    if doc.chat_message:
        status = frappe.db.get_value("Chat Message", doc.chat_message, "delivery_status")
        if status in ("Delivered", "Read", "Failed"):
            frappe.db.set_value(DT, name, {"status": status, "retryable": 0})


def deliver(name):
    from wa_chat_hub.token_notifications import prepare, send
    from wa_chat_hub.delivery_outcomes import PatientTemplateNotSentError
    frappe.db.sql("SELECT name FROM `tabOPD Token Notification` WHERE name=%s FOR UPDATE", name)
    doc = frappe.get_doc(DT, name)
    if doc.status not in ("Queued", "Failed") or (doc.status == "Failed" and not doc.retryable) or doc.attempts >= 3:
        return
    if doc.next_attempt and doc.next_attempt > now_datetime():
        return
    config = store.get("Clinic", doc.clinic).get("token_whatsapp") or {}
    if not cint(frappe.conf.get("opd_token_whatsapp_enabled")) or not config.get("enabled"):
        frappe.db.set_value(DT, name, {"status": "Skipped", "last_error": "Token WhatsApp sending is disabled."})
        frappe.db.commit(); return
    if config.get("channel") != doc.channel or config.get("template_name") != doc.template_name:
        frappe.db.set_value(DT, name, {"status": "Skipped", "last_error": "Branch messaging configuration changed; review this notification."})
        frappe.db.commit(); return
    data = frappe.parse_json(doc.payload)
    if len(data.get("values", [])) != 3:
        frappe.db.set_value(DT, name, {"status": "Skipped", "last_error": "Legacy template parameters; issue a new token or review this notification."})
        frappe.db.commit(); return
    attempted = False
    try:
        prepared = prepare(doc.channel, data["phone"], data["values"][0], doc.template_name, doc.language, data["values"], name)
        frappe.db.set_value(DT, name, {"status": "Sending", "attempts": doc.attempts + 1, "retryable": 0})
        frappe.db.commit()  # Persist the send fence before contacting Interakt.
        attempted = True
        result = send(prepared, doc.channel, "OPD token issued: " + data["values"][1])
        frappe.db.set_value(DT, name, {"status": "Sent", "chat_message": result["message"],
            "provider_message_id": result["provider_message_id"], "last_error": ""})
    except PatientTemplateNotSentError as exc:
        frappe.db.set_value(DT, name, {"status": "Failed", "retryable": int(exc.retryable),
            "attempts": doc.attempts + 1, "last_error": str(exc)[:500],
            "next_attempt": add_to_date(now_datetime(), minutes=2 ** (doc.attempts + 1))})
    except Exception:
        frappe.log_error(frappe.get_traceback(), "OPD token notification")
        frappe.db.set_value(DT, name, {"status": "Unknown" if attempted else "Failed", "retryable": 0,
            "last_error": "Delivery outcome is uncertain; review in WhatsApp Hub." if attempted else "Notification configuration failed; review the error log."})
    frappe.db.commit()


@frappe.whitelist()
def settings(clinic):
    from mobile_app.api.opd_queue import _access, _branch_admin
    _access(clinic)
    if not _branch_admin(): frappe.throw("Branch administrator required.", frappe.PermissionError)
    return {**(store.get("Clinic", clinic).get("token_whatsapp") or {}),
        "site_enabled": cint(frappe.conf.get("opd_token_whatsapp_enabled"))}


@frappe.whitelist(methods=["POST"])
def save_settings(clinic, enabled=0, channel=None, template_name=None, language="en"):
    settings(clinic)
    if cint(enabled):
        from wa_chat_hub.interakt.template_selection import resolve_chat_template
        account = frappe.get_doc("Chat Channel Account", channel)
        if not account.is_active or account.channel_type != "Interakt": frappe.throw("Choose an active Interakt channel.")
        resolve_chat_template(channel, {"template_name": template_name, "language_code": language, "body_values": ["Amit", "GGN-006", "12 Oct 2026, 10:30 AM, Dr Aayushi Pathak, Room sequence: Vitals OPD 2 \u2192 Medical History OPD 1 \u2192 Doctor Consultation OPD 3"]})
    previous = frappe.flags.in_opd_mutation
    frappe.flags.in_opd_mutation = True
    try:
        store.lock()
        branch = store.get("Clinic", clinic)
        branch.token_whatsapp = {"enabled": cint(enabled), "channel": channel, "template_name": template_name, "language": language}
        branch.save()
    finally:
        frappe.flags.in_opd_mutation = previous


@frappe.whitelist()
def status(clinic, visit):
    from mobile_app.api.opd_queue import _access, _require_reception, _get
    _access(clinic); _require_reception(); _get("Visit", visit, clinic)
    if not frappe.db.exists(DT, visit): return {"status": "Not queued"}
    sync_delivery(visit)
    doc = frappe.get_doc(DT, visit)
    return {"status": doc.status, "detail": doc.last_error,
        "can_retry": doc.status == "Failed" and bool(doc.retryable) and doc.attempts < 3}


@frappe.whitelist(methods=["POST"])
def retry(clinic, visit):
    result = status(clinic, visit)
    if not result.get("can_retry"): frappe.throw("This notification cannot be safely retried.")
    frappe.db.sql("SELECT name FROM `tabOPD Token Notification` WHERE name=%s FOR UPDATE", visit)
    doc = frappe.get_doc(DT, visit)
    if doc.status != "Failed" or not doc.retryable or doc.attempts >= 3: frappe.throw("Notification changed; refresh.")
    frappe.db.set_value(DT, visit, {"status": "Queued", "next_attempt": None})
    schedule_delivery(visit)
