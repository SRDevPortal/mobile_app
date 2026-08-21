# Copyright (c) 2026, SRIAAS and contributors

from frappe.model.document import Document
from frappe.utils import convert_utc_to_system_timezone, get_datetime, now_datetime


class AppSupportTicketMessage(Document):
	def validate(self):
		self._normalize_user_timestamp()

	def _normalize_user_timestamp(self):
		if self.sender_type == "Agent" or not self.timestamp:
			return

		try:
			raw_ts = get_datetime(self.timestamp)
			now_ts = now_datetime().replace(tzinfo=None)
			system_ts = convert_utc_to_system_timezone(raw_ts).replace(tzinfo=None)
		except Exception:
			return

		raw_distance = abs((now_ts - raw_ts).total_seconds())
		system_distance = abs((now_ts - system_ts).total_seconds())
		offset = abs((system_ts - raw_ts).total_seconds())
		if offset <= 14 * 60 * 60 and system_distance < raw_distance:
			self.timestamp = system_ts
