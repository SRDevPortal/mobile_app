import unittest
from datetime import date, datetime, timedelta
from mobile_app.mobileapp.practitioner_slots import available_slots


class TestPractitionerSlots(unittest.TestCase):
    day = date(2026, 9, 30)  # Wednesday

    def schedule(self, **changes):
        row = dict(day="Wednesday", from_time="10:15:00", to_time="10:45:00", maximum_appointments=1)
        row.update(changes)
        return dict(name="Morning", disabled=0, time_slots=[row])

    def slots(self, schedules=None, bookings=None, now=None):
        return available_slots(self.day, schedules or [self.schedule()], bookings or [],
                               now or datetime(2026, 9, 29, 12))

    def test_uses_exact_crm_times_not_half_hour_grid(self):
        self.assertEqual([s['time'] for s in self.slots()], ['10:15:00'])
        self.assertEqual(self.slots()[0]['duration'], 30)

    def test_wrong_weekday_and_disabled_schedules(self):
        self.assertEqual(self.slots([self.schedule(day='Monday')]), [])
        self.assertEqual(self.slots([{**self.schedule(), 'disabled': 1}]), [])

    def test_today_buffer_and_past_days(self):
        self.assertEqual(self.slots(now=datetime(2026, 9, 30, 10, 1)), [])
        self.assertEqual(len(self.slots(now=datetime(2026, 9, 30, 10))), 1)
        self.assertEqual(self.slots(now=datetime(2026, 10, 1)), [])

    def test_overlapping_bookings_and_cancelled(self):
        booking = dict(appointment_time='10:00:00', duration=30, status='Confirmed')
        self.assertEqual(self.slots(bookings=[booking]), [])
        self.assertEqual(len(self.slots(bookings=[{**booking, 'status': 'Cancelled'}])), 1)
        self.assertEqual(len(self.slots(bookings=[{**booking, 'duration': 15}])), 1)

    def test_capacity_and_duplicate_schedule_links(self):
        booking = dict(appointment_time='10:15:00', duration=30, status='Confirmed')
        schedule = self.schedule(maximum_appointments=2)
        self.assertEqual(self.slots([schedule, schedule], [booking])[0]['remaining'], 1)
        self.assertEqual(len(self.slots([schedule, schedule], [booking])), 1)
        self.assertEqual(self.slots([schedule], [booking, booking]), [])

    def test_invalid_rows_and_frappe_time_values(self):
        self.assertEqual(self.slots([self.schedule(to_time='09:00:00')]), [])
        self.assertEqual(self.slots([self.schedule(from_time='invalid')]), [])
        self.assertEqual(self.slots([self.schedule(from_time=timedelta(hours=10, minutes=15))])[0]['time'], '10:15:00')


if __name__ == '__main__':
    unittest.main()
