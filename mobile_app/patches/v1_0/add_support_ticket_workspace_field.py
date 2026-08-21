"""Add HTML workspace field so Support Ticket tab is not hidden by Frappe (empty tabs are hidden)."""

import os

import frappe
from frappe.modules.import_file import import_file_by_path


def execute():
	json_path = os.path.join(
		frappe.get_app_path("mobile_app"),
		"mobileapp",
		"doctype",
		"mobile_app_user",
		"mobile_app_user.json",
	)
	if os.path.isfile(json_path):
		import_file_by_path(json_path, force=True)
	frappe.clear_cache(doctype="Mobile App User")
