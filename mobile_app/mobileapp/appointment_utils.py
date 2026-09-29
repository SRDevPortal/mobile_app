"""Consultation / Online vs OPD helpers for Mobile App Appointment."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import frappe
from frappe import _

OPD_CONSULTATION_LABEL = "OPD Consultation"

_OPD_ALIASES = frozenset(
	{
		"opd",
		"opd consultation",
		"offline",
		"in-person",
		"in person",
	}
)

_MEET_HOST_SUFFIXES = ("meet.google.com",)


def _norm(value: Any) -> str:
	if value is None:
		return ""
	return str(value).strip()


def is_google_meet_url(value: Any) -> bool:
	text = _norm(value)
	if not text:
		return False
	lower = text.lower()
	if "meet.google.com" in lower:
		return True
	try:
		parsed = urlparse(text if "://" in text else f"https://{text}")
	except Exception:
		return False
	host = (parsed.netloc or "").lower()
	return any(host == suffix or host.endswith(f".{suffix}") for suffix in _MEET_HOST_SUFFIXES)


def normalize_meet_url(value: Any) -> str:
	text = _norm(value)
	if not text:
		return ""
	if is_google_meet_url(text):
		if not text.lower().startswith(("http://", "https://")):
			return f"https://{text}"
		return text
	return text


def is_opd_consultation(value: Any) -> bool:
	text = _norm(value).lower()
	if not text:
		return False
	if text == OPD_CONSULTATION_LABEL.lower():
		return True
	return text in _OPD_ALIASES


def is_online_appointment_type(value: Any) -> bool:
	text = _norm(value).lower()
	return text in ("online", "video", "teleconsult", "tele-consult")


def apply_consultation_rules(
	doc: Any,
	*,
	appointment_type_hint: str | None = None,
	strict_online: bool = False,
) -> None:
	"""Set consultation_type, is_online, google_meet_link from Consultation value."""
	consultation = _norm(getattr(doc, "consultation_type", None))
	hint = _norm(appointment_type_hint) or _norm(getattr(doc, "payload_json", None) and _payload_appointment_type(doc))

	if is_google_meet_url(consultation):
		doc.is_online = 1
		doc.google_meet_link = normalize_meet_url(consultation)
		doc.consultation_type = doc.google_meet_link
		return

	if is_opd_consultation(consultation) or (not consultation and is_opd_appointment_type(hint)):
		doc.is_online = 0
		doc.google_meet_link = None
		doc.consultation_type = OPD_CONSULTATION_LABEL
		return

	if is_online_appointment_type(hint) or (strict_online and is_online_appointment_type(consultation)):
		frappe.throw(
			_("Online appointments require a Google Meet link in Consultation (meet.google.com).")
		)

	if consultation:
		# Unknown text: treat as OPD unless hint says online
		if is_online_appointment_type(hint):
			frappe.throw(
				_("Online appointments require a Google Meet link in Consultation (meet.google.com).")
			)
		doc.is_online = 0
		doc.google_meet_link = None
		doc.consultation_type = OPD_CONSULTATION_LABEL
		return

	doc.is_online = 0
	doc.google_meet_link = None
	doc.consultation_type = OPD_CONSULTATION_LABEL


def is_opd_appointment_type(value: Any) -> bool:
	text = _norm(value).lower()
	return text in ("opd", "offline", "in-person", "in person") or text == OPD_CONSULTATION_LABEL.lower()


def _payload_appointment_type(doc: Any) -> str | None:
	payload = getattr(doc, "payload_json", None)
	if not payload:
		return None
	if isinstance(payload, str):
		try:
			payload = frappe.parse_json(payload)
		except Exception:
			return None
	if isinstance(payload, dict):
		for key in ("appointment_type", "appointmentType"):
			if payload.get(key):
				return str(payload[key])
	return None


def resolve_mobile_app_user_link(user_id: Any, parent_name: str | None = None) -> str | None:
	"""Map child user_id / parent external_id to Mobile App User name."""
	uid = _norm(user_id)
	if uid and frappe.db.exists("Mobile App User", uid):
		return uid
	if parent_name and frappe.db.exists("Mobile App User", parent_name):
		return parent_name
	if uid:
		name = frappe.db.get_value("Mobile App User", {"external_id": uid}, "name")
		if name:
			return name
		name = frappe.db.get_value("Mobile App User", {"supabase_user_id": uid}, "name")
		if name:
			return name
	return parent_name if parent_name and frappe.db.exists("Mobile App User", parent_name) else None


def resolve_doctor_user(doctor_name: Any) -> str | None:
	name = _norm(doctor_name)
	if not name:
		return None
	users = frappe.get_all(
		"User",
		filters={"enabled": 1},
		fields=["name", "full_name"],
		limit=0,
	)
	for u in users:
		if _norm(u.full_name).lower() == name.lower():
			return u.name
		if _norm(u.name).lower() == name.lower():
			return u.name
	return None


def child_row_to_appointment_dict(row: Any, parent_name: str) -> dict[str, Any]:
	"""Build Mobile App Appointment field dict from child row."""
	data = row.as_dict() if hasattr(row, "as_dict") else dict(row)
	appointment_external_id = _norm(data.get("appointment_external_id"))
	if not appointment_external_id:
		return {}

	mobile_app_user = resolve_mobile_app_user_link(data.get("user_id"), parent_name)
	doctor_user = resolve_doctor_user(data.get("doctor_name"))

	out = {
		"doctype": "Mobile App Appointment",
		"appointment_external_id": appointment_external_id,
		"booking_id": data.get("booking_id"),
		"submission_timestamp": data.get("submission_timestamp"),
		"appointment_date": data.get("appointment_date"),
		"appointment_time": data.get("appointment_time"),
		"doctor_name": data.get("doctor_name"),
		"practitioner_id": data.get("practitioner_id"),
		"practitioner_schedule": data.get("practitioner_schedule"),
		"duration": data.get("duration"),
		"patient_name": data.get("patient_name"),
		"mobile_number": data.get("mobile_number"),
		"email": data.get("email"),
		"consultation_type": data.get("consultation_type"),
		"appointment_for": data.get("appointment_for"),
		"page_url_disease": data.get("page_url_disease"),
		"status": data.get("status"),
		"email_status": data.get("email_status"),
		"whatsapp_status": data.get("whatsapp_status"),
		"payload_json": data.get("payload_json"),
		"mobile_app_user": mobile_app_user,
		"doctor_user": doctor_user,
	}
	return {k: v for k, v in out.items() if v is not None}
