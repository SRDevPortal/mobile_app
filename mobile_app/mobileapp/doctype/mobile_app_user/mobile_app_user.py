# Copyright (c) 2026, SRIAAS and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


def has_permission(doc, ptype, user=None):
	"""Patients are created via mobile/API only — not manually in Desk (except System Manager)."""
	# Trusted server-side API operations validate X-ERP-Token before setting this
	# request-local flag. Honour it while keeping manual Desk creation blocked.
	if frappe.flags.get("ignore_permissions"):
		return True

	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user) or user == "Administrator":
		return None

	if ptype == "create":
		return False
	if ptype == "delete":
		return False
	return None


class MobileAppUser(Document):
	def on_update(self):
		self._ensure_profile_image_public()

	def after_insert(self):
		self._ensure_profile_image_public()

	def _ensure_profile_image_public(self):
		"""Profile photos must be public so mobile/web clients can load them without auth."""
		if not self.image:
			return
		filters = {"attached_to_doctype": self.doctype, "attached_to_name": self.name}
		restrict = {
			**filters,
			"attached_to_field": "image",
		}
		names = frappe.get_all("File", filters=restrict, pluck="name")
		if not names:
			names = frappe.get_all(
				"File",
				filters={**filters, "file_url": self.image},
				pluck="name",
			)
		for fname in names:
			frappe.db.set_value("File", fname, "is_private", 0)
