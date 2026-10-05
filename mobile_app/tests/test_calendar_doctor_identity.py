import unittest
from unittest.mock import patch
import frappe
from mobile_app.api import appointment_calendar as api

class TestDoctorIdentity(unittest.TestCase):
    def setUp(self):
        self.roster = [frappe._dict(name="HP1", practitioner_name="Dr Test", user_id="doctor@example.invalid")]
        frappe.local.appointment_calendar_identity_roster = self.roster
        frappe.local.appointment_calendar_doctors = {p.name: p for p in self.roster}

    def tearDown(self):
        frappe.local.appointment_calendar_identity_roster = None
        frappe.local.appointment_calendar_doctors = None

    def test_linked_mobile_and_encounter_share_identity(self):
        mobile = frappe._dict(doctype="Mobile App Appointment", practitioner_id="HP1", doctor_name="Old display")
        encounter = frappe._dict(doctype="Patient Encounter", practitioner="HP1")
        self.assertEqual(api._doctor(mobile), api._doctor(encounter))

    def test_legacy_user_and_name_resolve_to_roster_id(self):
        for values in [{"doctor_user": "doctor@example.invalid"}, {"doctor_name": " Dr  Test "}]:
            self.assertEqual(api._doctor(frappe._dict(doctype="Mobile App Appointment", **values))[0], "HP1")

    def test_ambiguous_name_is_not_assigned_arbitrarily(self):
        self.roster.append(frappe._dict(name="HP2", practitioner_name="Dr Test", user_id="other@example.invalid"))
        self.assertNotIn(api._doctor(frappe._dict(doctype="Mobile App Appointment", doctor_name="Dr Test"))[0], ["HP1", "HP2"])
