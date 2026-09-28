# Copyright (c) 2026, SRIAAS and contributors

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import get_fullname, now_datetime

_PRIORITY_ALIASES = {
	"low": "Low",
	"low priority": "Low",
	"normal": "Normal",
	"normal priority": "Normal",
	"medium": "Normal",
	"high": "High",
	"high priority": "High",
	"urgent": "High",
}

_CATEGORY_ALIASES = {
	"general": "General",
	"technical": "Technical",
	"appointment": "Appointment",
	"payment": "Payment",
	"other": "Other",
}


class AppSupportTicket(Document):
	def before_insert(self):
		self._ensure_ticket_number()
		self._link_mobile_app_user()
		self._normalize_selects()

	def validate(self):
		self._ensure_ticket_number()
		self._link_mobile_app_user()
		self._normalize_selects()
		if self.assigned_to and not self.assigned_to_name:
			self.assigned_to_name = get_fullname(self.assigned_to) or self.assigned_to
		if self.status == "Resolved" and not self.resolved_at:
			self.resolved_at = now_datetime()
		if self.status == "Closed" and not self.closed_at:
			self.closed_at = now_datetime()

	def _ensure_ticket_number(self):
		if not self.ticket_number:
			self.ticket_number = self.name

	def _link_mobile_app_user(self):
		if self.mobile_app_user or not self.user_id:
			return
		name = frappe.db.get_value("Mobile App User", {"external_id": self.user_id}, "name")
		if not name and frappe.db.exists("Mobile App User", self.user_id):
			name = self.user_id
		if name:
			self.mobile_app_user = name
			user = frappe.db.get_value(
				"Mobile App User",
				name,
				["full_name", "email", "phone"],
				as_dict=True,
			)
			if user:
				self.user_name = self.user_name or user.full_name
				self.user_email = self.user_email or user.email
				self.user_phone = self.user_phone or user.phone

	def _normalize_selects(self):
		if self.priority:
			key = str(self.priority).strip().lower()
			self.priority = _PRIORITY_ALIASES.get(key, self.priority)
		if self.category:
			key = str(self.category).strip().lower()
			self.category = _CATEGORY_ALIASES.get(key, self.category)
