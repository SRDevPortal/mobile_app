"""Point existing support shortcuts at the imported inbox without replacing workspaces."""

import frappe


def execute():
    for name in frappe.get_all("Workspace Shortcut", filters={
        "parent": "Mobile App", "parenttype": "Workspace", "link_to": "Support Ticket",
    }, pluck="name"):
        frappe.db.set_value("Workspace Shortcut", name, "link_to", "App Support Ticket")
    frappe.clear_cache()
