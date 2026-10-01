"""Setup regression tests; database access is mocked and no site is required."""

import unittest
from contextlib import ExitStack
from unittest.mock import Mock, call, patch

import frappe

from mobile_app import calendar_setup
from mobile_app.api import appointment_calendar as api


class TestCalendarSetup(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.db = stack.enter_context(patch.object(frappe, "db", Mock(), create=True))
        self.db.exists.return_value = True
        self.get_doc = stack.enter_context(patch.object(frappe, "get_doc"))
        self.logger = stack.enter_context(patch.object(frappe, "logger"))
        stack.enter_context(patch.object(api, "_", side_effect=lambda text: text))
        self.throw = stack.enter_context(
            patch.object(frappe, "throw", side_effect=frappe.ValidationError)
        )
        self.doc = frappe._dict(
            doctype="Patient Encounter", name="old-encounter",
            encounter_reference="clinic-appointment", docstatus=2,
        )
        self.clinic = Mock(encounter_reference="amended-encounter", appointment_status="Confirmed")
        self.get_doc.return_value = self.clinic

    def test_setup_skips_mismatched_link_without_writes(self):
        api._sync_clinic_status(self.doc, "Cancelled", skip_invalid_links=True)
        self.db.set_value.assert_not_called()
        self.clinic.add_comment.assert_not_called()
        self.throw.assert_not_called()
        self.logger.return_value.warning.assert_called_once()

    def test_setup_skips_missing_appointment_without_loading_it(self):
        self.db.exists.side_effect = lambda doctype, name: doctype == "DocType"
        api._sync_clinic_status(self.doc, "Cancelled", skip_invalid_links=True)
        self.get_doc.assert_not_called()
        self.db.set_value.assert_not_called()
        self.logger.return_value.warning.assert_called_once()

    def test_normal_update_still_rejects_mismatched_link(self):
        with self.assertRaises(frappe.ValidationError):
            api._sync_clinic_status(self.doc, "Cancelled")
        self.db.set_value.assert_not_called()
        self.clinic.add_comment.assert_not_called()

    def test_normal_update_still_rejects_missing_appointment(self):
        self.get_doc.side_effect = frappe.DoesNotExistError
        with self.assertRaises(frappe.DoesNotExistError):
            api._sync_clinic_status(self.doc, "Cancelled")
        self.db.set_value.assert_not_called()

    def test_valid_link_syncs_once_when_setup_is_repeated(self):
        self.clinic.encounter_reference = self.doc.name
        api._sync_clinic_status(self.doc, "Cancelled", skip_invalid_links=True)
        self.db.set_value.assert_called_once_with(
            "Clinic Appointment", "clinic-appointment", "appointment_status", "Cancelled"
        )
        self.clinic.appointment_status = "Cancelled"
        api._sync_clinic_status(self.doc, "Cancelled", skip_invalid_links=True)
        self.db.set_value.assert_called_once()
        self.clinic.add_comment.assert_called_once()
        self.logger.assert_not_called()

    def test_reverse_lookup_still_syncs_valid_link(self):
        self.doc.encounter_reference = None
        self.db.get_value.return_value = "clinic-appointment"
        self.clinic.encounter_reference = self.doc.name
        api._sync_clinic_status(self.doc, "Cancelled", skip_invalid_links=True)
        self.db.set_value.assert_called_once_with(
            "Clinic Appointment", "clinic-appointment", "appointment_status", "Cancelled"
        )

    def test_unexpected_database_errors_are_not_hidden(self):
        self.get_doc.side_effect = RuntimeError("database unavailable")
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            api._sync_clinic_status(self.doc, "Cancelled", skip_invalid_links=True)

    def test_setup_continues_after_mismatched_and_missing_links(self):
        missing = frappe._dict(
            doctype="Patient Encounter", name="missing-encounter", encounter_reference="missing-clinic"
        )
        valid = frappe._dict(
            doctype="Patient Encounter", name="valid-encounter", encounter_reference="valid-clinic"
        )
        valid_clinic = Mock(encounter_reference=valid.name, appointment_status="Draft")
        docs = {
            self.doc.name: self.doc, missing.name: missing, valid.name: valid,
            "clinic-appointment": self.clinic, "valid-clinic": valid_clinic,
        }
        self.get_doc.side_effect = lambda doctype, name: docs[name]
        self.db.exists.side_effect = lambda doctype, name: name != "missing-clinic"
        with (
            patch("frappe.custom.doctype.custom_field.custom_field.create_custom_fields"),
            patch.object(frappe, "get_all", return_value=[self.doc.name, missing.name, valid.name]),
            patch.object(api, "_workflow", return_value=frappe._dict()),
            patch.object(api, "_status", return_value="Cancelled"),
            patch.object(api, "_sync_encounter_status") as sync_encounter,
        ):
            calendar_setup.setup_encounter_status()
        self.assertEqual(sync_encounter.call_args_list, [
            call(self.doc, "Cancelled"), call(missing, "Cancelled"), call(valid, "Cancelled"),
        ])
        self.db.set_value.assert_called_once_with(
            "Clinic Appointment", "valid-clinic", "appointment_status", "Cancelled"
        )
        self.clinic.add_comment.assert_not_called()
        valid_clinic.add_comment.assert_called_once()
        self.assertEqual(self.logger.return_value.warning.call_count, 2)


if __name__ == "__main__":
    unittest.main()
