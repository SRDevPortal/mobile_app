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
