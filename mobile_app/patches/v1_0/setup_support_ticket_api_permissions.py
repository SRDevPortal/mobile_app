"""Role + DocPerm for Render middleware API user (Support Ticket / Support Ticket Message)."""

import frappe
from frappe.permissions import add_permission


ROLE = "Support Ticket API"


def execute():
	if not frappe.db.exists("Role", ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 0}).insert(
			ignore_permissions=True
		)

	for doctype in ("Support Ticket", "Support Ticket Message"):
		if not frappe.db.exists("DocType", doctype):
			continue
		for ptype in ("read", "write", "create", "delete"):
			if frappe.db.get_value("DocPerm", {"parent": doctype, "role": ROLE, "permlevel": 0}, ptype):
				continue
			try:
				add_permission(doctype, ROLE, ptype=ptype, permlevel=0)
			except Exception:
				frappe.log_error(title=f"Support Ticket API perm: {doctype}.{ptype}")

	frappe.clear_cache()
