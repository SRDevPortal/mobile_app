"""Pure schedule calculations; all times are in the Frappe site's timezone."""
from datetime import datetime, timedelta


def seconds(value):
    if isinstance(value, timedelta):
        return int(value.total_seconds())
    parts = str(value or "").split(":")
    if len(parts) < 2:
        raise ValueError("Invalid appointment time")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(float(parts[2])) if len(parts) > 2 else 0
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        raise ValueError("Invalid appointment time")
    return hour * 3600 + minute * 60 + second


def clock(value):
    return f"{value // 3600:02d}:{value % 3600 // 60:02d}:{value % 60:02d}"


def available_slots(day, schedules, bookings, now):
    """Use each CRM time-slot row as configured, including its capacity."""
    output = []
    seen = set()
    for schedule in schedules:
        if schedule.get("disabled"):
            continue
        for row in schedule.get("time_slots", []):
            if row.get("day") != day.strftime("%A"):
                continue
            try:
                start, end = seconds(row.get("from_time")), seconds(row.get("to_time"))
            except (ValueError, TypeError):
                continue
            if end <= start:
                continue
            when = datetime.combine(day, datetime.min.time()) + timedelta(seconds=start)
            if when < now + timedelta(minutes=15):
                continue
            capacity = max(1, int(row.get("maximum_appointments") or 1))
            used = 0
            for booking in bookings:
                if str(booking.get("status") or "").lower() in {"cancelled", "canceled"}:
                    continue
                booked_start = seconds(booking.get("appointment_time"))
                # Legacy records without duration occupy their matching configured slot.
                booked_end = booked_start + int(float(booking.get("duration") or (end-start)/60) * 60)
                if booked_start < end and booked_end > start:
                    used += 1
            key = (start, end)
            if used >= capacity or key in seen:
                continue
            seen.add(key)
            output.append({"time": clock(start), "end_time": clock(end),
                           "duration": (end-start)/60, "schedule_id": schedule["name"],
                           "service_unit": schedule.get("service_unit") or "",
                           "remaining": capacity-used})
    return sorted(output, key=lambda slot: (slot["time"], slot["end_time"]))
