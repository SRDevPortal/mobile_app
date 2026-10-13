"""Internal OPD state; writes belong to the permission-checked queue API."""
import frappe
from frappe.model.document import Document

class OPDStateRecord(Document):
    def validate(self):
        if not frappe.flags.in_opd_mutation and not frappe.flags.in_migrate:
            frappe.throw("Manage OPD state through Doctor Clinical.", frappe.PermissionError)
