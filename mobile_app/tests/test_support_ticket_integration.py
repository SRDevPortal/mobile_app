"""Transactional regression checks for the compatible international support inbox."""

from unittest import TestCase
from unittest.mock import patch

import frappe
from mobile_app.api import support_ticket as api


class TestSupportTicketIntegration(TestCase):
    def setUp(self):
        self.previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.session = patch.object(api, "_is_desk_session", return_value=True)
        self.session.start()
        self.user = frappe.get_doc({"doctype": "Mobile App User",
            "external_id": "support-merge-test-" + frappe.generate_hash(length=12),
            "full_name": "Support Merge Test"}).insert(ignore_permissions=True)
        self.ticket = frappe.get_doc({"doctype": "App Support Ticket",
            "user_id": self.user.external_id, "subject": "Synthetic integration check",
            "priority": "medium", "category": "appointment", "status": "Open"}).insert()

    def tearDown(self):
        frappe.db.rollback()
        self.session.stop()
        frappe.set_user(self.previous_user)

    def test_link_list_reply_and_status(self):
        self.assertEqual(self.ticket.mobile_app_user, self.user.name)
        self.assertEqual(self.ticket.priority, "Normal")
        self.assertEqual(self.ticket.category, "Appointment")
        message = frappe.get_doc({"doctype": "App Support Ticket Message",
            "ticket": self.ticket.name, "sender_type": "User", "sender_name": "Test",
            "message": "Synthetic question", "timestamp": frappe.utils.now_datetime()}).insert()
        result = api.get_support_tickets(self.user.name)
        self.assertEqual([row["name"] for row in result["tickets"]], [self.ticket.name])
        self.assertEqual(result["tickets"][0]["unread_count"], 1)
        result = api.send_support_reply(self.user.name, self.ticket.name, "Synthetic reply")
        self.assertEqual(result["ticket"]["unread_count"], 0)
        self.assertEqual(frappe.db.get_value(message.doctype, message.name, "is_read"), 1)
        for status in ("In Progress", "Resolved", "Closed"):
            result = api.set_support_ticket_status(self.user.name, self.ticket.name, status)
            self.assertEqual(result["ticket"]["status"], status)
        self.ticket.reload()
        self.assertTrue(self.ticket.resolved_at)
        self.assertTrue(self.ticket.closed_at)

    def test_foreign_ticket_and_invalid_status_rejected(self):
        other = frappe.get_doc({"doctype": "Mobile App User",
            "external_id": "support-other-" + frappe.generate_hash(length=12)}).insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            api.send_support_reply(other.name, self.ticket.name, "must not be written")
        with self.assertRaises(frappe.ValidationError):
            api.set_support_ticket_status(self.user.name, self.ticket.name, "Invalid")
        self.assertEqual(frappe.db.get_value(self.ticket.doctype, self.ticket.name, "status"), "Open")
        self.assertFalse(frappe.db.exists("App Support Ticket Message", {"ticket": self.ticket.name}))

    def test_guest_requires_integration_token(self):
        frappe.set_user("Guest")
        with patch.object(api, "_is_desk_session", return_value=False), patch.object(
            api, "require_app_token", side_effect=frappe.AuthenticationError
        ) as require_token:
            with self.assertRaises(frappe.AuthenticationError):
                api.get_support_tickets(self.user.name)
            require_token.assert_called_once()
