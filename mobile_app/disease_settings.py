"""App-owned disease catalogue stored in framework defaults, without a disease DocType."""
import hashlib
import json
import frappe

KEY = "mobile_app_disease_list"


def _read():
    value = frappe.db.get_value("DefaultValue", {"parent": "__global", "defkey": KEY}, "defvalue")
    return json.loads(value or "[]")


def _revision(names):
    return hashlib.sha256(json.dumps(names).encode()).hexdigest()


def _manager():
    return frappe.session.user == "Administrator" or bool(set(frappe.get_roles()) & {"System Manager", "Appointment Manager"})


@frappe.whitelist()
def get_settings():
    if frappe.session.user == "Guest" or not frappe.has_permission("Healthcare Practitioner", "read"):
        frappe.throw("Doctor access required.", frappe.PermissionError)
    names = _read()
    return {"names": names, "revision": _revision(names), "can_manage": _manager()}


@frappe.whitelist(methods=["POST"])
def update(action, name, revision, new_name=None):
    if not _manager():
        frappe.throw("Appointment manager access required.", frappe.PermissionError)
    frappe.db.sql("SELECT name FROM tabDefaultValue WHERE parent=%s AND defkey=%s FOR UPDATE", ("__global", KEY))
    names = _read()
    if revision != _revision(names):
        frappe.throw("The disease list changed. Reopen Settings and try again.", frappe.TimestampMismatchError)
    name = (name or "").strip()
    new_name = (new_name or "").strip()
    if action not in ("add", "rename", "delete"):
        frappe.throw("Unknown disease action.")
    target = name if action == "add" else new_name
    if action != "delete":
        if not target or len(target) > 140:
            frappe.throw("Enter a disease name of 1 to 140 characters.")
        if any(n.casefold() == target.casefold() for n in names if action == "add" or n != name):
            frappe.throw("This disease already exists.")
    if action != "add" and name not in names:
        frappe.throw("Disease not found.")
    if action == "delete":
        if frappe.db.exists("SR Practitioner Disease", {"disease": name}):
            frappe.throw("This disease is assigned to a doctor. Remove those selections before deleting it.")
        names.remove(name)
    elif action == "rename":
        names[names.index(name)] = target
        parents = frappe.get_all("SR Practitioner Disease", filters={"disease": name}, pluck="parent")
        frappe.db.set_value("SR Practitioner Disease", {"disease": name}, "disease", target)
        for parent in set(parents):
            frappe.db.set_value("Healthcare Practitioner", parent, "modified", frappe.utils.now_datetime())
            frappe.clear_document_cache("Healthcare Practitioner", parent)
    else:
        names.append(target)
    frappe.db.set_global(KEY, json.dumps(sorted(names, key=str.casefold)))
    return get_settings()


def migrate():
    if frappe.db.exists("DefaultValue", {"parent": "__global", "defkey": KEY}):
        return
    names = []
    if frappe.db.table_exists("Mobile App Disease"):
        names += frappe.db.sql_list("SELECT disease_name FROM `tabMobile App Disease`")
    if frappe.db.table_exists("SR Practitioner Disease"):
        names += frappe.get_all("SR Practitioner Disease", pluck="disease")
    frappe.db.set_global(KEY, json.dumps(sorted(set(n for n in names if n), key=str.casefold)))
