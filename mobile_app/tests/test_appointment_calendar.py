"""Run with an initialized Frappe test site; all test records are rolled back."""

import unittest
from unittest.mock import patch

import frappe

from mobile_app.api import appointment_calendar as api


class TestAppointmentCalendar(unittest.TestCase):
    def setUp(self):
        self.original_user = frappe.session.user
        self.suffix = frappe.generate_hash(length=10)
        self.agent = f"agent-{self.suffix}@example.invalid"
        self.other = f"other-{self.suffix}@example.invalid"
        self.doctor = f"doctor-{self.suffix}@example.invalid"
        self.roles = {self.agent: ["Agent"], self.other: ["Agent"], self.doctor: ["Mobile App Doctor"],
                      "Administrator": ["System Manager"], "Guest": []}
        for user in [self.agent, self.other, self.doctor]:
            frappe.get_doc({"doctype": "User", "name": user, "email": user,
                            "first_name": "Calendar Test", "enabled": 1, "user_type": "System User"}).db_insert()
        self.name = "calendar-test-" + self.suffix
        frappe.get_doc({"doctype": "Mobile App Appointment", "name": self.name,
            "appointment_external_id": self.name, "patient_name": "Calendar Test Patient",
            "appointment_date": "2099-01-10", "appointment_time": "10:00:00",
            "doctor_user": self.doctor, "doctor_name": "Test Doctor", "status": "Booked", "is_online": 1}).db_insert()
        self.role_patch = patch.object(frappe, "get_roles", side_effect=lambda user=None: self.roles.get(user or frappe.session.user, []))
        self.role_patch.start()
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.db.rollback()
        self.role_patch.stop()
        frappe.set_user(self.original_user)

    def update(self, action, expected, **kwargs):
        return api.update_appointment("Mobile App Appointment", self.name, action, expected, **kwargs)

    def test_complete_lifecycle_and_audit(self):
        for action, before, after in [("approve", "Pending", "Approved"),
            ("check_in", "Approved", "Checked In")]:
            result = self.update(action, before)
            self.assertEqual(result["status"], after)
        self.assertTrue(result["checked_in_at"])
        self.assertEqual(result["actions"], [])
        self.assertNotIn("consultation_started_at", result)
        self.assertNotIn("checked_out_at", result)
        self.assertEqual(len(result["history"]), 2)
        self.assertEqual(frappe.db.get_value("Mobile App Appointment", self.name, "status"), "Booked")
        self.assertEqual(frappe.db.count(api.WORKFLOW, {"reference_name": self.name}), 1)

    def test_rejection_requires_reason_and_is_terminal(self):
        with self.assertRaises(frappe.ValidationError):
            self.update("reject", "Pending")
        result = self.update("reject", "Pending", reason="Doctor unavailable")
        self.assertEqual(result["reason"], "Doctor unavailable")
        self.assertEqual(result["actions"], [])
        with self.assertRaises(frappe.PermissionError):
            self.update("check_in", "Cancelled")

    def test_stale_and_out_of_order_actions_rejected(self):
        with self.assertRaises(frappe.PermissionError):
            self.update("complete", "Pending")
        self.update("approve", "Pending")
        with self.assertRaises(frappe.TimestampMismatchError):
            self.update("reject", "Pending", reason="Stale click")

    def test_agent_claim_and_ownership(self):
        frappe.set_user(self.agent)
        self.assertEqual(api.get_appointment("Mobile App Appointment", self.name)["actions"], ["claim"])
        with self.assertRaises(frappe.PermissionError):
            self.update("approve", "Pending")
        self.update("claim", "Pending")
        self.update("approve", "Pending")
        frappe.set_user(self.other)
        with self.assertRaises(frappe.PermissionError):
            api.get_appointment("Mobile App Appointment", self.name)
        listed = api.get_calendar("2099-01-10", "2099-01-11")["appointments"]
        self.assertNotIn(self.name, [r["name"] for r in listed])

    def test_assigned_doctor_can_view_final_check_in_but_not_advance_it(self):
        self.update("approve", "Pending")
        self.update("check_in", "Approved")
        frappe.set_user(self.doctor)
        self.assertEqual(api.get_appointment("Mobile App Appointment", self.name)["actions"], [])
        for action in ["start", "complete", "check_in", "cancel"]:
            with self.assertRaises(frappe.PermissionError):
                self.update(action, "Checked In")

    def test_manager_assignment_is_enforced(self):
        result = self.update("assign", "Pending", agent=self.agent)
        self.assertEqual(result["assigned_agent"], self.agent)
        frappe.set_user(self.agent)
        with self.assertRaises(frappe.PermissionError):
            self.update("assign", "Pending", agent=self.other)
        self.update("approve", "Pending")
        frappe.set_user("Administrator")
        self.update("assign", "Approved", agent=self.other)
        frappe.set_user(self.agent)
        with self.assertRaises(frappe.PermissionError):
            api.get_appointment("Mobile App Appointment", self.name)

    def test_guest_and_invalid_sources_denied(self):
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            api.get_calendar("2099-01-10", "2099-01-11")
        frappe.set_user("Administrator")
        with self.assertRaises(frappe.ValidationError):
            api.get_appointment("User", "Administrator")
        with self.assertRaises(frappe.ValidationError):
            api.get_calendar("2099-01-01", "2099-12-31")

    def test_cancelled_source_overrides_existing_approval(self):
        self.update("approve", "Pending")
        frappe.db.set_value("Mobile App Appointment", self.name, "status", "Cancelled")
        result = api.get_appointment("Mobile App Appointment", self.name)
        self.assertEqual(result["status"], "Cancelled")
        self.assertEqual(result["actions"], [])

    def test_encounter_bookings_are_canonical_and_mobile_link_deduplicates(self):
        encounter = "calendar-encounter-" + self.suffix
        frappe.get_doc({"doctype": "Patient Encounter", "name": encounter,
            "patient": "test-patient", "patient_name": "Encounter Test",
            "sr_encounter_type": "Appointment", "sr_encounter_place": "Online",
            "encounter_date": "2099-01-10", "pe_appointment_date": "2099-01-10",
            "pe_appointment_time": "11:00:00", "status": "Open"}).db_insert()
        frappe.db.set_value("Mobile App Appointment", self.name, "patient_encounter", encounter)
        rows = api.get_calendar("2099-01-10", "2099-01-11")["appointments"]
        self.assertIn(encounter, [r["name"] for r in rows])
        self.assertNotIn(self.name, [r["name"] for r in rows])
        result = api.update_appointment("Patient Encounter", encounter, "approve", "Pending")
        self.assertEqual(result["status"], "Approved")
        self.assertEqual(result["encounter"], encounter)
        self.assertTrue(result["online"])

    def test_linked_clinic_progress_survives_encounter_resync(self):
        from clinic_appointments.api.encounter_sync import _derive_appointment_status

        encounter = "calendar-linked-" + self.suffix
        clinic = "calendar-clinic-" + self.suffix
        patient = "calendar-patient-" + self.suffix
        frappe.get_doc({"doctype": "Patient", "name": patient,
                       "patient_name": "Calendar Patient Name", "mobile": "7700900123"}).db_insert()
        frappe.get_doc({"doctype": "Patient Encounter", "name": encounter,
            "patient": patient, "sr_encounter_type": "Appointment",
            "sr_encounter_status": "Draft", "encounter_reference": clinic, "sr_encounter_place": "Online",
            "pe_appointment_date": "2099-01-10", "pe_appointment_time": "10:00:00"}).db_insert()
        frappe.get_doc({"doctype": "Clinic Appointment", "name": clinic,
            "encounter_reference": encounter, "appointment_status": "Draft"}).db_insert()
        rows = api.get_calendar("2099-01-10", "2099-01-11")["appointments"]
        self.assertEqual(next(r for r in rows if r["name"] == encounter)["patient_name"], "Calendar Patient Name")
        self.assertEqual(api.get_appointment("Patient Encounter", encounter)["patient_name"], "Calendar Patient Name")
        self.assertEqual(next(r for r in rows if r["name"] == encounter)["phone"], "7700900123")
        self.assertEqual(api.get_appointment("Patient Encounter", encounter)["phone"], "7700900123")
        for action, before, expected in [("approve", "Pending", "Confirmed"),
            ("check_in", "Approved", "Completed")]:
            api.update_appointment("Patient Encounter", encounter, action, before)
            self.assertEqual(frappe.db.get_value("Clinic Appointment", clinic, "appointment_status"), expected)
            self.assertEqual(_derive_appointment_status(frappe.get_doc("Patient Encounter", encounter)), expected)
        self.assertEqual(frappe.db.get_value("Patient Encounter", encounter, "sr_encounter_status"), "Draft")

    def test_doctor_roster_includes_zero_bookings_and_respects_doctor_scope(self):
        own = "calendar-own-doctor-" + self.suffix
        other = "calendar-other-doctor-" + self.suffix
        disabled = "calendar-disabled-doctor-" + self.suffix
        for name, user, status in [(own, self.doctor, "Active"), (other, self.other, "Active"),
                                   (disabled, self.doctor, "Disabled")]:
            frappe.get_doc({"doctype": "Healthcare Practitioner", "name": name,
                "practitioner_name": name, "user_id": user, "status": status}).db_insert()
        roster = api.get_calendar("2099-02-01", "2099-02-02")["doctors"]
        self.assertIn(own, [d.id for d in roster])
        self.assertIn(other, [d.id for d in roster])
        self.assertNotIn(disabled, [d.id for d in roster])
        frappe.set_user(self.doctor)
        roster = api.get_calendar("2099-02-01", "2099-02-02")["doctors"]
        self.assertEqual([d.id for d in roster], [own])

    def test_appointment_manager_has_full_calendar_access_without_system_manager(self):
        self.roles[self.agent] = ["Appointment Manager"]
        frappe.db.set_value("Mobile App Appointment", self.name, "assigned_agent", self.other)
        frappe.set_user(self.agent)
        self.assertTrue(api.get_calendar("2099-01-10", "2099-01-11")["can_assign"])
        self.update("assign", "Pending", agent=self.agent)
        for action, before in [("approve", "Pending"), ("check_in", "Approved")]:
            self.update(action, before)
        self.assertEqual(api.get_appointment("Mobile App Appointment", self.name)["status"], "Checked In")

    def test_reception_visibility_and_actions_are_enforced_in_api(self):
        # A legacy Agent role must not accidentally broaden receptionist access.
        self.roles[self.other] = ["Appointment Receptionist", "Agent"]
        frappe.set_user(self.other)
        self.assertEqual(api.get_calendar("2099-01-10", "2099-01-11")["appointments"], [])
        with self.assertRaises(frappe.PermissionError):
            api.get_appointment("Mobile App Appointment", self.name)
        with self.assertRaises(frappe.PermissionError):
            self.update("approve", "Pending")
        frappe.set_user("Administrator")
        self.update("approve", "Pending")
        frappe.set_user(self.other)
        listing = api.get_calendar("2099-01-10", "2099-01-11")
        self.assertTrue(listing["reception_only"])
        self.assertFalse(listing["can_assign"])
        self.assertEqual([r["name"] for r in listing["appointments"]], [self.name])
        detail = api.get_appointment("Mobile App Appointment", self.name)
        self.assertEqual(detail["actions"], ["check_in"])
        self.assertFalse(detail["can_open_source"])
        with self.assertRaises(frappe.PermissionError):
            self.update("assign", "Approved", agent=self.other)
        checked_in = self.update("check_in", "Approved")
        self.assertEqual(checked_in["actions"], [])
        with self.assertRaises(frappe.PermissionError):
            self.update("start", "Checked In")
        frappe.set_user("Administrator")
        with self.assertRaises(frappe.PermissionError):
            self.update("start", "Checked In")
        frappe.db.set_value("Mobile App Appointment", self.name, "status", "Cancelled")
        frappe.set_user(self.other)
        with self.assertRaises(frappe.PermissionError):
            api.get_appointment("Mobile App Appointment", self.name)
        self.assertEqual(api.get_calendar("2099-01-10", "2099-01-11")["appointments"], [])

    def test_search_fields_are_returned_only_for_authorized_bookings(self):
        frappe.db.set_value("Mobile App Appointment", self.name,
            {"mobile_number": "7700900123", "booking_id": "SEARCH-BOOKING", "email": "qa@example.invalid"})
        row = api.get_calendar("2099-01-10", "2099-01-11")["appointments"][0]
        self.assertEqual(row["phone"], "7700900123")
        self.assertEqual(row["booking_id"], "SEARCH-BOOKING")
        self.assertEqual(row["doctor_name"], "Test Doctor")
        self.roles[self.other] = ["Appointment Receptionist"]
        frappe.set_user(self.other)
        self.assertEqual(api.get_calendar("2099-01-10", "2099-01-11")["appointments"], [])

    def test_agent_picker_requires_manager_and_limits_candidates(self):
        self.roles[self.agent] = ["Appointment Manager"]
        frappe.set_user(self.agent)
        self.assertIn("Administrator", [r[0] for r in api.agent_query("User", "Administrator", "name", 0, 10)])
        self.assertEqual(api.agent_query("User", "Guest", "name", 0, 10), ())
        self.roles[self.other] = ["Appointment Receptionist"]
        frappe.set_user(self.other)
        with self.assertRaises(frappe.PermissionError):
            api.agent_query("User", "", "name", 0, 10)

    def test_cancel_approved_and_check_in_are_terminal(self):
        self.update("approve", "Pending")
        result = self.update("cancel", "Approved", reason="Patient cancelled")
        self.assertEqual(result["status"], "Cancelled")
        self.assertEqual(result["actions"], [])
        with self.assertRaises(frappe.PermissionError):
            self.update("approve", "Cancelled")

    def test_legacy_statuses_normalize_without_reopening_appointments(self):
        self.update("approve", "Pending")
        for legacy, final in [("In Consultation", "Checked In"), ("Completed", "Checked In"), ("Rejected", "Cancelled")]:
            frappe.db.set_value(api.WORKFLOW, api._key("Mobile App Appointment", self.name), "workflow_status", legacy)
            result = api.get_appointment("Mobile App Appointment", self.name)
            self.assertEqual(result["status"], final)
            self.assertEqual(result["actions"], [])
            with self.assertRaises(frappe.ValidationError):
                self.update("assign", final, agent=self.agent)

    def test_patient_encounter_status_is_synced_and_cannot_be_forged(self):
        name = "calendar-status-" + self.suffix
        doc = frappe.get_doc({"doctype":"Patient Encounter", "name":name,
            "sr_encounter_type":"Appointment", "sr_encounter_place":"Online", "custom_appointment_status":"Approved"})
        api.sync_encounter_status(doc)
        self.assertEqual(doc.custom_appointment_status, "Pending")
        doc.db_insert()
        for action, before, final in [("approve", "Pending", "Approved"), ("check_in", "Approved", "Checked In")]:
            api.update_appointment("Patient Encounter", name, action, before)
            self.assertEqual(frappe.db.get_value("Patient Encounter", name, api.APPOINTMENT_STATUS_FIELD), final)
        doc.reload(); doc.custom_appointment_status = "Pending"
        api.sync_encounter_status(doc)
        self.assertEqual(doc.custom_appointment_status, "Checked In")
        doc.docstatus = 2
        api.sync_encounter_status(doc, method="on_cancel")
        self.assertEqual(frappe.db.get_value("Patient Encounter", name, api.APPOINTMENT_STATUS_FIELD), "Cancelled")
        doc.sr_encounter_type = "Followup"; api.sync_encounter_status(doc)
        self.assertEqual(doc.custom_appointment_status, "")

    def test_change_tokens_match_sections_and_track_insert_move_delete(self):
        def changes():
            return api.get_calendar_changes("2099-01-01", "2099-01-22")["chunks"]
        before = changes()
        self.assertEqual(len(before), 3)
        for section in before:
            payload = api.get_calendar(section["start"], section["end"])
            self.assertEqual(payload["revisions"], [section])
        self.assertEqual(before, changes())
        name = "calendar-lazy-" + self.suffix
        frappe.get_doc({"doctype": "Mobile App Appointment", "name": name,
            "appointment_date": "2099-01-02", "patient_name": "Lazy load test"}).db_insert()
        inserted = changes()
        self.assertNotEqual(before[0], inserted[0])
        self.assertEqual(before[1:], inserted[1:])
        frappe.db.set_value("Mobile App Appointment", name, "appointment_date", "2099-01-20")
        moved = changes()
        self.assertEqual(before[0], moved[0])
        self.assertNotEqual(before[2], moved[2])
        frappe.db.delete("Mobile App Appointment", {"name": name})
        self.assertEqual(before, changes())

    def test_change_tokens_track_workflow_and_recheck_permissions(self):
        before = api.get_calendar_changes("2099-01-01", "2099-01-22")["chunks"]
        self.update("approve", "Pending")
        after = api.get_calendar_changes("2099-01-01", "2099-01-22")["chunks"]
        self.assertEqual(before[0], after[0])
        self.assertNotEqual(before[1], after[1])
        self.assertEqual(before[2], after[2])
        self.roles[self.other] = ["Appointment Receptionist"]
        frappe.set_user(self.other)
        result = api.get_calendar("2099-01-08", "2099-01-15")
        self.assertEqual([r["name"] for r in result["appointments"]], [self.name])
        self.assertNotEqual(after[1]["revision"], result["revisions"][0]["revision"])
        self.roles[self.other] = []
        with self.assertRaises(frappe.PermissionError):
            api.get_calendar_changes("2099-01-01", "2099-01-22")
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            api.get_calendar_changes("2099-01-01", "2099-01-22")
        frappe.set_user("Administrator")
        with self.assertRaises(frappe.ValidationError):
            api.get_calendar_changes("2099-01-01", "2099-12-31")

    def test_change_tokens_track_encounter_and_patient_edits(self):
        patient = "calendar-lazy-patient-" + self.suffix
        encounter = "calendar-lazy-encounter-" + self.suffix
        frappe.get_doc({"doctype": "Patient", "name": patient,
            "patient_name": "Lazy patient", "mobile": "7700900123"}).db_insert()
        frappe.get_doc({"doctype": "Patient Encounter", "name": encounter,
            "patient": patient, "sr_encounter_type": "Appointment",
            "encounter_date": "2099-01-10"}).db_insert()
        def token():
            return api.get_calendar_changes("2099-01-08", "2099-01-15")["chunks"][0]
        before = token()
        frappe.db.set_value("Patient", patient, "mobile", "7700900124")
        self.assertNotEqual(before, token())
        before = token()
        api.update_appointment("Patient Encounter", encounter, "approve", "Pending")
        self.assertNotEqual(before, token())
        before = token()
        frappe.db.set_value("Patient Encounter", encounter, "pe_appointment_date", "2099-01-21")
        self.assertNotEqual(before, token())

    def test_database_stops_slow_reads_without_leaking_session_timeout(self):
        import time
        before = frappe.db.sql("SELECT @@session.max_statement_time")[0][0]
        started = time.monotonic()
        with patch.object(api, "CALENDAR_QUERY_TIMEOUT", 0.05):
            with self.assertRaises(frappe.QueryTimeoutError):
                api._calendar_sql("SELECT SLEEP(2)")
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(frappe.db.sql("SELECT @@session.max_statement_time")[0][0], before)
        self.assertEqual(api._calendar_sql("SELECT 1 AS value")[0].value, 1)
        # No settings or writes can accidentally run through the read helper.
        with self.assertRaises(ValueError):
            api._calendar_sql("DELETE FROM `tabPatient Encounter`")
