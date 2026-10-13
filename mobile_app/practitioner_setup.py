"""Healthcare Practitioner profile fields owned and deployed by MobileApp."""

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
        }, {
            "fieldname": "custom_about_doctor",
            "label": "About Doctor",
            "fieldtype": "Small Text",
            "insert_after": "sr_diseases",
            "description": "Introduce the doctor, their experience, and areas of expertise.",
            "module": "MobileApp",
        }, {
            "fieldname": "custom_accept_opd_appointments",
            "label": "Accept OPD Appointments",
            "fieldtype": "Check",
            "insert_after": "custom_accept_online_appointments",
            "default": "1",
            "hidden": 1,
            "description": "Allow in-person outpatient consultations in mobile bookings.",
            "module": "MobileApp",
        }]
    }, ignore_validate=True)

    create_custom_fields({"Healthcare Practitioner": [
        {"fieldname": "custom_online_consulting_charge", "label": "Online Consulting Charge",
         "fieldtype": "Currency", "insert_after": "op_consulting_charge", "non_negative": 1, "module": "MobileApp"},
        {"fieldname": "custom_experience_years", "label": "Experience (years)",
         "fieldtype": "Float", "insert_after": "custom_about_doctor", "non_negative": 1, "module": "MobileApp"},
        {"fieldname": "custom_doctor_rating", "label": "Doctor Rating (0–5)",
         "fieldtype": "Float", "insert_after": "custom_experience_years", "non_negative": 1,
         "description": "Manually managed doctor rating, from 0 to 5.", "module": "MobileApp"},
    ]}, ignore_validate=True)

    # Retain existing layout customizations while grouping these profile fields.
    profile_fields = ["sr_diseases", "custom_about_doctor", "custom_experience_years", "custom_doctor_rating"]
    fields = [field.fieldname for field in frappe.get_meta("Healthcare Practitioner").fields
              if field.fieldname not in profile_fields]
    position = fields.index("gender") + 1
    fields[position:position] = profile_fields
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
