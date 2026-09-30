"""Healthcare Practitioner / Practitioner Schedule API for the mobile backend."""
from __future__ import annotations

import frappe
from frappe.utils import getdate, now_datetime, get_system_timezone
from mobile_app.mobileapp.practitioner_slots import available_slots, seconds, clock


def _practitioner(name):
    doc = frappe.get_doc("Healthcare Practitioner", name)
    doc.check_permission("read")
    return doc


def _schedules(doc):
    schedules = []
    for link in doc.get("practitioner_schedules") or []:
        # Broken links are not bookable and must not produce invented slots.
        if not frappe.db.exists("Practitioner Schedule", link.schedule):
            continue
        schedule = frappe.get_doc("Practitioner Schedule", link.schedule)
        schedule.check_permission("read")
        if not schedule.disabled and schedule.time_slots:
            schedules.append({**schedule.as_dict(), "service_unit": link.service_unit})
    return schedules


def _public_doctor(doc, schedules):
    tags = [row.disease for row in doc.get("sr_diseases") or [] if row.get("disease")]
    if not tags and doc.get("department"):
        tags = [part.strip() for part in doc.department.split("/") if part.strip()]
    return {"id": doc.name, "name": doc.practitioner_name or doc.name,
            "specialty": doc.get("sr_qualification") or doc.get("department") or "",
            "tags": tags, "image_url": doc.get("image") or "", "is_active": True,
            "schedules": [{"id": s["name"], "service_unit": s.get("service_unit") or "",
                           "days": sorted({r.day for r in s["time_slots"]})} for s in schedules]}


@frappe.whitelist()
def list_doctors():
    doctors = []
    # get_list enforces the integration user's DocType and row permissions.
    for row in frappe.get_list("Healthcare Practitioner", filters={"status": "Active"},
                               fields=["name"], limit_page_length=0, order_by="practitioner_name asc"):
        doc = _practitioner(row.name)
        schedules = _schedules(doc)
        if schedules or frappe.db.exists("Doctor Availability Exception", {
            "practitioner": doc.name, "date": [">=", getdate()], "unavailable": 0}):
            doctors.append(_public_doctor(doc, schedules))
    return {"doctors": doctors, "timezone": get_system_timezone()}


def _bookings(doc, date, exclude_booking_id=None, lock=False):
    # These are occupancy counts only. Never expose other patients' records to callers.
    rows = frappe.db.sql("""
        SELECT booking_id, appointment_time, duration, status
        FROM `tabMobile App Appointment`
        WHERE appointment_date=%s AND
          (practitioner_id=%s OR
           (IFNULL(practitioner_id, '')='' AND doctor_name=%s))
        """ + (" FOR UPDATE" if lock else ""),
        (date, doc.name, doc.practitioner_name), as_dict=True)
    rows = [r for r in rows if not exclude_booking_id or r.booking_id != exclude_booking_id]
    if frappe.db.exists("DocType", "Patient Appointment"):
        rows += frappe.get_all("Patient Appointment", filters={"practitioner": doc.name,
                               "appointment_date": date, "docstatus": ["<", 2]},
                               fields=["appointment_time", "duration", "status"], limit_page_length=0)
    return rows


def _availability(doc, date, exclude_booking_id=None, lock=False):
    if doc.status != "Active":
        return []
    override_name = frappe.db.get_value("Doctor Availability Exception",
        {"practitioner": doc.name, "date": getdate(date)}, "name")
    if override_name:
        override = frappe.get_doc("Doctor Availability Exception", override_name)
        # A dated replacement has no recurring Practitioner Schedule link.
        schedules = [] if override.unavailable else [{"name": "", "time_slots": override.time_slots}]
    else:
        schedules = _schedules(doc)
    return available_slots(getdate(date), schedules,
                           _bookings(doc, date, exclude_booking_id, lock), now_datetime())


@frappe.whitelist()
def availability(practitioner_id, date, exclude_booking_id=None):
    doc = _practitioner(practitioner_id)
    return {"practitioner_id": doc.name, "date": str(getdate(date)),
            "timezone": get_system_timezone(),
            "slots": _availability(doc, date, exclude_booking_id)}


def validate_appointment(doc):
    """Recheck schedules under a database lock before accepting a changed booking."""
    if not doc.get("practitioner_id"):
        return  # Historical appointments retain their original identity.
    old = doc.get_doc_before_save()
    fields = ("practitioner_id", "practitioner_schedule", "appointment_date", "appointment_time", "status")
    if old and all(str(old.get(f) or "") == str(doc.get(f) or "") for f in fields):
        return
    if str(doc.status or "").lower() in {"cancelled", "canceled", "completed"}:
        return
    frappe.db.sql("SELECT name FROM `tabHealthcare Practitioner` WHERE name=%s FOR UPDATE",
                  (doc.practitioner_id,))
    practitioner = _practitioner(doc.practitioner_id)
    requested = clock(seconds(doc.appointment_time))
    slots = _availability(practitioner, doc.appointment_date, doc.booking_id, lock=True)
    slot = next((s for s in slots if s["time"] == requested and
                 (not doc.get("practitioner_schedule") or s["schedule_id"] == doc.practitioner_schedule)), None)
    if not slot:
        frappe.throw("This doctor or appointment slot is no longer available. Please select another slot.")
    doc.doctor_name = practitioner.practitioner_name or practitioner.name
    doc.doctor_user = practitioner.get("user_id")
    doc.practitioner_schedule = slot["schedule_id"]
    doc.duration = slot["duration"]


@frappe.whitelist(methods=["POST"])
def book_appointment(user_name, appointment):
    """Save standalone and child records in one transaction; no partial success."""
    data = frappe.parse_json(appointment) if isinstance(appointment, str) else appointment
    if not isinstance(data, dict) or any(not data.get(key) for key in
        ("practitioner_id", "booking_id", "appointment_external_id", "appointment_date", "appointment_time")):
        frappe.throw("Practitioner, booking ID, date and time are required")
    if not frappe.has_permission("Mobile App Appointment", "create"):
        frappe.throw("Appointment access denied", frappe.PermissionError)
    parent = frappe.get_doc("Mobile App User", user_name)
    parent.check_permission("write")
    # Consistent lock order across bookings: practitioner, then patient.
    frappe.db.sql("SELECT name FROM `tabHealthcare Practitioner` WHERE name=%s FOR UPDATE",
                  (data["practitioner_id"],))
    frappe.db.sql("SELECT name FROM `tabMobile App User` WHERE name=%s FOR UPDATE", (user_name,))
    parent.reload()
    existing = frappe.db.get_value("Mobile App Appointment", {"booking_id": data["booking_id"]}, "name")
    doc = frappe.get_doc("Mobile App Appointment", existing) if existing else frappe.new_doc("Mobile App Appointment")
    if existing and doc.mobile_app_user != user_name:
        frappe.throw("Booking does not belong to this patient", frappe.PermissionError)
    if str(data.get("status") or "Confirmed").lower() not in {"confirmed", "rescheduled"}:
        frappe.throw("Use Confirmed or Rescheduled for a scheduled booking")
    if existing and doc.practitioner_id and doc.practitioner_id != data["practitioner_id"]:
        frappe.throw("Rescheduling must retain the original practitioner")
    changed_slot = (not existing or str(doc.appointment_date) != str(data.get("appointment_date")) or
                    clock(seconds(doc.appointment_time)) != clock(seconds(data.get("appointment_time"))))
    allowed = {"appointment_external_id", "booking_id", "practitioner_id", "practitioner_schedule",
               "appointment_date", "appointment_time", "patient_name", "mobile_number", "email",
               "consultation_type", "appointment_for", "page_url_disease", "status", "payload_json"}
    if existing and data.get("payload_json"):
        data["payload_json"] = frappe.as_json({
            **(frappe.parse_json(doc.payload_json or "{}") or {}),
            **(frappe.parse_json(data["payload_json"]) or {}),
        })
    doc.update({k: v for k, v in data.items() if k in allowed and
                not (existing and k == "appointment_external_id")})
    if changed_slot and not data.get("practitioner_schedule"):
        doc.practitioner_schedule = None
    doc.status = data.get("status") or "Confirmed"
    doc.mobile_app_user = user_name
    previous = frappe.flags.get("in_appointment_sync_from_user")
    frappe.flags.in_appointment_sync_from_user = True
    try:
        doc.save() if existing else doc.insert()
        row = next((r for r in parent.appointments or [] if r.booking_id == doc.booking_id), None)
        if row is None:
            row = parent.append("appointments", {})
        row.update({k: doc.get(k) for k in allowed | {"doctor_name", "duration"}
                    if row.meta.has_field(k)})
        row.user_id = parent.get("external_id") or parent.name
        parent.save()
    finally:
        frappe.flags.in_appointment_sync_from_user = previous
    return {"booking_id": doc.booking_id, "practitioner_id": doc.practitioner_id,
            "doctor_name": doc.doctor_name, "appointment_time": str(doc.appointment_time)}
