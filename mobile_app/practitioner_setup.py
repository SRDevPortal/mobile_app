"""Healthcare Practitioner disease selections owned and deployed by MobileApp."""

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def setup_practitioner_diseases():
    if not frappe.db.exists("DocType", "Healthcare Practitioner"):
        return

    # Keep the existing child table and field names when moving from the old app.
    # Reloading the metadata transfers ownership without deleting selected rows.
    frappe.reload_doc("mobileapp", "doctype", "sr_practitioner_disease")
    create_custom_fields({
        "Healthcare Practitioner": [{
            "fieldname": "sr_diseases",
            "label": "Diseases",
            "fieldtype": "Table MultiSelect",
            "options": "SR Practitioner Disease",
            "insert_after": "gender",
            "description": "Select the diseases this practitioner treats.",
            "module": "MobileApp",
        }]
    }, ignore_validate=True)

    # Retain all existing layout customizations and place only this field.
    fields = [field.fieldname for field in frappe.get_meta("Healthcare Practitioner").fields]
    fields.remove("sr_diseases")
    fields.insert(fields.index("gender") + 1, "sr_diseases")
    filters = {
        "doc_type": "Healthcare Practitioner",
        "doctype_or_field": "DocType",
        "property": "field_order",
    }
    name = frappe.db.get_value("Property Setter", filters, "name")
    setter = frappe.get_doc("Property Setter", name) if name else frappe.get_doc({
        "doctype": "Property Setter", **filters,
    })
    value = json.dumps(fields)
    if setter.is_new() or setter.value != value or setter.module != "MobileApp":
        setter.update({"value": value, "property_type": "Text", "module": "MobileApp"})
        setter.save(ignore_permissions=True)

    frappe.clear_cache(doctype="SR Practitioner Disease")
    frappe.clear_cache(doctype="Healthcare Practitioner")
