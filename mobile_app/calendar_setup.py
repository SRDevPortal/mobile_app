"""Idempotent setup for the appointment calendar."""

import frappe


def after_migrate():
    for role in ("Appointment Agent", "Appointment Manager", "Appointment Receptionist"):
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
    setup_encounter_status()
    setup_calendar_indexes()
    frappe.clear_cache()


def setup_encounter_status():
    if not frappe.db.exists("DocType", "Patient Encounter"):
        return
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    from mobile_app.api.appointment_calendar import (
        APPOINTMENT_STATUS_FIELD, LEGACY_STATUSES, WORKFLOW, _status, _workflow,
        _sync_encounter_status, _sync_clinic_status,
    )

    create_custom_fields({"Patient Encounter": [{
        "fieldname": APPOINTMENT_STATUS_FIELD, "label": "Appointment Status",
        "fieldtype": "Select", "options": "\nPending\nApproved\nCancelled\nChecked In",
        "insert_after": "sr_encounter_type", "read_only": 1, "allow_on_submit": 1,
        "no_copy": 1, "in_standard_filter": 1,
        "depends_on": "eval:doc.sr_encounter_type == 'Appointment'",
        "description": "Updated by Appointment Calendar. Checked In is the final appointment step.",
    }]})
    if not frappe.db.exists("DocType", WORKFLOW):
        return
    # Retain timestamps and audit comments; only normalize obsolete queue states.
    for old, new in LEGACY_STATUSES.items():
        frappe.db.sql(f"UPDATE `tab{WORKFLOW}` SET workflow_status=%s WHERE workflow_status=%s", (new, old))
    for name in frappe.get_all("Patient Encounter", filters={"sr_encounter_type": "Appointment"}, pluck="name"):
        doc = frappe.get_doc("Patient Encounter", name)
        status = _status(doc, _workflow(doc.doctype, doc.name))
        _sync_encounter_status(doc, status)
        _sync_clinic_status(doc, status)


def setup_calendar_indexes():
    """Bound date lookups to the visible period instead of scanning encounter history."""
    if frappe.db.exists("DocType", "Patient Encounter"):
        meta = frappe.get_meta("Patient Encounter")
        fields = ["sr_encounter_type", "pe_appointment_date", "encounter_date"]
        if all(meta.has_field(field) for field in fields):
            frappe.db.add_index("Patient Encounter", fields, "appointment_calendar_dates")
    if frappe.db.exists("DocType", "Mobile App Appointment"):
        frappe.db.add_index("Mobile App Appointment", ["appointment_date"], "appointment_calendar_date")
