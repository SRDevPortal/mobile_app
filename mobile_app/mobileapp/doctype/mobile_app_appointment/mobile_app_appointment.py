# Copyright (c) 2026, SRIAAS and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from mobile_app.mobileapp.appointment_utils import (
	apply_consultation_rules,
	resolve_doctor_user,
)


class MobileAppAppointment(Document):
	def validate(self):
		from mobile_app.api.practitioners import validate_appointment
		validate_appointment(self)
		if not self.doctor_user and self.doctor_name:
			self.doctor_user = resolve_doctor_user(self.doctor_name)
		apply_consultation_rules(self)

	def on_update(self):
		sync_child_row_from_standalone(self)


def sync_child_row_from_standalone(doc: Document) -> None:
	"""Push Consultation/status back to parent Mobile App User child row."""
	if not doc.mobile_app_user or not doc.appointment_external_id:
		return
	if frappe.flags.get("in_appointment_sync_from_user"):
		return

	try:
		parent = frappe.get_doc("Mobile App User", doc.mobile_app_user)
	except frappe.DoesNotExistError:
		return

	updated = False
	for row in parent.appointments or []:
		if row.appointment_external_id != doc.appointment_external_id:
			continue
		row.consultation_type = doc.consultation_type
		row.status = doc.status
		row.doctor_name = doc.doctor_name
		row.practitioner_id = doc.practitioner_id
		row.practitioner_schedule = doc.practitioner_schedule
		row.duration = doc.duration
		row.appointment_date = doc.appointment_date
		row.appointment_time = doc.appointment_time
		row.email_status = doc.email_status
		row.whatsapp_status = doc.whatsapp_status
		updated = True
		break

	if not updated:
		return

	frappe.flags.in_appointment_sync_from_user = True
	try:
		parent.save(ignore_permissions=True)
	finally:
		frappe.flags.in_appointment_sync_from_user = False


def get_permission_query_conditions(user: str | None = None) -> str:
	user = user or frappe.session.user
	if not user or user == "Administrator":
		return ""
	if "System Manager" in frappe.get_roles(user):
		return ""
	if "Mobile App Doctor" not in frappe.get_roles(user):
		return ""

	full_name = frappe.db.get_value("User", user, "full_name") or ""
	escaped_user = frappe.db.escape(user)
	conditions = [f"`tabMobile App Appointment`.`doctor_user` = {escaped_user}"]
	if full_name:
		escaped_name = frappe.db.escape(full_name)
		conditions.append(f"`tabMobile App Appointment`.`doctor_name` = {escaped_name}")
	return f"({' OR '.join(conditions)})"
