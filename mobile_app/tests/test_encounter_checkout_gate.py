import unittest
from unittest.mock import patch, Mock
import frappe
from mobile_app import encounter_checkout_gate as gate


class TestManualSaveGate(unittest.TestCase):
    def test_save_must_follow_route_completion(self):
        visit = frappe._dict(completed_at="2026-10-28 12:00:00")
        doc = frappe._dict(name="PE-1", modified="2026-10-28 11:59:00")
        self.assertFalse(gate.is_ready(visit, doc))

    def test_current_version_requires_manual_marker(self):
        visit = frappe._dict(completed_at="2026-10-28 12:00:00")
        doc = frappe._dict(name="PE-1", modified="2026-10-28 12:01:00")
        with patch.object(frappe, "db", Mock(), create=True) as db:
            db.exists.return_value = False
            self.assertFalse(gate.is_ready(visit, doc))
            db.exists.return_value = True
            self.assertTrue(gate.is_ready(visit, doc))
            self.assertEqual(db.exists.call_args.args[1]["content"], gate.marker(doc))

    def test_autosave_never_marks_ready(self):
        doc = frappe._dict(docstatus=0, __opd_autosave=1)
        with patch.object(frappe, "form_dict", {"cmd":"frappe.desk.form.save.savedocs","action":"Save"}, create=True), patch.object(gate, "completed_visits") as visits:
            gate.on_update(doc)
            visits.assert_not_called()

    def test_manual_save_records_marker(self):
        doc = Mock(docstatus=0, modified="2026-10-28 12:01:00")
        doc.get.return_value = None
        with patch.object(frappe, "form_dict", {"cmd":"frappe.desk.form.save.savedocs","action":"Save"}, create=True), patch.object(gate, "completed_visits", return_value=iter([True])):
            gate.on_update(doc)
        doc.add_comment.assert_called_once_with("Info", gate.marker(doc))

    def test_autosave_after_completion_does_not_approve_version(self):
        doc = Mock(docstatus=0, modified="2026-10-28 12:02:00")
        doc.get.return_value = 1
        with patch.object(frappe, "form_dict", {"cmd":"frappe.desk.form.save.savedocs","action":"Save"}, create=True):
            gate.on_update(doc)
        doc.add_comment.assert_not_called()
