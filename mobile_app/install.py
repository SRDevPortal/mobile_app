"""Initialize MobileApp module discovery before install setup hooks."""

import frappe


def refresh_module_map():
    """Rebuild cached module lists after adding MobileApp to the bench."""
    frappe.cache.delete_value("app_modules")
    frappe.cache.delete_value("installed_app_modules")
    frappe.setup_module_map()
