"""Invalidate Mobile App views after committed writes, without sending record data."""
import frappe


def notify_change(doc, method=None):
    # Clients fetch fresh data through their existing permission-checked APIs.
    frappe.publish_realtime(
        "mobile_app_data_changed",
        {"doctype": doc.doctype},
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
