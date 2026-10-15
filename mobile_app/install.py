"""Initialize MobileApp module discovery before Frappe syncs its schema."""

import frappe


def refresh_module_map():
    """Refresh stale bench caches before sync_for() enumerates app modules.

    Running this only after_install is too late: sync_for() can silently skip
    every DocType when its cached app_modules does not contain mobile_app.
    Also run before migration so a partially installed site can be repaired.
    Retain the after-install refresh for installations already using that hook.
    """
    frappe.cache.delete_value("app_modules")
    frappe.cache.delete_value("installed_app_modules")
    frappe.setup_module_map()
