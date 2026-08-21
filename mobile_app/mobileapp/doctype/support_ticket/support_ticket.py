# Copyright (c) 2026, SRIAAS and contributors

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import get_fullname, now_datetime


class SupportTicket(Document):
	def before_insert(self):
		if not self.ticket_number:
			self.ticket_number = self.name

	def validate(self):
		if not self.ticket_number:
			self.ticket_number = self.name
		if self.assigned_to and not self.assigned_to_name:
			self.assigned_to_name = get_fullname(self.assigned_to) or self.assigned_to
		if self.status == "Resolved" and not self.resolved_at:
			self.resolved_at = now_datetime()
		if self.status == "Closed" and not self.closed_at:
			self.closed_at = now_datetime()
