"""Validation shared by weekly schedules and single-date overrides."""
from mobile_app.mobileapp.practitioner_slots import clock, seconds
from datetime import timedelta

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def calendar_days(start, end, schedules, exceptions, active=True):
    """Calendar working windows, with the same override precedence as booking."""
    overrides = {str(item["date"]): item for item in exceptions}
    output = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        override = overrides.get(str(day))
        on_leave = bool(override and override.get("unavailable"))
        rows = []
        if active and not on_leave:
            rows = override["slots"] if override else [row for schedule in schedules
                if not schedule.get("disabled") for row in schedule["slots"]
                if row["day"] == WEEKDAYS[day.weekday()]]
        intervals = []
        for row in rows:
            try:
                begin, finish = seconds(row["from_time"]), seconds(row["to_time"])
            except (ValueError, TypeError):
                continue
            if finish > begin:
                intervals.append((begin, finish))
        merged = []
        for begin, finish in sorted(intervals):
            if merged and begin <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], finish)
            else:
                merged.append([begin, finish])
        output.append({"date": str(day), "status": "inactive" if not active else
            "leave" if on_leave else "working" if merged else "off",
            "exception": bool(override), "hours": [{"from_time": clock(a), "to_time": clock(b)} for a,b in merged]})
    return output


def build_weekly_slots(weekdays, from_time, to_time, slot_minutes=30):
    """Expand working hours into individual appointments; never one all-day slot."""
    if not isinstance(weekdays, list) or not weekdays or any(day not in WEEKDAYS for day in weekdays):
        raise ValueError("Select at least one valid working day.")
    start, end = seconds(from_time), seconds(to_time)
    try:
        minutes = int(slot_minutes)
        if float(slot_minutes) != minutes or not 5 <= minutes <= 240:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Appointment length must be a whole number from 5 to 240 minutes.")
    step = minutes * 60
    if start % 60 or end % 60 or end <= start or (end - start) % step:
        raise ValueError("Working hours must contain complete appointments, with end time after start time.")
    rows = [{"day": day, "from_time": clock(value), "to_time": clock(value + step),
             "maximum_appointments": 1}
            for day in WEEKDAYS if day in weekdays for value in range(start, end, step)]
    return validate_slots(rows)


def validate_slots(rows):
    if not isinstance(rows, list) or len(rows) > 350:
        raise ValueError("Provide no more than 350 time slots.")
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get("day") not in WEEKDAYS:
            raise ValueError("Choose a valid weekday for every slot.")
        start, end = seconds(row.get("from_time")), seconds(row.get("to_time"))
        if end <= start:
            raise ValueError("End time must be after start time on the same day.")
        try:
            capacity = int(row.get("maximum_appointments", 1))
            if float(row.get("maximum_appointments", 1)) != capacity or not 1 <= capacity <= 100:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Capacity must be a whole number from 1 to 100.")
        if any(other["day"] == row["day"] and seconds(other["from_time"]) < end
               and seconds(other["to_time"]) > start for other in result):
            raise ValueError("Time slots on the same day must not overlap.")
        result.append({"day": row["day"], "from_time": clock(start), "to_time": clock(end),
                       "maximum_appointments": capacity,
                       "duration": (end - start) / 60 / capacity})
    return sorted(result, key=lambda row: (WEEKDAYS.index(row["day"]), row["from_time"]))
