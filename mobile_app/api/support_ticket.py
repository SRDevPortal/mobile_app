"""Support ticket inbox API for Mobile App User."""

from __future__ import annotations

from typing import Any
from html import unescape

import frappe
from frappe import _
from frappe.utils import convert_utc_to_system_timezone, get_datetime, now_datetime
from mobile_app.api.auth import require_app_token

SUPPORT_TICKET_STATUSES = {"Open", "In Progress", "Waiting for Customer", "Resolved", "Closed"}
SOURCES = {
    "App Support Ticket": ("App Support Ticket Message", "mobile_app_user"),
    "Support Ticket": ("Support Ticket Message", "custom_mobile_app_user"),
}


def _source(ticket_doctype):
    if ticket_doctype not in SOURCES:
        frappe.throw(_("Invalid support ticket type"))
    return SOURCES[ticket_doctype]


def _authenticated():
    return bool(frappe.session.user and frappe.session.user != "Guest")


def _plain(value):
    return unescape(frappe.utils.strip_html(str(value or "").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")))


def _load_ticket(doc, ticket_name, ticket_doctype, permission="read"):
    _source(ticket_doctype)
    if not frappe.db.exists(ticket_doctype, ticket_name):
        frappe.throw(_("Support ticket not found"))
    ticket = frappe.get_doc(ticket_doctype, ticket_name)
    if not _ticket_belongs_to_user(ticket, doc, ticket_doctype):
        frappe.throw(_("Support ticket not found"))
    ticket.check_permission(permission)
    return ticket



def _is_desk_session() -> bool:
	request = getattr(frappe.local, "request", None)
	has_session_cookie = bool(getattr(request, "cookies", {}).get("sid")) if request else False
	return bool(has_session_cookie and frappe.session.user and frappe.session.user != "Guest")


def _require_mobile_app_token() -> None:
	if _authenticated():
		return
	require_app_token()


def _ticket_names(doc, ticket_doctype):
    _, link_field = _source(ticket_doctype)
    has_link = frappe.get_meta(ticket_doctype).has_field(link_field)
    or_filters = [{"user_id": doc.name}]
    if has_link:
        or_filters.append({link_field: doc.name})
    if doc.external_id and doc.external_id != doc.name:
        or_filters.append({"user_id": doc.external_id})
    reader = frappe.get_list if _authenticated() else frappe.get_all
    rows = reader(ticket_doctype, or_filters=or_filters,
        fields=["name", "user_id"] + ([link_field] if has_link else []),
        order_by="modified desc", limit_page_length=0)
    return [row.name for row in rows if _ticket_belongs_to_user(row, doc, ticket_doctype)]


def _messages_by_ticket(ticket_names: list[str], ticket_doctype) -> dict[str, list[dict[str, Any]]]:
	grouped = {name: [] for name in ticket_names}
	if not ticket_names:
		return grouped

	reader = frappe.get_list if _authenticated() else frappe.get_all
	for row in reader(
		_source(ticket_doctype)[0],
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
		order_by="timestamp asc, creation asc, name asc",
		limit_page_length=0,
	):
		timestamp = _display_timestamp(row) if ticket_doctype == "App Support Ticket" else row.timestamp
		grouped.setdefault(row.ticket, []).append(
			{
				"from": "agent" if row.sender_type == "Agent" else "customer",
				"sender_id": row.sender_id,
				"sender_name": row.sender_name,
				"body": _plain(row.message) if ticket_doctype == "Support Ticket" else row.message,
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
		if message.get("from") == "customer" and not message.get("is_read")
	)


def _profile_image_url(doc) -> str:
	image = (doc.image or "").strip()
	if not image:
		return ""
	if image.startswith(("http://", "https://", "/")):
		return image
	return frappe.utils.get_url(image)


def _ticket_rows(doc) -> list[dict[str, Any]]:
    rows = []
    for ticket_doctype in SOURCES:
        if not frappe.db.exists("DocType", ticket_doctype):
            continue
        if not _authenticated() and ticket_doctype != "App Support Ticket":
            continue
        if _authenticated() and not frappe.has_permission(ticket_doctype, "read"):
            continue
        rows.extend(_source_rows(doc, ticket_doctype))
    return sorted(rows, key=lambda row: str(row.get("recorded_at") or ""), reverse=True)


def _source_rows(doc, ticket_doctype, ticket_names=None):
	if ticket_names is None:
		ticket_names = _ticket_names(doc, ticket_doctype)
	messages_by_ticket = _messages_by_ticket(ticket_names, ticket_doctype)
	profile_image_url = _profile_image_url(doc)
	tickets = {
		row.name: row
		for row in frappe.get_all(
			ticket_doctype,
			filters={"name": ["in", ticket_names]},
			fields=["name", "ticket_number", "subject", "description", "status", "category", "modified", "creation"],
		)
	}
	rows = []
	for name in ticket_names:
		ticket = tickets.get(name)
		if not ticket:
			continue
		if ticket_doctype == "Support Ticket":
			ticket.description = _plain(ticket.description)
		messages = messages_by_ticket.get(name) or []
		last_message = messages[-1] if messages else {}
		preview = last_message.get("body") or ticket.description or ticket.subject or ""
		unread_count = _unanswered_user_message_count(messages)
		rows.append(
			{
				"name": ticket.name,
				"ticket_doctype": ticket_doctype,
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
	if _authenticated():
		doc.check_permission("read")
	return doc


def _ticket_belongs_to_user(ticket, doc, ticket_doctype="App Support Ticket") -> bool:
    link = ticket.get(_source(ticket_doctype)[1])
    identities = {doc.name, doc.external_id} - {None, ""}
    # An explicit link to another account must never be overridden by user_id.
    if link:
        return link == doc.name and (not ticket.get("user_id") or ticket.get("user_id") in identities)
    return ticket.get("user_id") in identities


def _mark_ticket_user_messages_read(ticket_name: str, ticket_doctype):
	message_type = _source(ticket_doctype)[0]
	frappe.has_permission(message_type, "write", throw=True)
	unread_messages = frappe.get_all(
		message_type,
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
			message_type,
			message_name,
			{"is_read": 1, "read_at": now},
			update_modified=False,
		)

	frappe.publish_realtime("mobile_app_data_changed", {"doctype": message_type}, room="all", after_commit=True)


@frappe.whitelist(allow_guest=True)
def get_support_tickets(mobile_app_user: str) -> dict[str, Any]:
	_require_mobile_app_token()
	doc = _get_user_doc(mobile_app_user)
	return {"tickets": _ticket_rows(doc)}


@frappe.whitelist(allow_guest=True)
def send_support_reply(mobile_app_user: str, ticket_name: str, message: str, ticket_doctype: str = "App Support Ticket", _summary=False) -> dict[str, Any]:
	_require_mobile_app_token()
	if _summary and not _authenticated():
		frappe.throw(_("Login required"), frappe.PermissionError)
	message = (message or "").strip()
	if not message:
		frappe.throw(_("Message is required"))

	doc = _get_user_doc(mobile_app_user)
	ticket = _load_ticket(doc, ticket_name, ticket_doctype, "write")

	if ticket.status == "Closed":
		frappe.throw(_("Reopen the ticket before replying."))
	if len(message) > 10000:
		frappe.throw(_("Message is too long"))
	frappe.get_doc(
		{
			"doctype": _source(ticket_doctype)[0],
			"ticket": ticket.name,
			"sender_type": "Agent",
			"sender_id": frappe.session.user,
			"sender_name": frappe.utils.get_fullname(frappe.session.user) or frappe.session.user,
			"message": message,
			"timestamp": now_datetime(),
			"is_read": 0,
		}
	).insert()
	_mark_ticket_user_messages_read(ticket.name, ticket_doctype)
	# Message insertion updates ticket activity; avoid saving the stale document.

	if _summary:
		from mobile_app.api.desk_support import ticket_summary
		return {"ticket": ticket_summary(doc, ticket_doctype, ticket.name)}

	updated = next((row for row in _ticket_rows(doc) if row["name"] == ticket.name and row["ticket_doctype"] == ticket_doctype), None)
	return {"ticket": updated, "tickets": _ticket_rows(doc)}


@frappe.whitelist(allow_guest=True)
def mark_support_ticket_read(mobile_app_user: str, ticket_name: str, ticket_doctype: str = "App Support Ticket") -> dict[str, Any]:
	_require_mobile_app_token()
	doc = _get_user_doc(mobile_app_user)
	ticket = _load_ticket(doc, ticket_name, ticket_doctype, "write")

	_mark_ticket_user_messages_read(ticket.name, ticket_doctype)

	updated = next((row for row in _ticket_rows(doc) if row["name"] == ticket.name and row["ticket_doctype"] == ticket_doctype), None)
	return {"ticket": updated, "tickets": _ticket_rows(doc)}


@frappe.whitelist(allow_guest=True)
def set_support_ticket_status(mobile_app_user: str, ticket_name: str, status: str, ticket_doctype: str = "App Support Ticket", _summary=False) -> dict[str, Any]:
	_require_mobile_app_token()
	if _summary and not _authenticated():
		frappe.throw(_("Login required"), frappe.PermissionError)
	status = (status or "").strip()
	if status not in SUPPORT_TICKET_STATUSES:
		frappe.throw(_("Invalid support ticket status"))

	doc = _get_user_doc(mobile_app_user)
	ticket = _load_ticket(doc, ticket_name, ticket_doctype, "write")

	ticket.status = status
	if status in {"Open", "In Progress", "Waiting for Customer"}:
		ticket.resolved_at = None
		ticket.closed_at = None
	elif status == "Resolved":
		ticket.closed_at = None
	elif status == "Closed" and not ticket.resolved_at:
		ticket.resolved_at = now_datetime()
	ticket.save()

	if _summary:
		from mobile_app.api.desk_support import ticket_summary
		return {"ticket": ticket_summary(doc, ticket_doctype, ticket.name)}

	updated = next((row for row in _ticket_rows(doc) if row["name"] == ticket.name and row["ticket_doctype"] == ticket_doctype), None)
	return {"ticket": updated, "tickets": _ticket_rows(doc)}


@frappe.whitelist(allow_guest=True)
def delete_support_ticket(mobile_app_user: str, ticket_name: str, ticket_doctype: str = "App Support Ticket") -> dict[str, Any]:
	_require_mobile_app_token()
	doc = _get_user_doc(mobile_app_user)
	ticket = _load_ticket(doc, ticket_name, ticket_doctype, "write")
	ticket.check_permission("delete")
	message_type = _source(ticket_doctype)[0]

	for message_name in frappe.get_all(
		message_type,
		filters={"ticket": ticket.name},
		pluck="name",
	):
		frappe.delete_doc(message_type, message_name, ignore_permissions=True)

	frappe.delete_doc(ticket_doctype, ticket.name)
	return {"tickets": _ticket_rows(doc)}


@frappe.whitelist()
def get_desk_support_inbox(start=0, status=None, search=None, ticket_name=None, ticket_doctype=None, category=None):
    return _desk_support_inbox(start, status, search, ticket_name, ticket_doctype, category)


def _desk_support_inbox(start=0, status=None, search=None, ticket_name=None, ticket_doctype=None, category=None, summaries=False):
    """Bounded, permission-scoped inbox for both existing ticket integrations."""
    if not _authenticated():
        frappe.throw(_("Login required"), frappe.PermissionError)
    if ticket_name:
        _source(ticket_doctype)
    start = max(0, frappe.utils.cint(start))
    if start > 10000:
        frappe.throw(_("Narrow your search to see more tickets."))
    page_size = 20
    candidates = []
    categories = set()
    for doctype, (message_doctype, link_field) in SOURCES.items():
        if ticket_name and doctype != ticket_doctype:
            continue
        if not frappe.db.exists("DocType", doctype) or not frappe.has_permission(doctype, "read"):
            continue
        fields = ["name", "user_id", "modified"]
        if frappe.get_meta(doctype).has_field(link_field):
            fields.append(link_field)
        filters = {"status": status} if status and status != "__all" else {}
        if ticket_name:
            filters["name"] = ticket_name
        or_filters = None
        if search and not ticket_name:
            value = "%" + str(search).strip()[:100] + "%"
            or_filters = {field: ["like", value] for field in ("name", "subject", "user_id", "user_name")}
        # Collect options across matching tickets, before category filtering or paging.
        for option in frappe.get_list(doctype, fields=["category"], filters=filters,
                or_filters=or_filters, group_by="category", order_by="category asc", limit_page_length=0):
            if option.category:
                categories.add(option.category)
        if category and category != "__all":
            filters["category"] = category
        for row in frappe.get_list(doctype, fields=fields, filters=filters, or_filters=or_filters,
                order_by="modified desc, name desc", limit_page_length=start + page_size + 1):
            row.ticket_doctype = doctype
            row.linked_user = row.get(link_field)
            candidates.append(row)
    candidates.sort(key=lambda row: (str(row.modified), row.ticket_doctype, row.name), reverse=True)
    page = candidates[start:start + page_size]
    groups = {}
    for row in page:
        # A ticket permission does not grant access to the linked patient profile.
        name = row.linked_user or frappe.db.get_value("Mobile App User", row.user_id, "name")
        if not name:
            name = frappe.db.get_value("Mobile App User", {"external_id": row.user_id}, "name")
        if not name:
            continue
        key = (name, row.ticket_doctype)
        groups.setdefault(key, []).append(row.name)
    tickets = []
    for (name, doctype), names in groups.items():
        try:
            user = _get_user_doc(name)
        except frappe.PermissionError:
            continue
        allowed = [name for name in names if _ticket_belongs_to_user(frappe.get_doc(doctype, name), user, doctype)]
        if summaries:
            from mobile_app.api.desk_support import summary_rows
            rows = summary_rows(user, doctype, allowed)
        else:
            rows = _source_rows(user, doctype, allowed)
        for ticket in rows:
            ticket["mobile_app_user"] = user.name
            ticket["user_name"] = user.full_name or user.name
            ticket["can_mark_read"] = bool(frappe.has_permission(doctype, "write", doc=frappe.get_doc(doctype, ticket["name"])) and frappe.has_permission(_source(doctype)[0], "write"))
            tickets.append(ticket)
    order = {(row.ticket_doctype, row.name): index for index, row in enumerate(page)}
    tickets.sort(key=lambda ticket: order[(ticket["ticket_doctype"], ticket["name"])])
    return {"tickets": tickets, "has_more": len(candidates) > start + page_size,
            "start": start, "categories": sorted(categories)}


@frappe.whitelist()
def get_support_ticket_message_counts(ticket_names):
    """Unread customer-message counts for visible, permitted Support Ticket rows."""
    names = frappe.parse_json(ticket_names) if isinstance(ticket_names, str) else ticket_names
    if not isinstance(names, list) or len(names) > 200 or any(not isinstance(n, str) for n in names):
        frappe.throw(_("Choose up to 200 tickets."))
    if not _authenticated():
        frappe.throw(_("Login required"), frappe.PermissionError)
    frappe.has_permission("Support Ticket", "read", throw=True)
    frappe.has_permission("Support Ticket Message", "read", throw=True)
    if not names:
        return {}
    allowed = frappe.get_list("Support Ticket", filters={"name": ["in", names]},
                              pluck="name", limit_page_length=len(names))
    if not allowed:
        return {}
    counts = {name: 0 for name in allowed}
    for row in frappe.get_list("Support Ticket Message",
            filters={"ticket": ["in", allowed], "sender_type": "User", "is_read": 0},
            fields=["ticket", "count(name) as unread_count"], group_by="ticket",
            limit_page_length=len(allowed)):
        counts[row.ticket] = row.unread_count
    return counts
