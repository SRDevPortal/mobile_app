import hashlib

import frappe
from frappe.model.document import Document
from frappe.utils import getdate
from mobile_app.mobileapp.doctor_schedule import validate_slots


class DoctorAvailabilityException(Document):
    def validate(self):
        practitioner = frappe.get_doc("Healthcare Practitioner", self.practitioner)
        practitioner.check_permission("write")
        old = self.get_doc_before_save()
        if old and (old.practitioner != self.practitioner or str(old.date) != str(self.date)):
            frappe.throw("Doctor and date cannot be changed. Remove this exception and create another.")
        self.exception_key = hashlib.sha256(f"{self.practitioner}\n{getdate(self.date)}".encode()).hexdigest()
        rows = [] if self.unavailable else [dict(row.as_dict(), day=getdate(self.date).strftime("%A"))
                                           for row in self.time_slots or []]
        if not self.unavailable and not rows:
            frappe.throw("Add time slots or mark this doctor unavailable all day.")
        try:
            self.set("time_slots", validate_slots(rows))
        except (ValueError, TypeError) as error:
            frappe.throw(str(error))

    def on_trash(self):
        frappe.get_doc("Healthcare Practitioner", self.practitioner).check_permission("write")
