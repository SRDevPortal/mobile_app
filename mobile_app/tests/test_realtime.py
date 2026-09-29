"""Real transactions must notify once on commit, and never on rollback."""
from unittest import TestCase
from unittest.mock import patch

import frappe

from mobile_app.realtime import notify_change


class TestRealtime(TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_commit_coalesces_changes_without_record_data(self):
        doc = frappe._dict(doctype="Mobile App User", name="private-id", full_name="Private name")
        with patch("frappe.realtime.emit_via_redis") as emit:
            notify_change(doc)
            notify_change(doc)
            emit.assert_not_called()
            frappe.db.commit()
            emit.assert_called_once_with("mobile_app_data_changed", {"doctype": "Mobile App User"}, "all")

    def test_rollback_does_not_notify(self):
        with patch("frappe.realtime.emit_via_redis") as emit:
            notify_change(frappe._dict(doctype="Mobile App Appointment"))
            frappe.db.rollback()
            frappe.db.commit()
            emit.assert_not_called()

    def test_insert_edit_delete_hooks_queue_invalidation(self):
        old_user = frappe.session.user
        frappe.set_user("Administrator")
        try:
            doc = frappe.get_doc({"doctype": "Mobile App User",
                "external_id": "event-test-" + frappe.generate_hash(length=12)}).insert()
            self.assert_change_queued()
            frappe.realtime.clear_realtime_log()
            doc.full_name = "Synthetic event test"
            doc.save()
            self.assert_change_queued()
            frappe.realtime.clear_realtime_log()
            frappe.delete_doc(doc.doctype, doc.name, force=True)
            self.assert_change_queued()
        finally:
            frappe.db.rollback()
            frappe.set_user(old_user)

    def assert_change_queued(self):
        self.assertIn(["mobile_app_data_changed", {"doctype": "Mobile App User"}, "all"],
            frappe.local._realtime_log)
