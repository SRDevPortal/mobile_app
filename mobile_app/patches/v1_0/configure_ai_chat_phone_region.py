"""Set the domestic app's default country without rewriting patient data."""

import frappe
from frappe.installer import update_site_config


def execute():
	if frappe.conf.get("mobile_app_ai_phone_region") or frappe.conf.get("mobile_app_ai_require_country_code"):
		return
	update_site_config("mobile_app_ai_phone_region", "IN")
	frappe.conf.mobile_app_ai_phone_region = "IN"
