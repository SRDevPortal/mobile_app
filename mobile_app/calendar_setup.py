"""Idempotent setup for the appointment calendar."""

import frappe


def after_migrate():
    for role in ("Appointment Agent", "Appointment Manager", "Appointment Receptionist"):
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
    setup_calendar_indexes()
    setup_encounter_status()
    frappe.clear_cache()


def setup_encounter_status():
    if not frappe.db.exists("DocType", "Patient Encounter"):
        return
    # The appointment encounter integration is optional on standard Healthcare sites.
    # Do not query a clinic app's custom field until that app has created it.
    if not frappe.get_meta("Patient Encounter").has_field("sr_encounter_type"):
        return
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    from mobile_app.api.appointment_calendar import (
        APPOINTMENT_STATUS_FIELD, RESCHEDULE_DATE_FIELD, WORKFLOW,
    )

    create_custom_fields({"Patient Encounter": [{
        "fieldname": APPOINTMENT_STATUS_FIELD, "label": "Appointment Status",
        "fieldtype": "Select", "options": "\nPending\nApproved\nCancelled\nChecked In\nCompleted",
        "insert_after": "sr_encounter_type", "read_only": 1, "allow_on_submit": 1,
        "no_copy": 1, "in_standard_filter": 1,
        "depends_on": "eval:doc.sr_encounter_type == 'Appointment'",
        "description": "Independent Mobile App appointment workflow. Updated by authorized staff.",
    }, {
        "fieldname": RESCHEDULE_DATE_FIELD, "label": "Reschedule Date",
        "fieldtype": "Date", "insert_after": "pe_appointment_date",
        "read_only": 1, "allow_on_submit": 1, "no_copy": 1,
        "depends_on": "eval:doc.sr_encounter_type == 'Appointment'",
        "description": "Last date selected using Reschedule in the Mobile App Appointment Calendar.",
    }]})
    if not frappe.db.exists("DocType", WORKFLOW):
        return
    # Existing clinical-derived values require review, not a migration-time rewrite.
    # New saves mirror only the independent workflow into the read-only display field.


def setup_calendar_indexes():
    """Bound date lookups to the visible period instead of scanning encounter history."""
    if frappe.db.exists("DocType", "Patient Encounter"):
        meta = frappe.get_meta("Patient Encounter")
        fields = ["sr_encounter_type", "pe_appointment_date", "encounter_date"]
        if all(meta.has_field(field) for field in fields):
            frappe.db.add_index("Patient Encounter", fields, "appointment_calendar_dates")
    if frappe.db.exists("DocType", "Mobile App Appointment"):
        frappe.db.add_index("Mobile App Appointment", ["appointment_date"], "appointment_calendar_date")
