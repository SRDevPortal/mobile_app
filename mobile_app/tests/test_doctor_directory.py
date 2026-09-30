"""Run on an initialized site; synthetic records are rolled back after each test."""
import unittest
from unittest.mock import patch

import frappe
from mobile_app.api import doctor_directory as api
from mobile_app.api.practitioners import availability
from mobile_app.mobileapp.doctor_schedule import validate_slots, build_weekly_slots


class TestDoctorDirectory(unittest.TestCase):
    def setUp(self):
        self.user = frappe.session.user
        frappe.set_user("Administrator")
        self.suffix = frappe.generate_hash(length=10)
        self.doctor = frappe.get_doc({"doctype": "Healthcare Practitioner", "first_name": "Schedule Test " + self.suffix,
            "practitioner_name": "Schedule Test " + self.suffix, "status": "Active", "sr_qualification": "Test"}).insert()
        self.date = "2099-01-07"  # Wednesday
        self.rows = [{"day": "Wednesday", "from_time": "10:00", "to_time": "10:30", "maximum_appointments": 1}]

    def tearDown(self):
        frappe.db.rollback()
        frappe.set_user(self.user)

    def details(self):
        return api.get_doctor(self.doctor.name, self.date)

    def weekly(self, rows=None):
        data = self.details()
        schedule = data["schedules"][0] if data["schedules"] else {}
        return api.save_weekly(self.doctor.name, data["doctor"]["modified"], rows if rows is not None else self.rows,
            schedule_id=schedule.get("id"), schedule_modified=schedule.get("modified"))

    def exception(self, **kwargs):
        data = self.details()
        return api.save_exception(self.doctor.name, data["doctor"]["modified"], self.date,
                                 expected_modified=(data["exception"] or {}).get("modified"), **kwargs)

    def test_weekly_slots_feed_booking_and_exceptions_override_then_restore(self):
        self.weekly()
        self.assertEqual(availability(self.doctor.name, self.date)["slots"][0]["time"], "10:00:00")
        self.exception(unavailable=1)
        self.assertEqual(availability(self.doctor.name, self.date)["slots"], [])
        self.exception(slots=[{**self.rows[0], "from_time": "14:00", "to_time": "14:30"}])
        self.assertEqual(availability(self.doctor.name, self.date)["slots"][0]["time"], "14:00:00")
        self.assertEqual(availability(self.doctor.name, "2099-01-14")["slots"][0]["time"], "10:00:00")
        self.exception(remove=1)
        self.assertEqual(availability(self.doctor.name, self.date)["slots"][0]["time"], "10:00:00")

    def test_working_week_and_leave_feed_mobile_api(self):
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        rows = api.preview_weekly(self.doctor.name, days, "10:00", "17:00", 30)["slots"]
        self.assertEqual(len(rows), 70)
        self.weekly(rows)
        slots = availability(self.doctor.name, self.date)["slots"]
        self.assertEqual(len(slots), 14)
        self.assertEqual(slots[0]["time"], "10:00:00")
        self.assertEqual(slots[-1]["end_time"], "17:00:00")
        self.assertEqual(slots[0]["duration"], 30)
        self.assertEqual(availability(self.doctor.name, "2099-01-10")["slots"], [])  # Saturday
        self.exception(unavailable=1)
        self.assertEqual(availability(self.doctor.name, self.date)["slots"], [])
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "Mobile App Appointment", "appointment_external_id": self.suffix,
                "booking_id": self.suffix, "practitioner_id": self.doctor.name, "appointment_date": self.date,
                "appointment_time": "10:00:00", "status": "Confirmed"}).insert()
        self.assertEqual(len(availability(self.doctor.name, "2099-01-08")["slots"]), 14)
        self.exception(remove=1)
        self.assertEqual(len(availability(self.doctor.name, self.date)["slots"]), 14)

    def test_working_hours_reject_incomplete_slots_and_bad_days(self):
        for args in [([], "10:00", "17:00", 30), (["Funday"], "10:00", "17:00", 30),
                     (["Monday"], "17:00", "10:00", 30), (["Monday"], "10:00", "17:10", 30),
                     (["Monday"], "10:00", "17:00", 0), (["Monday"], "10:00", "17:00", 15.5)]:
            with self.assertRaises(ValueError):
                build_weekly_slots(*args)

    def test_weekly_preview_checks_doctor_permissions(self):
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            api.preview_weekly(self.doctor.name, ["Monday"], "10:00", "17:00", 30)

    def leave_range(self, start="2099-01-07", end="2099-01-09", remove=0):
        preview = api.get_leave_range(self.doctor.name, start, end)
        return api.save_leave_range(self.doctor.name, preview["doctor_modified"], start, end,
                                    preview["versions"], remove=remove)

    def test_inclusive_leave_range_and_restore(self):
        self.weekly(build_weekly_slots(["Wednesday", "Thursday", "Friday"], "10:00", "17:00", 30))
        self.assertEqual(self.leave_range()["changed_days"], 3)
        for day in ["2099-01-07", "2099-01-08", "2099-01-09"]:
            self.assertEqual(availability(self.doctor.name, day)["slots"], [])
        self.assertEqual(len(availability(self.doctor.name, "2099-01-14")["slots"]), 14)
        month = api.get_doctor(self.doctor.name, self.date, "2099-01-01", "2099-01-31")
        self.assertEqual(len(month["week_exceptions"]), 3)
        calendar = {day['date']:day for day in month['calendar_days']}
        self.assertEqual(calendar['2099-01-07']['status'], 'leave')
        self.assertEqual(calendar['2099-01-10']['status'], 'off')
        self.assertEqual(calendar['2099-01-14']['hours'], [{'from_time':'10:00:00','to_time':'17:00:00'}])
        self.leave_range(remove=1)
        self.assertEqual(len(availability(self.doctor.name, "2099-01-09")["slots"]), 14)

    def test_calendar_preserves_breaks_and_date_overrides(self):
        self.weekly(self.rows + [{**self.rows[0], 'from_time':'14:00','to_time':'15:00'}])
        day = next(d for d in self.details()['calendar_days'] if d['date']==self.date)
        self.assertEqual(day['hours'], [{'from_time':'10:00:00','to_time':'10:30:00'}, {'from_time':'14:00:00','to_time':'15:00:00'}])
        self.exception(slots=[{**self.rows[0], 'from_time':'11:00','to_time':'12:00'}])
        day = next(d for d in self.details()['calendar_days'] if d['date']==self.date)
        self.assertTrue(day['exception'])
        self.assertEqual(day['hours'], [{'from_time':'11:00:00','to_time':'12:00:00'}])
        self.doctor.reload(); self.doctor.status='Disabled'; self.doctor.save()
        day = next(d for d in self.details()['calendar_days'] if d['date']==self.date)
        self.assertEqual(day['status'], 'inactive')
        self.assertEqual(day['hours'], [])

    def test_leave_single_day_and_restore_does_not_delete_custom_hours(self):
        self.exception(slots=self.rows)
        self.leave_range("2099-01-08", "2099-01-08")
        self.leave_range(remove=1)
        self.assertEqual(availability(self.doctor.name, self.date)["slots"][0]["time"], "10:00:00")
        self.assertFalse(frappe.db.exists(api.EXCEPTION, {"practitioner":self.doctor.name,"date":"2099-01-08"}))

    def test_leave_range_rejects_stale_changes_and_bad_dates(self):
        old = api.get_leave_range(self.doctor.name, self.date, self.date)
        self.exception(unavailable=1)
        with self.assertRaises(frappe.TimestampMismatchError):
            api.save_leave_range(self.doctor.name, old["doctor_modified"], self.date, self.date, old["versions"])
        for start,end in [("2000-01-01", "2000-01-02"), ("2099-01-09", "2099-01-07"), ("2099-01-01", "2101-01-01")]:
            with self.assertRaises(frappe.ValidationError):
                api.get_leave_range(self.doctor.name, start, end)

    def test_leave_range_rolls_back_if_one_day_fails(self):
        from mobile_app.mobileapp.doctype.doctor_availability_exception.doctor_availability_exception import DoctorAvailabilityException
        original = DoctorAvailabilityException.validate
        def fail_second(doc):
            if str(doc.date) == "2099-01-08":
                raise frappe.ValidationError("Synthetic failure")
            return original(doc)
        with patch.object(DoctorAvailabilityException, "validate", fail_second):
            with self.assertRaises(frappe.ValidationError):
                self.leave_range()
        self.assertFalse(frappe.db.exists(api.EXCEPTION, {"practitioner":self.doctor.name}))

    def test_leave_range_guest_denied(self):
        modified = self.details()["doctor"]["modified"]
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            api.get_leave_range(self.doctor.name, self.date, self.date)
        with self.assertRaises(frappe.PermissionError):
            api.save_leave_range(self.doctor.name, modified, self.date, self.date, {})

    def test_shared_schedule_is_copied_for_selected_doctor(self):
        original = self.weekly()["schedule_id"]
        other = frappe.get_doc({"doctype": "Healthcare Practitioner", "first_name": "Other " + self.suffix,
            "practitioner_name": "Other " + self.suffix, "status": "Active", "sr_qualification": "Test",
            "practitioner_schedules": [{"schedule": original}]}).insert()
        self.assertTrue(self.details()["schedules"][0]["shared"])
        updated = self.weekly([{**self.rows[0], "from_time": "11:00", "to_time": "11:30"}])["schedule_id"]
        self.assertNotEqual(original, updated)
        self.assertEqual(availability(other.name, self.date)["slots"][0]["time"], "10:00:00")
        self.assertEqual(availability(self.doctor.name, self.date)["slots"][0]["time"], "11:00:00")

    def test_schedule_save_preserves_broken_legacy_profile_links(self):
        # Simulate an imported practitioner whose masters are not on this site.
        frappe.db.set_value('Healthcare Practitioner', self.doctor.name,
                            {'department':'Missing department '+self.suffix, 'sr_pathy':'Missing pathy '+self.suffix})
        self.doctor.reload()
        old = self.doctor.append('practitioner_schedules',
            {'schedule':'Missing schedule '+self.suffix,'service_unit':'Missing unit '+self.suffix})
        old.db_insert()
        old_schedule, old_unit = old.schedule, old.service_unit
        before = self.doctor.as_dict()
        with self.assertRaises(frappe.LinkValidationError):
            self.doctor.save()
        result = self.weekly()
        self.doctor.reload()
        self.assertEqual(self.doctor.department, before['department'])
        self.assertEqual(self.doctor.sr_pathy, before['sr_pathy'])
        self.assertEqual(self.doctor.practitioner_schedules[0].schedule, old_schedule)
        self.assertEqual(self.doctor.practitioner_schedules[0].service_unit, old_unit)
        self.assertEqual(self.doctor.practitioner_schedules[-1].schedule, result['schedule_id'])
        self.assertEqual(availability(self.doctor.name, self.date)['slots'][0]['time'], '10:00:00')
        # Editing the same schedule still avoids unrelated profile validation.
        self.weekly([{**self.rows[0], 'from_time':'11:00','to_time':'11:30'}])
        self.assertEqual(availability(self.doctor.name, self.date)['slots'][0]['time'], '11:00:00')

    def test_stale_writes_and_unlinked_schedule_rejected(self):
        self.weekly()
        data = self.details()
        self.weekly([])
        with self.assertRaises(frappe.TimestampMismatchError):
            api.save_weekly(self.doctor.name, data["doctor"]["modified"], self.rows)
        with self.assertRaises(frappe.PermissionError):
            api.save_weekly(self.doctor.name, self.details()["doctor"]["modified"], self.rows, schedule_id="unrelated")
        self.exception(unavailable=1)
        old = self.details()["exception"]["modified"]
        self.exception(slots=self.rows)
        with self.assertRaises(frappe.TimestampMismatchError):
            api.save_exception(self.doctor.name, self.details()["doctor"]["modified"], self.date,
                               unavailable=1, expected_modified=old)

    def test_invalid_slots_and_empty_exception_rejected(self):
        for rows in [[{**self.rows[0], "to_time": "09:00"}], self.rows * 2,
                     [{**self.rows[0], "maximum_appointments": 0}], [{**self.rows[0], "maximum_appointments": 1.5}]]:
            with self.assertRaises(ValueError):
                validate_slots(rows)
        with self.assertRaises(frappe.ValidationError):
            self.exception(slots=[])

    def test_guest_cannot_read_or_write(self):
        modified = self.details()["doctor"]["modified"]
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            api.list_doctors()
        with self.assertRaises(frappe.PermissionError):
            api.get_doctor(self.doctor.name, self.date)
        with self.assertRaises(frappe.PermissionError):
            api.save_weekly(self.doctor.name, modified, self.rows)
        with self.assertRaises(frappe.PermissionError):
            api.save_exception(self.doctor.name, modified, self.date, unavailable=1)

    def test_doctor_without_weekly_schedule_can_have_date_hours(self):
        self.exception(slots=self.rows)
        self.assertEqual(availability(self.doctor.name, self.date)["slots"][0]["time"], "10:00:00")
        self.assertEqual(availability(self.doctor.name, "2099-01-14")["slots"], [])

    def test_exception_booking_is_validated_and_existing_booking_is_preserved(self):
        self.exception(slots=self.rows)
        booking = frappe.get_doc({"doctype": "Mobile App Appointment", "appointment_external_id": self.suffix,
            "booking_id": self.suffix, "practitioner_id": self.doctor.name, "appointment_date": self.date,
            "appointment_time": "10:00:00", "status": "Confirmed"}).insert()
        self.assertEqual(booking.duration, 30)
        self.assertFalse(booking.practitioner_schedule)
        self.assertEqual(availability(self.doctor.name, self.date)["slots"], [])
        self.exception(unavailable=1)
        booking.reload()
        self.assertEqual(booking.status, "Confirmed")
        self.assertEqual(self.details()["booked_count"], 1)

    def test_disabled_schedule_and_inactive_doctor_are_not_bookable(self):
        self.weekly()
        data = self.details()
        schedule = data["schedules"][0]
        api.save_weekly(self.doctor.name, data["doctor"]["modified"], self.rows,
                        schedule_id=schedule["id"], schedule_modified=schedule["modified"], disabled=1)
        self.assertEqual(availability(self.doctor.name, self.date)["slots"], [])
        self.exception(slots=self.rows)
        self.doctor.reload()
        self.doctor.status = "Disabled"
        self.doctor.save()
        self.assertEqual(availability(self.doctor.name, self.date)["slots"], [])
