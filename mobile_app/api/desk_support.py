"""Bounded Desk-only support APIs; mobile responses remain unchanged."""
import frappe
from frappe import _
from mobile_app.api import support_ticket as legacy

PAGE_SIZE = 50
MESSAGE_FIELDS = ["name", "creation", "timestamp", "sender_type", "sender_id", "sender_name", "message", "is_read"]


def _login():
    if not legacy._authenticated():
        frappe.throw(_("Login required"), frappe.PermissionError)


def _context(user, name, source, permission="read"):
    _login()
    doc = legacy._get_user_doc(user)
    ticket = legacy._load_ticket(doc, name, source, permission)
    message_type = legacy._source(source)[0]
    frappe.has_permission(message_type, "read", throw=True)
    return doc, ticket, message_type


def summary_rows(user, source, names):
    """At most one message body per visible ticket, never its full history."""
    message_type = legacy._source(source)[0]
    unread = {
        row.ticket: int(row.total)
        for row in frappe.get_list(message_type,
            filters={"ticket": ["in", names], "sender_type": "User", "is_read": 0},
            fields=["ticket", "count(name) as total"], group_by="ticket", limit_page_length=0)
    } if names else {}
    result = []
    for name in names:
        ticket = frappe.get_doc(source, name)
        ticket.check_permission("read")
        latest = frappe.get_list(message_type, filters={"ticket": name},
            fields=MESSAGE_FIELDS, order_by="creation desc, name desc", limit_page_length=1)
        message = latest[0] if latest else None
        description = legacy._plain(ticket.description) if source == "Support Ticket" else ticket.description
        body = (legacy._plain(message.message) if source == "Support Ticket" else message.message) if message else ""
        result.append({
            "name": name, "ticket_doctype": source, "external_id": ticket.ticket_number,
            "subject": ticket.subject, "description": description or "", "status": ticket.status,
            "label": ticket.category or "", "preview": str(body or description or ticket.subject or "")[:200],
            "recorded_at": (legacy._display_timestamp(message) if source == "App Support Ticket" else message.timestamp) if message else ticket.modified,
            "profile_image_url": legacy._profile_image_url(user), "unread_count": unread.get(name, 0),
            "mobile_app_user": user.name, "user_name": user.full_name or user.name,
            "can_mark_read": bool(frappe.has_permission(source, "write", doc=ticket) and frappe.has_permission(message_type, "write")),
        })
    return result


def ticket_summary(user, source, name):
    return summary_rows(user, source, [name])[0]


@frappe.whitelist()
def inbox(start=0, status=None, search=None, ticket_name=None, ticket_doctype=None, category=None):
    _login()
    return legacy._desk_support_inbox(start, status, search, ticket_name, ticket_doctype, category, summaries=True)


def _cursor(message_type, ticket_name, name):
    rows = frappe.get_list(message_type, filters={"name": name, "ticket": ticket_name},
        fields=["name", "creation"], limit_page_length=1)
    if not rows:
        frappe.throw(_("Message cursor does not belong to this conversation"), frappe.PermissionError)
    return rows[0]


def _page(message_type, ticket_name, before=None, after=None):
    if before and after:
        frappe.throw(_("Use only one message cursor"))
    direction = "asc" if after else "desc"
    order = f"creation {direction}, name {direction}"
    cursor_name = before or after
    if not cursor_name:
        return frappe.get_list(message_type, filters={"ticket": ticket_name},
            fields=MESSAGE_FIELDS, order_by=order, limit_page_length=PAGE_SIZE + 1)
    cursor = _cursor(message_type, ticket_name, cursor_name)
    op = ">" if after else "<"
    # Two permission-scoped, bounded queries implement a stable (creation, name) cursor.
    same = frappe.get_list(message_type,
        filters={"ticket": ticket_name, "creation": cursor.creation, "name": [op, cursor.name]},
        fields=MESSAGE_FIELDS, order_by=order, limit_page_length=PAGE_SIZE + 1)
    other = frappe.get_list(message_type, filters={"ticket": ticket_name, "creation": [op, cursor.creation]},
        fields=MESSAGE_FIELDS, order_by=order, limit_page_length=PAGE_SIZE + 1)
    return sorted(same + other, key=lambda r: (r.creation, r.name), reverse=not bool(after))[:PAGE_SIZE + 1]


@frappe.whitelist()
def messages(mobile_app_user, ticket_name, ticket_doctype, before=None, after=None):
    user, ticket, message_type = _context(mobile_app_user, ticket_name, ticket_doctype)
    rows = _page(message_type, ticket.name, before, after)
    has_more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    if not after:
        rows.reverse()
    return {
        "messages": [{
            "name": r.name, "creation": str(r.creation), "from": "agent" if r.sender_type == "Agent" else "customer",
            "sender_id": r.sender_id, "sender_name": r.sender_name,
            "body": legacy._plain(r.message) if ticket_doctype == "Support Ticket" else r.message,
            "timestamp": legacy._display_timestamp(r) if ticket_doctype == "App Support Ticket" else r.timestamp,
            "is_read": int(r.is_read or 0),
        } for r in rows],
        "has_more": has_more,
        "oldest": rows[0].name if rows else None,
        "newest": rows[-1].name if rows else None,
        "ticket": ticket_summary(user, ticket_doctype, ticket.name),
    }


@frappe.whitelist(methods=["POST"])
def reply(mobile_app_user, ticket_name, ticket_doctype, message):
    _login()
    return legacy.send_support_reply(mobile_app_user, ticket_name, message, ticket_doctype, _summary=True)


@frappe.whitelist(methods=["POST"])
def status(mobile_app_user, ticket_name, ticket_doctype, status):
    _login()
    return legacy.set_support_ticket_status(mobile_app_user, ticket_name, status, ticket_doctype, _summary=True)


@frappe.whitelist(methods=["POST"])
def read(mobile_app_user, ticket_name, ticket_doctype, through):
    """Read only through the last fetched message; concurrent arrivals stay unread."""
    user, ticket, message_type = _context(mobile_app_user, ticket_name, ticket_doctype, "write")
    frappe.has_permission(message_type, "write", throw=True)
    cursor = _cursor(message_type, ticket.name, through)
    # Keep the existing global message-write policy, but never mark a future message read.
    table = frappe.qb.DocType(message_type)
    cutoff = (table.creation < cursor.creation) | ((table.creation == cursor.creation) & (table.name <= cursor.name))
    (frappe.qb.update(table).set(table.is_read, 1).set(table.read_at, frappe.utils.now_datetime())
        .where((table.ticket == ticket.name) & (table.sender_type == "User") & (table.is_read == 0) & cutoff)).run()
    frappe.publish_realtime("mobile_app_data_changed", {"doctype": message_type}, room="all", after_commit=True)
    return {"ticket": ticket_summary(user, ticket_doctype, ticket.name)}
