"""Create Support Ticket API user and print API key/secret for Render."""

from __future__ import annotations

import frappe
from frappe.core.doctype.user.user import generate_keys


ROLE = "Support Ticket API"
DEFAULT_EMAIL = "support.ticket.api@example.com"


def ensure(email: str | None = None) -> dict:
	email = (email or DEFAULT_EMAIL).strip()
	if not frappe.db.exists("Role", ROLE):
		frappe.throw(f"Role {ROLE} missing — run bench migrate first.")

	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Support",
				"last_name": "Ticket API",
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		)
		user.insert(ignore_permissions=True)
	else:
		user = frappe.get_doc("User", email)

	if ROLE not in [r.role for r in user.roles]:
		user.append("roles", {"role": ROLE})
		user.save(ignore_permissions=True)

	keys = generate_keys(user.name)
	frappe.db.commit()

	return {
		"user": email,
		"api_key": keys["api_key"],
		"api_secret": keys["api_secret"],
		"render_env": {
			"ERP_BASE_URL": "(your public https URL — not site1.local)",
			"ERP_API_KEY": keys["api_key"],
			"ERP_API_SECRET": keys["api_secret"],
		},
	}
