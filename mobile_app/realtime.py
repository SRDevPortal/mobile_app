"""Invalidate Mobile App views after committed writes, without sending record data."""
import frappe


def notify_change(doc, method=None):
    # Only date boundaries are broadcast, never patient identity or record contents.
    message = {"doctype": doc.doctype}
    if doc.doctype in ("Patient Encounter", "Mobile App Appointment"):
        previous = doc.get_doc_before_save() if callable(getattr(doc, "get_doc_before_save", None)) else None
        dates = set()
        for record in (doc, previous):
            if not record: continue
            if record.doctype == "Patient Encounter" and record.get("sr_encounter_type") != "Appointment": continue
            date = record.get("pe_appointment_date") or record.get("appointment_date") or record.get("encounter_date")
            if date: dates.add(str(date))
        message["calendar_dates"] = sorted(dates)
    elif doc.doctype == "Mobile Appointment Workflow":
        source, name = doc.get("reference_doctype"), doc.get("reference_name")
        dates = []
        if source == "Patient Encounter":
            record = frappe.db.get_value(source, name, ["pe_appointment_date", "encounter_date"], as_dict=True)
            date = (record.pe_appointment_date or record.encounter_date) if record else None
            if date: dates.append(str(date))
        elif source == "Mobile App Appointment":
            date = frappe.db.get_value(source, name, "appointment_date")
            if date: dates.append(str(date))
        message["calendar_dates"] = dates
    # Other record types use a lightweight permission-checked revision manifest.
    # Clients fetch fresh data through their existing permission-checked APIs.
    frappe.publish_realtime(
        "mobile_app_data_changed",
        message,
        room="all",
        after_commit=True,
    )


def boot_session(bootinfo):
    bootinfo.mobile_app_realtime_origin = frappe.conf.get("mobile_app_realtime_origin")


def touch_support_ticket(doc, method=None):
    """New messages bump their conversation; read receipts must not reorder it."""
    ticket_doctype = {
        "Support Ticket Message": "Support Ticket",
        "App Support Ticket Message": "App Support Ticket",
    }[doc.doctype]
    if not doc.ticket:
        return
    frappe.db.set_value(ticket_doctype, doc.ticket, {
        "modified": frappe.utils.now_datetime(),
        "modified_by": frappe.session.user,
    }, update_modified=False)
    notify_change(frappe._dict(doctype=ticket_doctype))
