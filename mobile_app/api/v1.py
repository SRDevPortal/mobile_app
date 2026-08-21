"""
Single-DocType API for Mobile App.

All mobile data now lives under `Mobile App User` and its child tables.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import frappe
from frappe import _
from mobile_app.api.auth import get_expected_app_token, require_app_token
from mobile_app.mobileapp.appointment_sync import sync_appointments_from_user


@contextmanager
def ignore_permissions():
	prev = bool(frappe.flags.get("ignore_permissions"))
	frappe.flags.ignore_permissions = True
	try:
		yield
	finally:
		frappe.flags.ignore_permissions = prev


def _parse_body() -> dict[str, Any]:
	"""Parse JSON POST body; always decode bytes first (parse_json can return bytes unchanged otherwise)."""
	raw = frappe.request.data
	if not raw:
		return {}
	if isinstance(raw, bytes):
		raw = raw.decode("utf-8")
	try:
		parsed = frappe.parse_json(raw)
	except Exception:
		frappe.throw(_("Invalid JSON body"))
	if not isinstance(parsed, dict):
		frappe.throw(_("JSON body must be a JSON object"))
	return parsed


def _ok(data: Any, http_status: int | None = None) -> dict[str, Any]:
	if http_status:
		frappe.local.response.http_status_code = http_status
	return {"success": True, "data": data}


def _err(message: str, http_status: int = 400) -> None:
	frappe.local.response.http_status_code = http_status
	frappe.throw(message)


def _doc_payload(doc) -> dict[str, Any]:
	return doc.as_dict(convert_dates_to_str=True, no_private_properties=True, no_nulls=False)


def _mobile_app_user_api_payload(doc) -> dict[str, Any]:
	data = _doc_payload(doc)
	path = data.get("image")
	data["profile_image_url"] = frappe.utils.get_url(path) if path else None
	return data


def _replace_child_table(doc, fieldname: str, rows: list[dict[str, Any]] | None) -> None:
	if rows is None:
		return
	if not isinstance(rows, list):
		_err(_("{0} must be an array").format(fieldname))
	doc.set(fieldname, [])
	for row in rows:
		if isinstance(row, dict):
			doc.append(fieldname, row)


def _find_user_name(p: dict[str, Any]) -> str | None:
	if p.get("external_id") and frappe.db.exists("Mobile App User", p["external_id"]):
		return p["external_id"]
	if p.get("id") and frappe.db.exists("Mobile App User", p["id"]):
		return p["id"]
	if p.get("customer_id") and frappe.db.exists("Mobile App User", p["customer_id"]):
		return p["customer_id"]
	for field in ("supabase_user_id", "email", "phone"):
		if p.get(field):
			name = frappe.db.get_value("Mobile App User", {field: p[field]}, "name")
			if name:
				return name
	return None


@frappe.whitelist(allow_guest=True, methods=["GET"])
def health():
	token_ok = bool(get_expected_app_token())
	return {
		"success": True,
		"service": "mobile_app",
		"frappe": {
			"baseUrlConfigured": bool(frappe.utils.get_url()),
			"tokenConfigured": bool(frappe.conf.get("api_key") or frappe.conf.get("api_secret")),
			"appTokenConfigured": token_ok,
			"doctypes": {
				"MOBILE_APP_USER": "Mobile App User",
				"MOBILE_APP_USER_PROFILE_ITEM": "Mobile App User Profile Item",
				"MOBILE_APP_USER_SESSION_ITEM": "Mobile App User Session Item",
				"MOBILE_APP_MEDICAL_ITEM": "Mobile App Medical Item",
				"MOBILE_APP_HEALTH_ENTRY_ITEM": "Mobile App Health Entry Item",
				"MOBILE_APP_APPOINTMENT_ITEM": "Mobile App Appointment Item",
				"MOBILE_APP_APPOINTMENT": "Mobile App Appointment",
				"MOBILE_APP_ENGAGEMENT_ITEM": "Mobile App Engagement Item",
			},
		},
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def users_sync():
	"""Backward-compatible basic upsert for the single parent doctype."""
	require_app_token()
	data = _parse_body()
	name = data.get("external_id")
	if not name:
		_err(_("external_id is required"))

	fields = {
		"doctype": "Mobile App User",
		"external_id": name,
		"supabase_user_id": data.get("supabase_user_id"),
		"email": data.get("email"),
		"phone": data.get("phone"),
		"full_name": data.get("full_name"),
		"first_name": data.get("first_name"),
		"last_name": data.get("last_name"),
		"is_active": 1 if data.get("is_active", True) else 0,
		"last_login_at": data.get("last_login_at"),
	}
	with ignore_permissions():
		if frappe.db.exists("Mobile App User", name):
			doc = frappe.get_doc("Mobile App User", name)
			doc.update({k: v for k, v in fields.items() if k not in ("doctype", "external_id") and v is not None})
			doc.save(ignore_permissions=True)
		else:
			doc = frappe.get_doc(fields).insert(ignore_permissions=True)
	return _ok(_mobile_app_user_api_payload(doc), 200)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def users_lookup():
	require_app_token()
	p = dict(frappe.local.form_dict or {})
	user = _find_user_name(p)
	if not user:
		frappe.local.response.http_status_code = 404
		frappe.throw(_("User not found"))
	doc = frappe.get_doc("Mobile App User", user)
	return _ok(_mobile_app_user_api_payload(doc), 200)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def users_full_sync():
	"""Single endpoint for user + all tabbed child-table data."""
	require_app_token()
	data = _parse_body()
	name = data.get("external_id")
	if not name:
		_err(_("external_id is required"))

	fields = {
		"doctype": "Mobile App User",
		"external_id": name,
		"supabase_user_id": data.get("supabase_user_id"),
		"email": data.get("email"),
		"phone": data.get("phone"),
		"full_name": data.get("full_name"),
		"first_name": data.get("first_name"),
		"last_name": data.get("last_name"),
		"is_active": 1 if data.get("is_active", True) else 0,
		"last_login_at": data.get("last_login_at"),
	}

	with ignore_permissions():
		if frappe.db.exists("Mobile App User", name):
			doc = frappe.get_doc("Mobile App User", name)
			doc.update({k: v for k, v in fields.items() if k not in ("doctype", "external_id") and v is not None})
		else:
			doc = frappe.get_doc(fields)

		_replace_child_table(doc, "profiles", data.get("profiles"))
		_replace_child_table(doc, "sessions", data.get("sessions"))
		_replace_child_table(doc, "medical_items", data.get("medical_items"))
		_replace_child_table(doc, "health_entries", data.get("health_entries"))
		_replace_child_table(doc, "appointments", data.get("appointments"))
		_replace_child_table(doc, "engagement_items", data.get("engagement_items"))
		doc.save(ignore_permissions=True)
		sync_appointments_from_user(doc)

	return _ok(_mobile_app_user_api_payload(doc), 200)
