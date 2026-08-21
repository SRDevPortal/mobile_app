"""Copy Support Ticket rows from Mobile App User engagement_items into Support Ticket DocTypes."""

from __future__ import annotations

import json

import frappe
from frappe.utils import get_datetime, now_datetime


def _parse_payload(raw):
	if raw is None or raw == "":
		return {}
	if isinstance(raw, dict):
		return raw
	if isinstance(raw, str):
		try:
			parsed = json.loads(raw)
			return parsed if isinstance(parsed, dict) else {}
		except json.JSONDecodeError:
			return {}
	return {}


def _sender_type(from_value: str) -> str:
	v = (from_value or "").strip().lower()
	return "Agent" if v in ("agent", "staff", "support") else "User"


def execute():
	if not frappe.db.exists("DocType", "Support Ticket"):
		return

	users = frappe.get_all("Mobile App User", pluck="name")
	created_tickets = 0
	created_messages = 0

	for user_name in users:
		doc = frappe.get_doc("Mobile App User", user_name)
		user_id = (doc.external_id or doc.name or "").strip()
		if not user_id:
			continue

		for row in doc.get("engagement_items") or []:
			if (row.record_type or "").strip() != "Support Ticket":
				continue

			engagement_key = row.name or f"{user_name}:{row.idx}"
			if frappe.db.sql(
				"""
				select name from `tabSupport Ticket`
				where metadata like %s limit 1
				""",
				(f"%{engagement_key}%",),
			):
				continue

			payload = _parse_payload(row.payload_json)
			messages = payload.get("messages") or []
			if messages and not isinstance(messages, list):
				messages = []

			ticket = frappe.get_doc(
				{
					"doctype": "Support Ticket",
					"naming_series": "ST-.YYYY.-",
					"user_id": user_id,
					"user_name": doc.full_name or doc.name,
					"user_email": doc.email,
					"user_phone": doc.phone,
					"customer_name": doc.full_name,
					"email": doc.email,
					"phone": doc.phone,
					"subject": row.subject or "Support Ticket",
					"description": payload.get("description") or "",
					"status": row.status or "Open",
					"priority": payload.get("priority") or "Medium",
					"category": payload.get("label") or payload.get("category") or "",
					"metadata": json.dumps(
						{
							"source": "engagement_items",
							"engagement_row": engagement_key,
							"record_external_id": row.record_external_id,
						}
					),
				}
			)
			ticket.insert(ignore_permissions=True)
			created_tickets += 1

			for msg in messages:
				if not isinstance(msg, dict):
					continue
				body = (msg.get("body") or msg.get("text") or "").strip()
				if not body:
					continue
				ts = msg.get("timestamp") or row.recorded_at
				try:
					ts = get_datetime(ts) if ts else now_datetime()
				except Exception:
					ts = now_datetime()

				frappe.get_doc(
					{
						"doctype": "Support Ticket Message",
						"ticket": ticket.name,
						"sender_type": _sender_type(msg.get("from")),
						"sender_id": msg.get("sender_id") or "",
						"sender_name": msg.get("author") or msg.get("sender_name") or "",
						"message": body,
						"timestamp": ts,
						"is_read": 1 if msg.get("seen") else 0,
					}
				).insert(ignore_permissions=True)
				created_messages += 1

	frappe.db.commit()
	frappe.clear_cache()
	print(
		f"migrate_engagement_support_tickets: tickets={created_tickets}, messages={created_messages}"
	)
