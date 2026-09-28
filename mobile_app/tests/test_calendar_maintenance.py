"""Recovery must never match writes, unrelated SELECTs or another query shape."""
import unittest
from unittest.mock import patch
import frappe
from mobile_app import calendar_maintenance as maintenance


class TestCalendarMaintenance(unittest.TestCase):
    def test_old_query_match_is_strict(self):
        query = (maintenance.OLD_SELECT + "'2026-09-21'"
                 + " AND COALESCE(pe_appointment_date, encounter_date) < '2026-09-28'"
                 + maintenance.OLD_END)
        self.assertTrue(maintenance._is_old_calendar_read(query))
        self.assertTrue(maintenance._is_old_calendar_read(query.replace(" FROM ", "\nFROM ")))
        for other in [query + " FOR UPDATE", query + "; DELETE FROM `tabPatient Encounter`",
                      query.replace("LIMIT 2001", "LIMIT 1"), "SELECT * FROM `tabPatient Encounter`",
                      "UPDATE `tabPatient Encounter` SET status='Open'", "SELECT SLEEP(1000)", ""]:
            self.assertFalse(maintenance._is_old_calendar_read(other))

    def test_preview_never_cancels_and_rechecks_before_cancel(self):
        query = (maintenance.OLD_SELECT + "'2026-09-21'"
                 + " AND COALESCE(pe_appointment_date, encounter_date) < '2026-09-28'"
                 + maintenance.OLD_END)
        rows = [frappe._dict(ID=123, TIME=300, STATE="Creating sort index", INFO=query)]
        with patch.object(frappe.db, "sql", return_value=rows) as sql:
            result = maintenance.recover_stale_reads()
            self.assertFalse(result["queries"][0]["cancelled"])
            self.assertEqual(sql.call_count, 1)
        with patch.object(frappe.db, "sql", side_effect=[rows, []]) as sql:
            result = maintenance.recover_stale_reads(cancel=True)
            self.assertFalse(result["queries"][0]["cancelled"])
            self.assertEqual(sql.call_count, 2)
        with patch.object(frappe.db, "sql", side_effect=[rows, rows, []]) as sql:
            result = maintenance.recover_stale_reads(cancel=True)
            self.assertTrue(result["queries"][0]["cancelled"])
            self.assertEqual(sql.call_args.args, ("KILL QUERY %s", (123,)))
