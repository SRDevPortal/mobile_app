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


def ensure_workspace():
    """Ensure the public menu entry on both new and already-installed sites.

    Keep site-specific shortcuts, content and role restrictions intact.
    Workspace ordering does not grant access to its underlying documents.
    """
    if not frappe.db.exists("Workspace", "Mobile App"):
        from frappe.modules.import_file import import_file_by_path

        import_file_by_path(
            frappe.get_app_path(
                "mobile_app", "mobileapp", "workspace", "mobile_app", "mobile_app.json"
            ),
            force=True,
        )

    # Runs in install/migrate, including sites where the workspace already exists.
    # Use DB updates to avoid exporting site-specific content into the standard file.
    desired = {"public": 1, "is_hidden": 0, "parent_page": "", "sequence_id": 0.5}
    current = frappe.db.get_value("Workspace", "Mobile App", list(desired), as_dict=True)
    if any(current.get(key) != value for key, value in desired.items()):
        frappe.db.set_value("Workspace", "Mobile App", desired)
    frappe.clear_cache(doctype="Workspace")
    # Sidebar module/permission results are cached per user by Frappe.
    frappe.clear_cache()
