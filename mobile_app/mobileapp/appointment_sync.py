"""Mirror Mobile App User appointment child rows to standalone Mobile App Appointment."""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from mobile_app.mobileapp.appointment_utils import (
	apply_consultation_rules,
	child_row_to_appointment_dict,
)


def stored_values(doc):
    """Compare persisted values, normalizing Frappe dates and numeric field types."""
    values = doc.get_valid_dict(convert_dates_to_str=True)
    from frappe.utils import getdate, get_datetime, get_time
    for field in doc.meta.fields:
        value = values.get(field.fieldname)
        if value in (None, ''): continue
        convert = {'Date':getdate, 'Datetime':get_datetime, 'Time':get_time}.get(field.fieldtype)
        if convert: values[field.fieldname] = str(convert(value))
    return values


def sync_appointments_from_user(user_doc: Document) -> list[str]:
	"""Upsert standalone appointments from parent child table. Returns synced names."""
	if frappe.flags.get("in_appointment_sync_from_user"):
		return []

	synced: list[str] = []
	frappe.flags.in_appointment_sync_from_user = True
	try:
		for row in user_doc.appointments or []:
			name = _upsert_appointment_from_child(row, user_doc.name)
			if name:
				synced.append(name)
	finally:
		frappe.flags.in_appointment_sync_from_user = False
	return synced


def _upsert_appointment_from_child(row, parent_name: str) -> str | None:
	fields = child_row_to_appointment_dict(row, parent_name)
	ext_id = fields.get("appointment_external_id")
	if not ext_id:
		return None

	exists = frappe.db.exists("Mobile App Appointment", ext_id)
	if exists:
		doc = frappe.get_doc("Mobile App Appointment", ext_id)
		before = stored_values(doc)
		preserved_agent = doc.assigned_agent
		doc.update({k: v for k, v in fields.items() if k not in ("doctype", "appointment_external_id")})
		if preserved_agent and not doc.assigned_agent:
			doc.assigned_agent = preserved_agent
	else:
		doc = frappe.get_doc(fields)

	apply_consultation_rules(doc)
	doc.flags.ignore_permissions = True
	if exists:
		if stored_values(doc) != before:
			doc.save()
	else:
		doc.insert()
	return doc.name


def backfill_all_appointments() -> int:
	"""Migration: sync every Mobile App User's appointments to standalone."""
	count = 0
	for name in frappe.get_all("Mobile App User", pluck="name"):
		doc = frappe.get_doc("Mobile App User", name)
		count += len(sync_appointments_from_user(doc))
	return count
