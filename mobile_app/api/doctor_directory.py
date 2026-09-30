"""Permission-checked doctor directory and calendar schedule editing."""
from datetime import timedelta
from html import unescape

import frappe
from frappe.utils import getdate, get_system_timezone, now_datetime
from mobile_app.api.practitioners import _practitioner, _bookings
from mobile_app.mobileapp.doctor_schedule import validate_slots, build_weekly_slots, calendar_days

EXCEPTION = "Doctor Availability Exception"


@frappe.whitelist()
def preview_weekly(practitioner_id, weekdays, from_time, to_time, slot_minutes=30):
    doc = _practitioner(practitioner_id)
    doc.check_permission("write")
    try:
        days = frappe.parse_json(weekdays) if isinstance(weekdays, str) else weekdays
        return {"slots": build_weekly_slots(days, from_time, to_time, slot_minutes)}
    except (ValueError, TypeError) as error:
        frappe.throw(str(error))


def _details(doc):
    diseases = list(dict.fromkeys(unescape(row.disease).strip()
        for row in doc.get("sr_diseases") or [] if row.get("disease") and unescape(row.disease).strip()))
    return {"id": doc.name, "name": doc.practitioner_name or doc.name,
            "diseases": diseases,
            "department": doc.get("department") or "", "qualification": doc.get("sr_qualification") or "",
            "status": doc.status, "phone": doc.get("mobile_phone") or doc.get("mobile_no") or "",
            "email": doc.get("email_id") or "", "hospital": doc.get("hospital") or "",
            "can_edit": bool(doc.has_permission("write")), "modified": str(doc.modified)}


@frappe.whitelist()
def list_doctors():
    return {"doctors": [_details(_practitioner(row.name)) for row in frappe.get_list(
        "Healthcare Practitioner", filters={"status": "Active"}, fields=["name"],
        order_by="practitioner_name asc", limit_page_length=0)],
        "timezone": get_system_timezone()}


@frappe.whitelist()
def get_doctor(practitioner_id, date, range_start=None, range_end=None):
    doc = _practitioner(practitioner_id)
    day = getdate(date)
    schedules, seen = [], set()
    for link in doc.get("practitioner_schedules") or []:
        if link.schedule in seen or not frappe.db.exists("Practitioner Schedule", link.schedule):
            continue
        seen.add(link.schedule)
        schedule = frappe.get_doc("Practitioner Schedule", link.schedule)
        if not schedule.has_permission("read"):
            continue
        shared = bool(frappe.db.exists("Practitioner Service Unit Schedule", {
            "schedule": schedule.name, "parenttype": "Healthcare Practitioner", "parent": ["!=", doc.name]}))
        schedules.append({"id": schedule.name, "modified": str(schedule.modified),
            "disabled": schedule.disabled, "shared": shared,
            "can_edit": bool(doc.has_permission("write") and schedule.has_permission("write")
                             and (not shared or frappe.has_permission("Practitioner Schedule", "create"))),
            "slots": [{k: str(row.get(k)) if k in ("from_time", "to_time") else row.get(k)
                       for k in ("day", "from_time", "to_time", "maximum_appointments")} for row in schedule.time_slots]})
    exception = None
    name = frappe.db.get_value(EXCEPTION, {"practitioner": doc.name, "date": day}, "name")
    if name:
        override = frappe.get_doc(EXCEPTION, name)
        exception = {"id": name, "modified": str(override.modified), "unavailable": override.unavailable,
            "slots": [{"day": row.day, "from_time": str(row.from_time), "to_time": str(row.to_time),
                       "maximum_appointments": row.maximum_appointments} for row in override.time_slots]}
    week_start = day - timedelta(days=day.weekday())
    calendar_start = getdate(range_start) if range_start else week_start
    calendar_end = getdate(range_end) if range_end else week_start + timedelta(days=6)
    if not 0 <= (calendar_end - calendar_start).days <= 62:
        frappe.throw("Choose a calendar range of no more than 63 days.")
    week_exceptions = []
    for row in frappe.get_all(EXCEPTION, filters={"practitioner": doc.name,
            "date": ["between", [calendar_start, calendar_end]]}, pluck="name"):
        item = frappe.get_doc(EXCEPTION, row)
        week_exceptions.append({"date": str(item.date), "unavailable": item.unavailable,
            "slots": [{"from_time": str(slot.from_time), "to_time": str(slot.to_time),
                       "maximum_appointments": slot.maximum_appointments} for slot in item.time_slots]})
    return {"doctor": _details(doc), "date": str(day), "schedules": schedules, "exception": exception,
            "week_exceptions": week_exceptions,
            "calendar_days": calendar_days(calendar_start, calendar_end, schedules, week_exceptions, doc.status == "Active"),
            "can_create_schedule": bool(doc.has_permission("write") and frappe.has_permission("Practitioner Schedule", "create")),
            "can_manage_leave": bool(doc.has_permission("write") and frappe.has_permission(EXCEPTION, "create")),
            "can_edit_exception": bool(doc.has_permission("write") and
                (override.has_permission("write") if name else frappe.has_permission(EXCEPTION, "create"))),
            "can_delete_exception": bool(name and doc.has_permission("write") and override.has_permission("delete")),
            "booked_count": sum(str(row.status or "").lower() not in {"cancelled", "canceled"}
                                for row in _bookings(doc, day)), "timezone": get_system_timezone()}


def _lock_doctor(practitioner_id, expected_modified):
    doc = _practitioner(practitioner_id)
    doc.check_permission("write")
    frappe.db.sql("SELECT name FROM `tabHealthcare Practitioner` WHERE name=%s FOR UPDATE", (doc.name,))
    doc.reload()
    _check_modified(doc, expected_modified)
    return doc


def _check_modified(doc, expected):
    if not expected or str(doc.modified) != str(expected):
        frappe.throw("The schedule changed. Refresh the doctor details before saving again.", frappe.TimestampMismatchError)


@frappe.whitelist(methods=["POST"])
def save_weekly(practitioner_id, doctor_modified, slots, schedule_id=None, schedule_modified=None, disabled=0):
    doc = _lock_doctor(practitioner_id, doctor_modified)
    try:
        rows = validate_slots(frappe.parse_json(slots) if isinstance(slots, str) else slots)
    except (ValueError, TypeError) as error:
        frappe.throw(str(error))
    copy_required = False
    if schedule_id:
        links = [link for link in doc.practitioner_schedules if link.schedule == schedule_id]
        if not links:
            frappe.throw("This schedule does not belong to the doctor.", frappe.PermissionError)
        frappe.db.sql("SELECT name FROM `tabPractitioner Schedule` WHERE name=%s FOR UPDATE", (schedule_id,))
        schedule = frappe.get_doc("Practitioner Schedule", schedule_id)
        schedule.check_permission("write")
        _check_modified(schedule, schedule_modified)
        copy_required = bool(frappe.db.exists("Practitioner Service Unit Schedule", {
            "schedule": schedule_id, "parenttype": "Healthcare Practitioner", "parent": ["!=", doc.name]}))
        if copy_required:
            schedule = frappe.copy_doc(schedule)
    else:
        schedule = frappe.new_doc("Practitioner Schedule")
    schedule.set("time_slots", rows)
    schedule.disabled = int(bool(frappe.utils.cint(disabled)))
    updated_links = []
    new_link = None
    if schedule.is_new():
        schedule.schedule_name = f"{doc.name[:100]} - {frappe.generate_hash(length=10)}"
        schedule.insert()
        if copy_required:
            for link in links:
                link.schedule = schedule.name
                updated_links.append(link)
        else:
            new_link = doc.append("practitioner_schedules", {"schedule": schedule.name})
    else:
        schedule.save()
    # This operation edits availability, not the full practitioner profile.
    # Old imported links (department, pathy, service unit, etc.) may be missing
    # locally. Preserve them; do not bypass validation of new schedules/links.
    # _lock_doctor already checks write access, freshness and locks the parent.
    doc.validate_practitioner_schedules()
    if new_link:
        new_link._action = "save"
        new_link._validate_links()
        new_link.db_insert()
    for link in updated_links:
        frappe.db.set_value(link.doctype, link.name, "schedule", schedule.name)
    frappe.db.set_value(doc.doctype, doc.name, {"modified": now_datetime(), "modified_by": frappe.session.user})
    frappe.clear_document_cache(doc.doctype, doc.name)
    doc.reload()
    doc.notify_update()
    return {"schedule_id": schedule.name}


@frappe.whitelist(methods=["POST"])
def save_exception(practitioner_id, doctor_modified, date, slots=None, unavailable=0,
                   expected_modified=None, remove=0):
    doc = _lock_doctor(practitioner_id, doctor_modified)
    day = getdate(date)
    name = frappe.db.get_value(EXCEPTION, {"practitioner": doc.name, "date": day}, "name")
    if name:
        override = frappe.get_doc(EXCEPTION, name)
        _check_modified(override, expected_modified)
    else:
        if expected_modified:
            frappe.throw("This exception was removed. Refresh before saving.", frappe.TimestampMismatchError)
        override = frappe.new_doc(EXCEPTION)
    if frappe.utils.cint(remove):
        if name:
            frappe.delete_doc(EXCEPTION, name)
    else:
        override.update({"practitioner": doc.name, "date": day, "unavailable": frappe.utils.cint(unavailable)})
        parsed = frappe.parse_json(slots) if isinstance(slots, str) else slots
        if not isinstance(parsed or [], list):
            frappe.throw("Time slots must be a list.")
        override.set("time_slots", parsed or [])
        override.save() if name else override.insert()
    return {"date": str(day)}


def _leave_dates(start_date, end_date):
    if not start_date or not end_date:
        frappe.throw("Choose a start and end date.")
    start, end = getdate(start_date), getdate(end_date)
    if start < getdate():
        frappe.throw("Leave can only be changed for today or upcoming dates.")
    if not 0 <= (end - start).days < 366:
        frappe.throw("End date must be on or after start date, within 366 days.")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _leave_versions(practitioner_id, dates):
    return {str(row.date): str(row.modified) for row in frappe.get_all(EXCEPTION,
        filters={"practitioner": practitioner_id, "date": ["between", [dates[0], dates[-1]]]},
        fields=["date", "modified"])}


@frappe.whitelist()
def get_leave_range(practitioner_id, start_date, end_date):
    doc = _practitioner(practitioner_id)
    doc.check_permission("write")
    dates = _leave_dates(start_date, end_date)
    return {"versions": _leave_versions(doc.name, dates), "doctor_modified": str(doc.modified),
            "days": len(dates)}


@frappe.whitelist(methods=["POST"])
def save_leave_range(practitioner_id, doctor_modified, start_date, end_date, expected_versions, remove=0):
    """Apply one inclusive leave range atomically; never partially save a range."""
    doc = _lock_doctor(practitioner_id, doctor_modified)
    dates = _leave_dates(start_date, end_date)
    expected = frappe.parse_json(expected_versions) if isinstance(expected_versions, str) else expected_versions
    if not isinstance(expected, dict) or expected != _leave_versions(doc.name, dates):
        frappe.throw("Leave dates changed. Review the dates and try again.", frappe.TimestampMismatchError)
    remove = bool(frappe.utils.cint(remove))
    changes = []
    for day in dates:
        name = frappe.db.get_value(EXCEPTION, {"practitioner": doc.name, "date": day}, "name")
        override = frappe.get_doc(EXCEPTION, name) if name else frappe.new_doc(EXCEPTION)
        if remove:
            # Removing leave must not delete a custom working-hours exception.
            if name and override.unavailable:
                override.check_permission("delete")
                changes.append(override)
        else:
            override.check_permission("write" if name else "create")
            override.update({"practitioner": doc.name, "date": day, "unavailable": 1})
            override.set("time_slots", [])
            changes.append(override)
    savepoint = "doctor_leave_range"
    frappe.db.savepoint(savepoint)
    try:
        for override in changes:
            if remove:
                frappe.delete_doc(EXCEPTION, override.name)
            else:
                override.save() if not override.is_new() else override.insert()
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
    return {"start_date": str(dates[0]), "end_date": str(dates[-1]), "changed_days": len(changes)}
