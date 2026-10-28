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
        self.get_meta = stack.enter_context(patch.object(frappe, "get_meta"))
        self.get_meta.return_value.has_field.return_value = True
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

    def test_standard_healthcare_install_skips_optional_encounter_backfill(self):
        self.get_meta.return_value.has_field.return_value = False
        with (
            patch("frappe.custom.doctype.custom_field.custom_field.create_custom_fields") as fields,
            patch.object(frappe, "get_all") as get_all,
        ):
            calendar_setup.setup_encounter_status()
        fields.assert_not_called()
        get_all.assert_not_called()
        self.db.sql.assert_not_called()

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

    def test_setup_does_not_rewrite_existing_appointment_history(self):
        with (
            patch("frappe.custom.doctype.custom_field.custom_field.create_custom_fields"),
            patch.object(frappe, "get_all") as get_all,
            patch.object(api, "_sync_encounter_status") as sync,
        ):
            calendar_setup.setup_encounter_status()
        get_all.assert_not_called()
        sync.assert_not_called()
        self.db.set_value.assert_not_called()
        self.db.sql.assert_not_called()


class TestCalendarReschedule(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(api, "_", side_effect=lambda text: text))
        self.stack.enter_context(patch.object(frappe, "throw", side_effect=frappe.ValidationError))
        self.stack.enter_context(patch.object(api, "now_datetime", return_value=frappe.utils.get_datetime("2026-10-27 09:00:00")))
        self.doc = Mock(doctype="Patient Encounter", flags=frappe._dict())
        self.doc.name = "test-encounter"
        self.doc.get.side_effect = {"pe_appointment_date": "2026-10-28", "docstatus": 0}.get
        self.doc.meta.has_field.return_value = True

    def test_reschedule_updates_canonical_date_and_read_only_field(self):
        self.assertEqual(api._reschedule(self.doc, "2026-10-30", "2026-10-28"), "2026-10-28")
        self.assertEqual(str(self.doc.pe_appointment_date), "2026-10-30")
        self.doc.set.assert_called_once_with(api.RESCHEDULE_DATE_FIELD, frappe.utils.getdate("2026-10-30"))
        self.doc.save.assert_called_once_with(ignore_permissions=True)
        api.protect_reschedule_date(self.doc)
        self.assertEqual(self.doc.set.call_count, 1)

    def test_invalid_or_stale_date_does_not_save(self):
        for date, expected in [(None, "2026-10-28"), ("2026-10-26", "2026-10-28"), ("2026-10-27", "2026-10-28"),
                               ("2026-10-28", "2026-10-28"), ("2026-10-30", "2026-10-29")]:
            with self.subTest(date=date, expected=expected), self.assertRaises(frappe.ValidationError):
                api._reschedule(self.doc, date, expected)
        self.doc.save.assert_not_called()

    def test_form_api_cannot_change_reschedule_date(self):
        self.doc.is_new.return_value = False
        with patch.object(frappe, "db", Mock(), create=True) as db:
            db.get_value.return_value = "2026-10-30"
            api.protect_reschedule_date(self.doc)
        self.doc.set.assert_called_once_with(api.RESCHEDULE_DATE_FIELD, "2026-10-30")

    def test_new_encounter_cannot_supply_reschedule_date(self):
        self.doc.is_new.return_value = True
        api.protect_reschedule_date(self.doc)
        self.doc.set.assert_called_once_with(api.RESCHEDULE_DATE_FIELD, None)

    def test_reschedule_requires_responsible_staff_and_open_appointment(self):
        with patch.object(api, "_agent", return_value="agent@example.com"):
            for status in ("Pending", "Approved", "Checked In", "Completed", "Cancelled"):
                workflow = frappe._dict(workflow_status=status)
                actions = api._actions(self.doc, workflow, ("agent@example.com", {"Agent"}, False))
                self.assertEqual("reschedule" in actions, status in ("Pending", "Approved"))
                self.assertNotIn("reschedule", api._actions(self.doc, workflow, ("other", {"Agent"}, False)))
                self.assertNotIn("reschedule", api._actions(self.doc, workflow, ("reception", {"Appointment Receptionist"}, False)))

    def test_future_actions_are_reschedule_only_for_both_sources(self):
        for doctype, field in [("Patient Encounter", "pe_appointment_date"), ("Mobile App Appointment", "appointment_date")]:
            doc = frappe._dict(doctype=doctype, docstatus=0, **{field: "2026-10-28"})
            with patch.object(api, "_agent", return_value="agent"):
                for status in ("Pending", "Approved", "Checked In", "Completed", "Cancelled"):
                    workflow = frappe._dict(workflow_status=status)
                    self.assertEqual(api._actions(doc, workflow, ("Administrator", {"System Manager"}, True)),
                                     ["reschedule"] if status in ("Pending", "Approved") else [])
                    self.assertEqual(api._actions(doc, workflow, ("reception", {"Appointment Receptionist"}, False)), [])
            with self.assertRaises(frappe.ValidationError):
                api._require_current_or_past(doc)

    def test_today_and_past_preserve_status_actions(self):
        for date in ("2026-10-27", "2026-10-26"):
            doc = frappe._dict(doctype="Patient Encounter", pe_appointment_date=date, sr_encounter_place="Online", docstatus=0)
            with patch.object(api, "_agent", return_value="agent"):
                context = ("Administrator", {"System Manager", "Appointment Receptionist"}, True)
                self.assertIn("approve", api._actions(doc, frappe._dict(workflow_status="Pending"), context))
                self.assertIn("check_in", api._actions(doc, frappe._dict(workflow_status="Approved"), context))
                self.assertIn("complete", api._actions(doc, frappe._dict(workflow_status="Checked In"), context))
                self.assertIn("cancel", api._actions(doc, frappe._dict(workflow_status="Pending"), context))
                api._require_current_or_past(doc)


class TestClinicStatusNormalization(unittest.TestCase):
    def test_known_options_are_normalized_without_changing_other_fields(self):
        for value, expected in [("draft", "Draft"), (" confirmed ", "Confirmed"),
                                ("CHECKED IN", "Checked In"), ("Draft", "Draft")]:
            doc = Mock()
            doc.meta.get_field.return_value = frappe._dict(fieldtype="Select", options="Draft\nConfirmed\nChecked In")
            doc.get.return_value = value
            api.normalize_clinic_appointment_status(doc)
            doc.set.assert_called_once_with("appointment_status", expected)

    def test_unknown_empty_and_missing_options_remain_for_standard_validation(self):
        for value in ("Unknown", "", None):
            doc = Mock()
            doc.meta.get_field.return_value = frappe._dict(fieldtype="Select", options="Draft")
            doc.get.return_value = value
            api.normalize_clinic_appointment_status(doc)
            doc.set.assert_not_called()
        doc.meta.get_field.return_value = None
        api.normalize_clinic_appointment_status(doc)
        doc.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
