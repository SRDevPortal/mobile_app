"""Support ticket inbox API for Mobile App User."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import convert_utc_to_system_timezone, get_datetime, now_datetime
from mobile_app.api.auth import require_app_token

SUPPORT_TICKET_STATUSES = {"Open", "In Progress", "Resolved", "Closed"}


def _is_desk_session() -> bool:
	request = getattr(frappe.local, "request", None)
	has_session_cookie = bool(getattr(request, "cookies", {}).get("sid")) if request else False
	return bool(has_session_cookie and frappe.session.user and frappe.session.user != "Guest")


def _require_mobile_app_token() -> None:
	if _is_desk_session():
		return
	require_app_token()


def _ticket_names(doc) -> list[str]:
	or_filters = [{"mobile_app_user": doc.name}, {"user_id": doc.name}]
	if doc.external_id and doc.external_id != doc.name:
		or_filters.append({"user_id": doc.external_id})
	return frappe.get_all(
		"App Support Ticket",
		or_filters=or_filters,
		order_by="modified desc",
		pluck="name",
	)


def _messages_by_ticket(ticket_names: list[str]) -> dict[str, list[dict[str, Any]]]:
	grouped = {name: [] for name in ticket_names}
	if not ticket_names:
		return grouped

	for row in frappe.get_all(
		"App Support Ticket Message",
		filters={"ticket": ["in", ticket_names]},
		fields=[
			"ticket",
			"sender_type",
			"sender_id",
			"sender_name",
			"message",
			"timestamp",
			"creation",
			"is_read",
		],
		order_by="creation asc",
	):
		timestamp = _display_timestamp(row)
		grouped.setdefault(row.ticket, []).append(
			{
				"from": "agent" if row.sender_type == "Agent" else "customer",
				"sender_id": row.sender_id,
				"sender_name": row.sender_name,
				"body": row.message,
				"timestamp": timestamp,
				"is_read": int(row.is_read or 0),
			}
		)
	return grouped


def _display_timestamp(row):
	"""Return message timestamp in system timezone when mobile sends UTC as naive datetime."""
	if not row.timestamp:
		return row.timestamp
	if row.sender_type == "Agent" or not row.creation:
		return row.timestamp

	try:
		raw_ts = get_datetime(row.timestamp)
		created_at = get_datetime(row.creation)
		system_ts = convert_utc_to_system_timezone(raw_ts).replace(tzinfo=None)
	except Exception:
		return row.timestamp

	raw_distance = abs((created_at - raw_ts).total_seconds())
	system_distance = abs((created_at - system_ts).total_seconds())
	offset = abs((system_ts - raw_ts).total_seconds())

	if offset <= 14 * 60 * 60 and system_distance < raw_distance:
		return system_ts
	return row.timestamp


def _unanswered_user_message_count(messages: list[dict[str, Any]]) -> int:
	last_agent_index = -1
	for idx, message in enumerate(messages):
		if message.get("from") == "agent":
			last_agent_index = idx

	return sum(
		1
		for message in messages[last_agent_index + 1 :]
		if message.get("from") == "customer"
	)


def _profile_image_url(doc) -> str:
	image = (doc.image or "").strip()
	if not image:
		return ""
	if image.startswith(("http://", "https://", "/")):
		return image
	return frappe.utils.get_url(image)


def _ticket_rows(doc) -> list[dict[str, Any]]:
	ticket_names = _ticket_names(doc)
	messages_by_ticket = _messages_by_ticket(ticket_names)
	profile_image_url = _profile_image_url(doc)
	tickets = {
		row.name: row
		for row in frappe.get_all(
			"App Support Ticket",
			filters={"name": ["in", ticket_names]},
			fields=["name", "ticket_number", "subject", "description", "status", "category", "modified", "creation"],
		)
	}
	rows = []
	for name in ticket_names:
		ticket = tickets.get(name)
		if not ticket:
			continue
		messages = messages_by_ticket.get(name) or []
		last_message = messages[-1] if messages else {}
		preview = last_message.get("body") or ticket.description or ticket.subject or ""
		unread_count = _unanswered_user_message_count(messages)
		rows.append(
			{
				"name": ticket.name,
				"external_id": ticket.ticket_number,
				"subject": ticket.subject or _("Support Ticket"),
				"description": ticket.description or "",
				"status": ticket.status or "Open",
				"recorded_at": last_message.get("timestamp") or ticket.modified or ticket.creation,
				"preview": str(preview)[:200],
				"label": ticket.category or "",
				"profile_image_url": profile_image_url,
				"unread_count": unread_count,
				"messages": messages,
			}
		)
	rows.sort(key=lambda row: str(row.get("recorded_at") or ""), reverse=True)
	return rows


def _get_user_doc(mobile_app_user: str):
	if not mobile_app_user or not frappe.db.exists("Mobile App User", mobile_app_user):
		frappe.throw(_("Mobile App User not found"))
	doc = frappe.get_doc("Mobile App User", mobile_app_user)
	if _is_desk_session():
		doc.check_permission("read")
	return doc


def _ticket_belongs_to_user(ticket, doc) -> bool:
	return bool(
		ticket.mobile_app_user == doc.name
		or ticket.user_id == doc.name
		or (doc.external_id and ticket.user_id == doc.external_id)
	)


def _mark_ticket_user_messages_read(ticket_name: str):
	unread_messages = frappe.get_all(
		"App Support Ticket Message",
		filters={
			"ticket": ticket_name,
			"sender_type": "User",
			"is_read": 0,
		},
		pluck="name",
	)
	if not unread_messages:
		return

	now = now_datetime()
	for message_name in unread_messages:
		frappe.db.set_value(
			"App Support Ticket Message",
			message_name,
			{"is_read": 1, "read_at": now},
			update_modified=False,
		)


@frappe.whitelist(allow_guest=True)
def get_support_tickets(mobile_app_user: str) -> dict[str, Any]:
	_require_mobile_app_token()
	doc = _get_user_doc(mobile_app_user)
	return {"tickets": _ticket_rows(doc)}


@frappe.whitelist(allow_guest=True)
def send_support_reply(mobile_app_user: str, ticket_name: str, message: str) -> dict[str, Any]:
	_require_mobile_app_token()
	message = (message or "").strip()
	if not message:
		frappe.throw(_("Message is required"))

	doc = _get_user_doc(mobile_app_user)
	if not ticket_name or not frappe.db.exists("App Support Ticket", ticket_name):
		frappe.throw(_("Support ticket not found"))

	ticket = frappe.get_doc("App Support Ticket", ticket_name)
	if not _ticket_belongs_to_user(ticket, doc):
		frappe.throw(_("Support ticket not found"))

	frappe.get_doc(
		{
			"doctype": "App Support Ticket Message",
			"ticket": ticket.name,
			"sender_type": "Agent",
			"sender_id": frappe.session.user,
			"sender_name": frappe.utils.get_fullname(frappe.session.user) or frappe.session.user,
			"message": message,
			"timestamp": now_datetime(),
			"is_read": 1,
		}
	).insert()
	_mark_ticket_user_messages_read(ticket.name)
	ticket.save()

	updated = next((row for row in _ticket_rows(doc) if row["name"] == ticket.name), None)
	return {"ticket": updated, "tickets": _ticket_rows(doc)}


@frappe.whitelist(allow_guest=True)
def mark_support_ticket_read(mobile_app_user: str, ticket_name: str) -> dict[str, Any]:
	_require_mobile_app_token()
	doc = _get_user_doc(mobile_app_user)
	if not ticket_name or not frappe.db.exists("App Support Ticket", ticket_name):
		frappe.throw(_("Support ticket not found"))

	ticket = frappe.get_doc("App Support Ticket", ticket_name)
	if not _ticket_belongs_to_user(ticket, doc):
		frappe.throw(_("Support ticket not found"))

	_mark_ticket_user_messages_read(ticket.name)

	updated = next((row for row in _ticket_rows(doc) if row["name"] == ticket.name), None)
	return {"ticket": updated, "tickets": _ticket_rows(doc)}


@frappe.whitelist(allow_guest=True)
def set_support_ticket_status(mobile_app_user: str, ticket_name: str, status: str) -> dict[str, Any]:
	_require_mobile_app_token()
	status = (status or "").strip()
	if status not in SUPPORT_TICKET_STATUSES:
		frappe.throw(_("Invalid support ticket status"))

	doc = _get_user_doc(mobile_app_user)
	if not ticket_name or not frappe.db.exists("App Support Ticket", ticket_name):
		frappe.throw(_("Support ticket not found"))

	ticket = frappe.get_doc("App Support Ticket", ticket_name)
	if not _ticket_belongs_to_user(ticket, doc):
		frappe.throw(_("Support ticket not found"))

	ticket.status = status
	if status == "In Progress":
		ticket.resolved_at = None
		ticket.closed_at = None
	elif status == "Resolved":
		ticket.closed_at = None
	elif status == "Closed" and not ticket.resolved_at:
		ticket.resolved_at = now_datetime()
	ticket.save()

	updated = next((row for row in _ticket_rows(doc) if row["name"] == ticket.name), None)
	return {"ticket": updated, "tickets": _ticket_rows(doc)}


@frappe.whitelist(allow_guest=True)
def delete_support_ticket(mobile_app_user: str, ticket_name: str) -> dict[str, Any]:
	_require_mobile_app_token()
	doc = _get_user_doc(mobile_app_user)
	if not ticket_name or not frappe.db.exists("App Support Ticket", ticket_name):
		frappe.throw(_("Support ticket not found"))

	ticket = frappe.get_doc("App Support Ticket", ticket_name)
	if not _ticket_belongs_to_user(ticket, doc):
		frappe.throw(_("Support ticket not found"))
	ticket.check_permission("delete")

	for message_name in frappe.get_all(
		"App Support Ticket Message",
		filters={"ticket": ticket.name},
		pluck="name",
	):
		frappe.delete_doc("App Support Ticket Message", message_name, ignore_permissions=True)

	frappe.delete_doc("App Support Ticket", ticket.name)
	return {"tickets": _ticket_rows(doc)}
