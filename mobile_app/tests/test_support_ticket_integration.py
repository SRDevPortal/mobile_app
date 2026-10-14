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
        self.assertIn(self.ticket.name, [row["name"] for row in result["tickets"]])
        result["tickets"] = [row for row in result["tickets"] if row["ticket_doctype"] == "App Support Ticket"]
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


class TestSiyaSupportInbox(TestSupportTicketIntegration):
    def setUp(self):
        super().setUp()
        self.siya = frappe.get_doc({"doctype": "Support Ticket",
            "user_id": self.user.name, "subject": "Mobile support question",
            "description": "Delivery&lt;help&gt;<br>Second line", "status": "Open",
            "priority": "Medium", "category": "Orders"}).insert()

    def test_both_sources_and_two_way_chat_use_original_records(self):
        question = frappe.get_doc({"doctype": "Support Ticket Message", "ticket": self.siya.name,
            "sender_type": "User", "sender_id": self.user.name,
            "message": "<p>Where is my order?</p>", "timestamp": frappe.utils.now_datetime()}).insert()
        rows = api.get_support_tickets(self.user.name)["tickets"]
        self.assertEqual({row["ticket_doctype"] for row in rows}, {"Support Ticket", "App Support Ticket"})
        row = next(row for row in rows if row["ticket_doctype"] == "Support Ticket")
        self.assertEqual(row["name"], self.siya.name)
        self.assertEqual(row["description"], "Delivery<help>\nSecond line")
        self.assertEqual(row["messages"][0]["body"], "Where is my order?")
        response = api.send_support_reply(self.user.name, self.siya.name, "We are checking your order.", "Support Ticket")
        self.assertEqual(response["ticket"]["messages"][-1]["from"], "agent")
        replies = frappe.get_all("Support Ticket Message", filters={"ticket": self.siya.name, "sender_type": "Agent"}, fields=["name", "message", "is_read", "sender_id"])
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].message, "We are checking your order.")
        self.assertEqual(replies[0].sender_id, "Administrator")
        self.assertEqual(replies[0].is_read, 0)
        question.reload()
        self.assertEqual(question.is_read, 1)
        self.assertFalse(frappe.db.exists("App Support Ticket Message", {"ticket": self.ticket.name}))
        api.set_support_ticket_status(self.user.name, self.siya.name, "Waiting for Customer", "Support Ticket")
        self.assertEqual(frappe.db.get_value("Support Ticket", self.siya.name, "status"), "Waiting for Customer")
        api.set_support_ticket_status(self.user.name, self.siya.name, "Closed", "Support Ticket")
        with self.assertRaises(frappe.ValidationError):
            api.send_support_reply(self.user.name, self.siya.name, "Must not send", "Support Ticket")

    def test_normal_erp_auth_reads_inbox_without_separate_app_token(self):
        with patch.object(api, "_is_desk_session", return_value=False), patch.object(api, "require_app_token", side_effect=AssertionError("No shared token needed")):
            self.assertEqual(len(api.get_support_tickets(self.user.name)["tickets"]), 2)

    def test_foreign_standard_ticket_and_invalid_source_rejected(self):
        other = frappe.get_doc({"doctype": "Mobile App User", "external_id": "support-other-" + frappe.generate_hash(length=12)}).insert(ignore_permissions=True)
        for call in (
            lambda: api.send_support_reply(other.name, self.siya.name, "Must not send", "Support Ticket"),
            lambda: api.set_support_ticket_status(other.name, self.siya.name, "Closed", "Support Ticket"),
            lambda: api.delete_support_ticket(other.name, self.siya.name, "Support Ticket"),
            lambda: api.send_support_reply(self.user.name, self.siya.name, "Must not send", "Patient"),
        ):
            with self.assertRaises(frappe.ValidationError):
                call()
        conflicting = frappe._dict(user_id=self.user.name, custom_mobile_app_user=other.name)
        self.assertFalse(api._ticket_belongs_to_user(conflicting, self.user, "Support Ticket"))
        self.assertFalse(frappe.db.exists("Support Ticket Message", {"ticket": self.siya.name}))

    def test_record_permission_required_before_reply(self):
        with patch.object(api, "_get_user_doc", return_value=self.user), patch.object(api, "_require_mobile_app_token"), patch.object(type(self.siya), "check_permission", side_effect=frappe.PermissionError):
            with self.assertRaises(frappe.PermissionError):
                api.send_support_reply(self.user.name, self.siya.name, "Forbidden", "Support Ticket")
        self.assertFalse(frappe.db.exists("Support Ticket Message", {"ticket": self.siya.name}))

    def test_delete_only_selected_source(self):
        api.delete_support_ticket(self.user.name, self.siya.name, "Support Ticket")
        self.assertFalse(frappe.db.exists("Support Ticket", self.siya.name))
        self.assertTrue(frappe.db.exists("App Support Ticket", self.ticket.name))

class TestDeskSupportInbox(TestSiyaSupportInbox):
    def test_desk_inbox_includes_both_sources_and_customer_identity(self):
        rows = api.get_desk_support_inbox(search=self.user.name)["tickets"]
        self.assertEqual({(r["ticket_doctype"], r["name"]) for r in rows},
                         {("App Support Ticket", self.ticket.name), ("Support Ticket", self.siya.name)})
        self.assertTrue(all(r["mobile_app_user"] == self.user.name for r in rows))
        self.assertTrue(all(r["user_name"] == self.user.full_name for r in rows))
        api.set_support_ticket_status(self.user.name, self.ticket.name, "Closed")
        rows = api.get_desk_support_inbox(search=self.user.name, status="Closed")["tickets"]
        self.assertEqual([r["name"] for r in rows], [self.ticket.name])

    def test_desk_inbox_requires_login_and_profile_permission(self):
        with patch.object(api, "_get_user_doc", side_effect=frappe.PermissionError):
            self.assertEqual(api.get_desk_support_inbox(search=self.user.name)["tickets"], [])
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            api.get_desk_support_inbox()

    def test_customer_badge_counts_and_read_reset(self):
        for sender in ("User", "Agent"):
            frappe.get_doc({"doctype": "Support Ticket Message", "ticket": self.siya.name,
                "sender_type": sender, "sender_id": self.user.name, "message": "Badge check",
                "is_read": 0, "timestamp": frappe.utils.now_datetime()}).insert()
        self.assertEqual(api.get_support_ticket_message_counts([self.siya.name]), {self.siya.name: 1})
        api.mark_support_ticket_read(self.user.name, self.siya.name, "Support Ticket")
        self.assertEqual(api.get_support_ticket_message_counts([self.siya.name]), {self.siya.name: 0})
        frappe.get_doc({"doctype": "Support Ticket Message", "ticket": self.siya.name,
            "sender_type": "User", "sender_id": self.user.name, "message": "New customer message",
            "is_read": 0, "timestamp": frappe.utils.now_datetime()}).insert()
        self.assertEqual(api.get_support_ticket_message_counts([self.siya.name]), {self.siya.name: 1})

    def test_inbox_exact_ticket_routing_and_permissions(self):
        rows = api.get_desk_support_inbox(ticket_name=self.siya.name, ticket_doctype="Support Ticket")["tickets"]
        self.assertEqual([(r["ticket_doctype"], r["name"]) for r in rows], [("Support Ticket", self.siya.name)])
        self.assertEqual(api.get_desk_support_inbox(ticket_name=self.siya.name, ticket_doctype="App Support Ticket")["tickets"], [])
        with self.assertRaises(frappe.ValidationError):
            api.get_desk_support_inbox(ticket_name=self.siya.name, ticket_doctype="User")
        with patch.object(frappe, "get_list", return_value=[]):
            self.assertEqual(api.get_support_ticket_message_counts([self.siya.name]), {})
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            api.get_support_ticket_message_counts([self.siya.name])


    def test_new_messages_bring_old_tickets_to_first_page_without_read_receipt_reordering(self):
        # Enough newer tickets to push both original conversations off page one.
        for i in range(21):
            frappe.get_doc({"doctype": "Support Ticket", "user_id": self.user.name,
                "subject": f"Newer ticket {i}", "status": "Open",
                "priority": "Medium", "category": "General"}).insert()
        first = api.get_desk_support_inbox(search=self.user.name)
        self.assertNotIn(self.siya.name, [r["name"] for r in first["tickets"]])
        for ticket, message_type in ((self.siya, "Support Ticket Message"),
                                     (self.ticket, "App Support Ticket Message")):
            frappe.get_doc({"doctype": message_type, "ticket": ticket.name,
                "sender_type": "User", "sender_id": self.user.name,
                "message": "New activity on an old conversation",
                "timestamp": frappe.utils.now_datetime(), "is_read": 0}).insert()
            top = api.get_desk_support_inbox(search=self.user.name)["tickets"][0]
            self.assertEqual((top["ticket_doctype"], top["name"]), (ticket.doctype, ticket.name))
            modified = frappe.db.get_value(ticket.doctype, ticket.name, "modified")
            api.mark_support_ticket_read(self.user.name, ticket.name, ticket.doctype)
            self.assertEqual(frappe.db.get_value(ticket.doctype, ticket.name, "modified"), modified)
            # Replying must still work after the message hook updates the parent.
            reply = api.send_support_reply(self.user.name, ticket.name, "Received", ticket.doctype)
            self.assertEqual(reply["ticket"]["messages"][-1]["body"], "Received")

    def test_category_options_and_filter_include_tickets_beyond_first_page(self):
        for i in range(21):
            frappe.get_doc({"doctype": "Support Ticket", "user_id": self.user.name,
                "subject": f"General ticket {i}", "status": "Open",
                "priority": "Medium", "category": "General"}).insert()
        first = api.get_desk_support_inbox(search=self.user.name)
        self.assertTrue(first["has_more"])
        self.assertNotIn("Orders", [r["label"] for r in first["tickets"]])
        self.assertIn("Orders", first["categories"])
        self.assertIn("Appointment", first["categories"])
        for category, ticket in (("Orders", self.siya), ("Appointment", self.ticket)):
            filtered = api.get_desk_support_inbox(search=self.user.name, category=category)
            self.assertEqual([r["name"] for r in filtered["tickets"]], [ticket.name])
            self.assertFalse(filtered["has_more"])
        empty = api.get_desk_support_inbox(search=self.user.name, category="No matches")
        self.assertEqual(empty["tickets"], [])
        self.assertIn("Orders", empty["categories"])
