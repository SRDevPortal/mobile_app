"""Diet Chart PDF, item, and item group template links, managed by MobileApp."""

from urllib.parse import unquote, urlsplit

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_migrate():
    if not frappe.db.exists("DocType", "Diet Chart"):
        return
    frappe.reload_doc("mobileapp", "doctype", "diet_chart_item")
    frappe.reload_doc("mobileapp", "doctype", "diet_chart_item_group_template")
    create_custom_fields({
        "Diet Chart": [{
            "fieldname": "custom_diet_chart_pdf",
            "label": "Diet Chart PDF",
            "fieldtype": "Attach",
            "insert_after": "diet_chart_name",
            "description": "Attach a PDF version of this diet chart.",
            "module": "MobileApp",
        }, {
            "fieldname": "custom_items",
            "label": "Items",
            "fieldtype": "Table MultiSelect",
            "options": "Diet Chart Item",
            "insert_after": "custom_diet_chart_pdf",
            "description": "Select one or more items to link to this diet chart.",
            "module": "MobileApp",
        }, {
            "fieldname": "custom_item_group_templates",
            "label": "Item Group Templates",
            "fieldtype": "Table MultiSelect",
            "options": "Diet Chart Item Group Template",
            "insert_after": "custom_items",
            "description": "Select one or more item group templates to link to this diet chart.",
            "module": "MobileApp",
        }]
    })


def validate_pdf(doc, method=None):
    value = doc.get("custom_diet_chart_pdf")
    if not value:
        return
    try:
        is_pdf = unquote(urlsplit(value).path).lower().endswith(".pdf")
    except (TypeError, ValueError):
        is_pdf = False
    if not is_pdf:
        frappe.throw(_("Diet Chart PDF must be a PDF file (.pdf)."))
