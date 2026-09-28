"""Mobile ISO login timestamps must survive real insert/update and both sync APIs."""

from datetime import datetime
from unittest import TestCase
from unittest.mock import patch

import frappe
from mobile_app.api import v1


class TestMobileUserTimestamps(TestCase):
    def setUp(self):
        self.previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.timezone = patch("frappe.utils.data.get_system_timezone", return_value="Asia/Kolkata")
        self.timezone.start()
        self.identity = "timestamp-test-" + frappe.generate_hash(length=12)

    def tearDown(self):
        frappe.db.rollback()
        self.timezone.stop()
        frappe.set_user(self.previous_user)

    def new_user(self, timestamp):
        return frappe.get_doc({"doctype": "Mobile App User", "external_id": self.identity,
            "last_login_at": timestamp}).insert(ignore_permissions=True)

    def test_iso_utc_insert_and_offset_update(self):
        doc = self.new_user("2026-09-28T10:53:37.040Z")
        self.assertEqual(doc.reload().last_login_at, datetime(2026, 9, 28, 16, 23, 37, 40000))
        doc.last_login_at = "2026-09-28T10:53:37.040+02:00"
        doc.save(ignore_permissions=True)
        self.assertEqual(doc.reload().last_login_at, datetime(2026, 9, 28, 14, 23, 37, 40000))
        doc.save(ignore_permissions=True)
        self.assertEqual(doc.reload().last_login_at, datetime(2026, 9, 28, 14, 23, 37, 40000))

    def test_local_time_and_blank_preserved(self):
        doc = self.new_user("2026-09-28 10:53:37.040")
        self.assertEqual(doc.reload().last_login_at, datetime(2026, 9, 28, 10, 53, 37, 40000))
        doc.last_login_at = " "
        doc.save(ignore_permissions=True)
        self.assertIsNone(doc.reload().last_login_at)

    def test_invalid_timestamp_is_validation_error(self):
        for invalid in ("not-a-date", "0000-00-00", 12345, "2026-99-99T10:00:00Z"):
            with self.assertRaises(frappe.ValidationError):
                self.new_user(invalid)
        self.assertFalse(frappe.db.exists("Mobile App User", {"external_id": self.identity}))

    def test_sync_and_full_sync_normalize_login_timestamp(self):
        payload = {"external_id": self.identity, "last_login_at": "2026-09-28T10:53:37.040Z"}
        # These endpoints normally commit inside their operation lock; keep fixtures transactional.
        with patch.object(v1, "require_app_token"), patch.object(v1, "_parse_body", return_value=payload), patch.object(frappe.db, "commit"):
            v1.users_sync()
            name = frappe.db.get_value("Mobile App User", {"external_id": self.identity}, "name")
            self.assertEqual(frappe.db.get_value("Mobile App User", name, "last_login_at"), datetime(2026, 9, 28, 16, 23, 37, 40000))
            payload["last_login_at"] = "2026-09-28T10:53:06.955Z"
            v1.users_full_sync()
            self.assertEqual(frappe.db.get_value("Mobile App User", name, "last_login_at"), datetime(2026, 9, 28, 16, 23, 6, 955000))
