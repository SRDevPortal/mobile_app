# Copyright (c) 2026, SRIAAS and contributors

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class SupportTicketMessage(Document):
	def validate(self):
		if not self.timestamp:
			self.timestamp = now_datetime()
		if self.is_read and not self.read_at:
			self.read_at = now_datetime()
