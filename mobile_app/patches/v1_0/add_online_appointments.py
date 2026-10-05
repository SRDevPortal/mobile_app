"""Online consultations are opt-in per Healthcare Practitioner."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if not frappe.db.exists("DocType", "Healthcare Practitioner"):
        return
    create_custom_fields({"Healthcare Practitioner": [{
        "fieldname": "custom_accept_online_appointments",
        "label": "Accept Online Appointments",
        "fieldtype": "Check",
        "insert_after": "status",
        "default": "0",
        "hidden": 1,
        "description": "Show this practitioner for online consultations in the mobile app.",
    }]})
