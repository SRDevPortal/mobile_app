"""Checkout guards without a site; integration is separately exercised on local fixtures."""
import unittest
import inspect
from contextlib import ExitStack
from unittest.mock import Mock, patch
import frappe
from mobile_app.api import opd_queue as api


class TestCheckout(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(frappe, "throw", side_effect=frappe.ValidationError))
        self.stack.enter_context(patch.object(api, "_checkout_access"))
        self.stack.enter_context(patch.object(api, "_version"))
        self.stack.enter_context(patch.object(api, "now_datetime", return_value="2026-10-27 18:00:00"))
        self.stack.enter_context(patch.object(frappe, "session", frappe._dict(user="reception"), create=True))
        self.visit=frappe._dict(name="visit", status="Completed", stages=[frappe._dict(state="Completed")], source_doctype=None)
        self.stack.enter_context(patch.object(api,"_get",return_value=self.visit))
        self.doc=Mock(docstatus=0, modified="version-1")
        self.stack.enter_context(patch.object(api,"_checkout_encounter",return_value=self.doc))
        self.stack.enter_context(patch.object(api,"_checkout_documents",return_value=[{"id":"prescription"}]))
        self.save=self.stack.enter_context(patch.object(api,"_save"))

    def run_checkout(self, **overrides):
        args=dict(clinic="clinic",request_id="request",visit="visit",expected_version=1,
                  encounter_modified="version-1",checked=["prescription"],complete=1)
        args.update(overrides)
        return inspect.unwrap(api.save_checkout)(**args)

    def test_requires_every_stage_completed(self):
        self.visit.stages.append(frappe._dict(state="Waiting"))
        with self.assertRaises(frappe.ValidationError):self.run_checkout()
        self.doc.submit.assert_not_called();self.save.assert_not_called()

    def test_rejects_stale_doctor_update(self):
        with self.assertRaises(frappe.ValidationError):self.run_checkout(encounter_modified="old")
        self.doc.submit.assert_not_called()

    def test_rejects_unknown_print_sections(self):
        for checked in (["unknown"],[{}]):
            with self.subTest(checked=checked),self.assertRaises(frappe.ValidationError):self.run_checkout(checked=checked)
        self.doc.submit.assert_not_called()

    def test_completion_does_not_require_print_or_handover_confirmation(self):
        self.assertTrue(self.run_checkout(checked=[])["completed"])
        self.doc.submit.assert_called_once()

    def test_checklist_save_does_not_submit(self):
        result=self.run_checkout(complete=0)
        self.assertFalse(result["completed"])
        self.assertEqual(self.visit.checkout_checklist,["prescription"])
        self.doc.submit.assert_not_called();self.save.assert_called_once()

    def test_completes_walkin_and_submits_encounter(self):
        self.assertTrue(self.run_checkout()["completed"])
        self.doc.check_permission.assert_called_once_with("submit")
        self.doc.submit.assert_called_once()
        self.assertEqual(self.visit.checkout_completed_by,"reception")

    def test_submit_failure_never_marks_checkout_complete(self):
        self.doc.submit.side_effect=frappe.ValidationError
        with self.assertRaises(frappe.ValidationError):self.run_checkout()
        self.assertFalse(self.visit.get("checkout_completed_at"));self.save.assert_not_called()

    def test_already_submitted_encounter_is_not_submitted_again(self):
        self.doc.docstatus=1
        self.assertTrue(self.run_checkout()["completed"])
        self.doc.submit.assert_not_called()

    def test_permission_failure_prevents_submission(self):
        self.doc.check_permission.side_effect=frappe.PermissionError
        with self.assertRaises(frappe.PermissionError):self.run_checkout()
        self.doc.submit.assert_not_called();self.save.assert_not_called()

    def test_completed_checkout_cannot_run_again(self):
        self.visit.checkout_completed_at="done"
        with self.assertRaises(frappe.ValidationError):self.run_checkout()
        self.doc.submit.assert_not_called()
