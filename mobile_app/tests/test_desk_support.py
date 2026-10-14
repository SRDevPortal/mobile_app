"""Read-only-to-production regressions; every fixture is rolled back locally."""
from unittest.mock import patch
import frappe
from mobile_app.api import desk_support as desk, support_ticket as legacy
from mobile_app.tests.test_support_ticket_integration import TestSiyaSupportInbox


class TestDeskSupport(TestSiyaSupportInbox):
    def make_messages(self, count, source="Support Ticket"):
        dt = legacy.SOURCES[source][0]
        ticket = self.siya if source == "Support Ticket" else self.ticket
        messages = []
        for n in range(count):
            messages.append(frappe.get_doc({"doctype": dt, "ticket": ticket.name, "sender_type": "User",
                "message": "page message " + str(n), "timestamp": frappe.utils.now_datetime()}).insert())
        return messages

    def test_summaries_do_not_load_history_and_legacy_keeps_it(self):
        self.make_messages(3)
        with patch.object(legacy, "_messages_by_ticket", side_effect=AssertionError("Full history fetched")):
            result = desk.inbox(ticket_name=self.siya.name, ticket_doctype="Support Ticket")
            self.assertEqual(len(result["tickets"]), 1)
            self.assertNotIn("messages", result["tickets"][0])
            self.assertEqual(result["tickets"][0]["unread_count"], 3)
            self.assertEqual(result["tickets"][0]["preview"], "page message 2")
        result = legacy.get_support_tickets(self.user.name)
        self.assertEqual(len(next(t for t in result["tickets"] if t["name"] == self.siya.name)["messages"]), 3)

    def test_paging_concurrent_arrivals_and_read_watermark(self):
        rows = self.make_messages(57)
        args = (self.user.name, self.siya.name, "Support Ticket")
        first = desk.messages(*args)
        self.assertEqual(len(first["messages"]), 50)
        self.assertTrue(first["has_more"])
        newer = self.make_messages(1)[0]
        older = desk.messages(*args, before=first["oldest"])
        self.assertEqual(len(older["messages"]), 7)
        self.assertFalse(older["has_more"])
        self.assertEqual(len({m["name"] for m in older["messages"] + first["messages"]}), 57)
        delta = desk.messages(*args, after=first["newest"])
        self.assertEqual([m["name"] for m in delta["messages"]], [newer.name])
        desk.read(*args, through=first["newest"])
        self.assertEqual(frappe.db.get_value(newer.doctype, newer.name, "is_read"), 0)
        self.assertEqual(desk.inbox(ticket_name=self.siya.name, ticket_doctype="Support Ticket")["tickets"][0]["unread_count"], 1)
        desk.read(*args, through=newer.name)
        self.assertEqual(frappe.db.get_value(newer.doctype, newer.name, "is_read"), 1)

    def test_equal_creation_cursor_and_large_incremental_burst(self):
        rows = self.make_messages(56, "App Support Ticket")
        stamp = frappe.utils.now_datetime()
        for row in rows:
            frappe.db.set_value(row.doctype, row.name, "creation", stamp, update_modified=False)
        args = (self.user.name, self.ticket.name, "App Support Ticket")
        ordered = sorted(row.name for row in rows)
        newer = desk.messages(*args, after=ordered[0])
        self.assertEqual([m["name"] for m in newer["messages"]], ordered[1:51])
        self.assertTrue(newer["has_more"])
        tail = desk.messages(*args, after=newer["newest"])
        self.assertEqual([m["name"] for m in tail["messages"]], ordered[51:])
        older = desk.messages(*args, before=ordered[10])
        self.assertEqual([m["name"] for m in older["messages"]], ordered[:10])

    def test_cursor_and_profile_permissions(self):
        a = self.make_messages(1)[0]
        self.make_messages(1, "App Support Ticket")
        with self.assertRaises(frappe.PermissionError):
            desk.messages(self.user.name, self.ticket.name, "App Support Ticket", before=a.name)
        with patch.object(legacy, "_get_user_doc", side_effect=frappe.PermissionError):
            with self.assertRaises(frappe.PermissionError):
                desk.messages(self.user.name, self.siya.name, "Support Ticket")
        with patch.object(legacy, "_load_ticket", side_effect=frappe.PermissionError):
            with self.assertRaises(frappe.PermissionError):
                desk.reply(self.user.name, self.siya.name, "Support Ticket", "denied")
        frappe.set_user("Guest")
        for func, args in [(desk.inbox, ()), (desk.messages, (self.user.name, self.siya.name, "Support Ticket")),
                (desk.reply, (self.user.name, self.siya.name, "Support Ticket", "denied"))]:
            with self.assertRaises(frappe.PermissionError):
                func(*args)

    def test_compact_mutations_keep_legacy_response(self):
        self.make_messages(2)
        args = (self.user.name, self.siya.name, "Support Ticket")
        with patch.object(legacy, "_ticket_rows", side_effect=AssertionError("All histories fetched")):
            reply = desk.reply(*args, message="desk reply")
            self.assertNotIn("messages", reply["ticket"])
            self.assertEqual(reply["ticket"]["preview"], "desk reply")
            status = desk.status(*args, status="Resolved")
            self.assertEqual(status["ticket"]["status"], "Resolved")
        full = legacy.set_support_ticket_status(self.user.name, self.siya.name, "Open", "Support Ticket")
        self.assertIn("tickets", full)
        self.assertIn("messages", full["ticket"])
