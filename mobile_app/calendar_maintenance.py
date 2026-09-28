"""Bench-only recovery for the retired calendar query. No HTTP methods exposed."""

import re

import frappe
from frappe.utils import cint


OLD_SELECT = (
    "SELECT name, patient_name, patient, sr_pe_mobile, sr_encounter_type, "
    "sr_encounter_place, pe_appointment_date, encounter_date, pe_appointment_time, "
    "encounter_time, pe_practitioner, practitioner, status, sr_encounter_status, docstatus, created_by_agent "
    "FROM `tabPatient Encounter` WHERE sr_encounter_type='Appointment' "
    "AND COALESCE(pe_appointment_date, encounter_date) >= "
)
OLD_END = " ORDER BY COALESCE(pe_appointment_date, encounter_date) LIMIT 2001"


def _is_old_calendar_read(query):
    normalized = " ".join((query or "").split()).rstrip(";")
    date = r"'\d{4}-\d{2}-\d{2}'"
    pattern = (re.escape(OLD_SELECT) + date
               + re.escape(" AND COALESCE(pe_appointment_date, encounter_date) < ")
               + date + re.escape(OLD_END))
    return bool(re.fullmatch(pattern, normalized, re.IGNORECASE))


def recover_stale_reads(cancel=False):
    """Preview by default; cancel only this site's old SELECTs running >=120s.

    KILL QUERY interrupts the read, not the connection. This does not cancel
    patient writes, unrelated SQL, or other sites' requests. Run from bench after
    closing old calendar tabs and deploying the new version.
    """
    if frappe.session.user != "Administrator":
        frappe.only_for("System Manager")
    cancel = bool(cint(cancel))
    rows = frappe.db.sql("""SELECT ID, TIME, STATE, INFO FROM information_schema.PROCESSLIST
        WHERE DB=DATABASE() AND COMMAND='Query' AND TIME >= 120""", as_dict=True)
    result = []
    for row in rows:
        if not _is_old_calendar_read(row.INFO):
            continue
        item = {"id": row.ID, "seconds": row.TIME, "state": row.STATE, "cancelled": False}
        if cancel:
            # Recheck on the same connection immediately before cancellation.
            current = frappe.db.sql("""SELECT INFO FROM information_schema.PROCESSLIST
                WHERE ID=%s AND DB=DATABASE() AND COMMAND='Query' AND TIME >= 120""",
                (row.ID,), as_dict=True)
            if current and _is_old_calendar_read(current[0].INFO):
                try:
                    frappe.db.sql("KILL QUERY %s", (int(row.ID),))
                    item["cancelled"] = True
                except frappe.db.OperationalError as exc:
                    if exc.args[0] != 1094:  # Query already finished / connection closed.
                        raise
        result.append(item)
    return {"mode": "cancel" if cancel else "preview", "queries": result}


def verify(start=None, end=None):
    """Read-only post-deployment check. Return timings/counts, never patient data."""
    from time import perf_counter
    from frappe.utils import add_days, nowdate
    from mobile_app.api import appointment_calendar as api

    start = start or nowdate()
    end = end or add_days(start, 7)
    api._context()
    indexes = frappe.db.sql("""SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='tabPatient Encounter'
            AND INDEX_NAME='appointment_calendar_dates' ORDER BY SEQ_IN_INDEX""", as_dict=True)
    before = perf_counter()
    changes = api.get_calendar_changes(start, end)
    change_seconds = perf_counter() - before
    before = perf_counter()
    data = api.get_calendar(start, end)
    return {"range": [start, end], "change_check_seconds": round(change_seconds, 3),
            "calendar_seconds": round(perf_counter() - before, 3),
            "appointments": len(data["appointments"]), "doctors": len(data["doctors"]),
            "sections": len(changes["chunks"]), "query_timeout_seconds": api.CALENDAR_QUERY_TIMEOUT,
            "date_index_columns": [row.COLUMN_NAME for row in indexes],
            "stale_calendar_reads": len(recover_stale_reads()["queries"])}
