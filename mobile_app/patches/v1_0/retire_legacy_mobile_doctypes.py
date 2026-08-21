import frappe


LEGACY_DOCTYPES = (
	"Mobile App User Profile",
	"Mobile App User Session",
	"Mobile App Disease",
	"Mobile App User Disease Selection",
	"Mobile App Health Entry",
	"Mobile App Prescription",
	"Mobile App Doctor",
	"Mobile App Appointment",
	"Mobile App Notification",
	"Mobile App Support Ticket",
	"Mobile App Webhook Event",
)


def execute():
	"""Keep legacy doctypes/data intact on live sites.

	Older builds used this patch to delete retired DocTypes after migration. The
	final mobile_app package must be safe to deploy over existing live data, so
	this patch is intentionally non-destructive.
	"""
	for dt in LEGACY_DOCTYPES:
		if frappe.db.exists("DocType", dt):
			frappe.logger("mobile_app").info("Keeping legacy DocType during migration: %s", dt)
