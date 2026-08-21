import os

import frappe
from frappe.modules.import_file import import_file_by_path
from frappe.permissions import add_permission

MOBILE_DOCTYPES = (
	"Mobile App User",
	"Mobile App User Profile Item",
	"Mobile App User Session Item",
	"Mobile App Medical Item",
	"Mobile App Appointment Item",
	"Mobile App Engagement Item",
	"App Support Ticket",
	"App Support Ticket Message",
	"Support Ticket",
	"Support Ticket Message",
)


def execute():
	"""Load public Workspace from app; ensure Desk users can read one module doctype for sidebar."""
	app_path = frappe.get_app_path("mobile_app")
	json_path = os.path.join(app_path, "mobileapp", "workspace", "mobile_app", "mobile_app.json")
	if os.path.isfile(json_path):
		import_file_by_path(json_path, force=True)

	for dt in MOBILE_DOCTYPES:
		if not frappe.db.exists("DocType", dt):
			continue
		if frappe.db.exists("DocPerm", {"parent": dt, "role": "Desk User"}):
			continue
		if frappe.db.exists("Custom DocPerm", {"parent": dt, "role": "Desk User"}):
			continue
		try:
			add_permission(dt, "Desk User", ptype="read")
		except Exception:
			frappe.log_error(f"Could not add Desk User read on {dt}")

	frappe.clear_cache()
