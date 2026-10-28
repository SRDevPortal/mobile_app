"""Require a Desk manual save after OPD route completion."""
import frappe
from frappe.utils import get_datetime
from mobile_app import opd_store as store


def completed_visits(doc):
    from mobile_app.api.opd_queue import _checkout_ready
    for visit in store.records("Visit", {"patient": doc.patient, "status": "Completed"}):
        if visit.get("checkout_completed_at") or not _checkout_ready(visit):
            continue
        encounter = visit.get("encounter")
        if not encounter and visit.source_doctype == "Patient Encounter":
            encounter = visit.source_name
        if not encounter and visit.source_doctype == "Mobile App Appointment":
            encounter = frappe.db.get_value("Mobile App Appointment", visit.source_name, "patient_encounter")
        if encounter == doc.name:
            yield visit


def marker(doc):
    return "OPD manual encounter save: " + str(doc.modified)


def is_ready(visit, doc):
    completed = visit.get("completed_at")
    if not completed:
        return False
    if get_datetime(doc.modified) <= get_datetime(completed):
        return False
    return bool(frappe.db.exists("Comment", {
        "reference_doctype": "Patient Encounter", "reference_name": doc.name,
        "comment_type": "Info", "content": marker(doc),
        "creation": [">", completed],
    }))


def on_update(doc, method=None):
    request = frappe.form_dict
    if (doc.docstatus != 0 or doc.get("__opd_autosave")
            or request.get("cmd") != "frappe.desk.form.save.savedocs"
            or request.get("action") != "Save"):
        return
    if any(completed_visits(doc)):
        doc.add_comment("Info", marker(doc))
