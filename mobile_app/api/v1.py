"""
Single-DocType API for Mobile App.

All mobile data now lives under `Mobile App User` and its child tables.
"""

from __future__ import annotations

import re
import hashlib
import time
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


def _user_lock_name(identity: str) -> str:
	"""Return a bounded, non-sensitive MariaDB advisory-lock name."""
	digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
	return f"mobile_app_user:{digest}"


@contextmanager
def user_operation_lock(identity: str, timeout_seconds: int = 10):
	"""Serialize provisioning, full sync and erasure for one mobile user."""
	lock_name = _user_lock_name(identity)
	acquired = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (lock_name, timeout_seconds))[0][0]
	if acquired != 1:
		frappe.local.response.http_status_code = 409
		frappe.throw(_("Another operation is in progress for this user. Please retry."))
	try:
		yield
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_name,))


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


_INVALID_NAME_CHARS = re.compile(r"[<>]")

# Frappe metadata is stripped; existing profile names are separately checked against their owner.
_CHILD_ROW_META_KEYS = frozenset(
	{
		"name",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"parent",
		"parentfield",
		"parenttype",
		"idx",
		"docstatus",
		"doctype",
	}
)


_USER_ID_KEYS = ("supabase_user_id", "id", "customer_id", "external_id")


def _sanitize_frappe_name(value: Any) -> str:
	"""Make a value safe for Frappe document names (strips < and >)."""
	if value is None:
		return ""
	name = str(value).strip()
	if not name:
		return ""
	return _INVALID_NAME_CHARS.sub("", name).strip()


def _resolve_user_external_id(data: dict[str, Any]) -> str:
	"""Pick a Frappe-safe document name; prefer UUID fields over email-style ids."""
	unsafe: list[str] = []
	for key in _USER_ID_KEYS:
		raw = data.get(key)
		if raw is None or raw == "":
			continue
		stripped = str(raw).strip()
		if not stripped:
			continue
		if not _INVALID_NAME_CHARS.search(stripped):
			return stripped
		unsafe.append(stripped)

	for raw in unsafe:
		sanitized = _sanitize_frappe_name(raw)
		if sanitized:
			return sanitized
	return ""


def _get_existing_user_name(data: dict[str, Any]) -> str | None:
	"""Resolve id and find an existing Mobile App User if present."""
	name = _resolve_user_external_id(data)
	if name and frappe.db.exists("Mobile App User", name):
		return name
	return _find_user_name(data)


def _clean_child_row(row: dict[str, Any]) -> dict[str, Any]:
	return {k: v for k, v in row.items() if k not in _CHILD_ROW_META_KEYS}


def _replace_child_table(doc, fieldname: str, rows: list[dict[str, Any]] | None) -> None:
	if rows is None:
		return
	if not isinstance(rows, list):
		_err(_("{0} must be an array").format(fieldname))
	# A profile row is also a chat identity. Preserve echoed IDs only when
	# they belong to this user; never attach another user's child row.
	existing_profiles = {row.name for row in doc.get("profiles") or []} if fieldname == "profiles" else set()
	seen = set()
	cleaned_rows = []
	for row in rows:
		if not isinstance(row, dict):
			continue
		cleaned = _clean_child_row(row)
		if fieldname == "profiles" and row.get("name"):
			profile_id = str(row["name"])
			if profile_id not in existing_profiles or profile_id in seen:
				frappe.throw(_("The selected profile does not belong to this user or is repeated."), frappe.PermissionError)
			seen.add(profile_id)
			cleaned["name"] = profile_id
		cleaned_rows.append(cleaned)
	doc.set(fieldname, [])
	for row in cleaned_rows:
		doc.append(fieldname, row)


def _find_user_name(p: dict[str, Any]) -> str | None:
	for key in _USER_ID_KEYS:
		raw = p.get(key)
		if not raw:
			continue
		for candidate in (str(raw).strip(), _sanitize_frappe_name(raw)):
			if candidate and frappe.db.exists("Mobile App User", candidate):
				return candidate
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
	name = _resolve_user_external_id(data)
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
	with user_operation_lock(name), ignore_permissions():
		existing = _get_existing_user_name(data)
		if existing:
			doc = frappe.get_doc("Mobile App User", existing)
			doc.update({k: v for k, v in fields.items() if k not in ("doctype", "external_id") and v is not None})
			doc.save(ignore_permissions=True)
		else:
			doc = frappe.get_doc(fields).insert(ignore_permissions=True)
		# The advisory lock must cover the database commit, not just doc.save().
		frappe.db.commit()
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
	name = _resolve_user_external_id(data)
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

	with user_operation_lock(name), ignore_permissions():
		existing = _get_existing_user_name(data)
		if existing:
			doc = frappe.get_doc("Mobile App User", existing)
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
		frappe.db.commit()

	return _ok(_mobile_app_user_api_payload(doc), 200)


_STANDALONE_USER_DOCTYPES = (
	"Mobile App Notification",
	"Mobile App Appointment",
	"Mobile App Prescription",
	"Mobile App Health Entry",
	"Mobile App User Disease Selection",
	"Mobile App User Profile",
	"Mobile App User Session",
)


def _is_lock_timeout(exc: Exception) -> bool:
	message = str(exc).lower()
	return "1205" in message or "lock wait timeout" in message


def _delete_standalone_user_rows(user_name: str) -> dict[str, int]:
	deleted: dict[str, int] = {}
	for doctype in _STANDALONE_USER_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		link_field = "user_id" if meta.has_field("user_id") else "mobile_app_user" if meta.has_field("mobile_app_user") else None
		if not link_field:
			continue
		names = frappe.get_all(doctype, filters={link_field: user_name}, pluck="name")
		for docname in names:
			frappe.delete_doc(doctype, docname, ignore_permissions=True)
		deleted[doctype] = len(names)
	return deleted


@frappe.whitelist(allow_guest=True, methods=["POST"])
def users_delete():
	"""Idempotently erase one ERP user in a serialized, retryable transaction."""
	require_app_token()
	data = _parse_body()
	identity = _resolve_user_external_id(data)
	if not identity:
		_err(_("external_id or supabase_user_id is required"))

	for attempt in range(3):
		try:
			with user_operation_lock(identity), ignore_permissions():
				user_name = _find_user_name(data)
				if not user_name:
					frappe.db.commit()
					return _ok({"already_deleted": True, "deleted": {}}, 200)
				deleted = _delete_standalone_user_rows(user_name)
				frappe.delete_doc("Mobile App User", user_name, ignore_permissions=True)
				deleted["Mobile App User"] = 1
				frappe.db.commit()
				return _ok({"already_deleted": False, "deleted": deleted}, 200)
		except Exception as exc:
			frappe.db.rollback()
			if not _is_lock_timeout(exc) or attempt == 2:
				raise
			time.sleep(0.25 * (2**attempt))

	_err(_("Unable to delete user"), 500)
