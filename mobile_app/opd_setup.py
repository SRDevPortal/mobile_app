"""OPD uses existing settings, appointment workflows and native Version audit records."""
import frappe
from mobile_app import opd_store as store

LEGACY = {'OPD Clinic':'Clinic','OPD Room':'Room','OPD Department Route':'Route',
    'OPD Room Session':'Session','OPD Visit':'Visit','OPD Queue Event':'Event'}


def after_migrate():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields({'Healthcare Settings':[{'fieldname':store.FIELD,
        'label':'Doctor Clinical room configuration','fieldtype':'Long Text','hidden':1,'read_only':1}]},update=True)
    for role in ('OPD Staff','OPD Display'):
        if not frappe.db.exists('Role',role):
            frappe.get_doc({'doctype':'Role','role_name':role,'desk_access':1}).insert(ignore_permissions=True)
    for fields in (['opd_clinic','opd_status'],['opd_patient']):frappe.db.add_index(store.WORKFLOW,fields)
    if not frappe.db.sql('SELECT value FROM `tabSingles` WHERE doctype=%s AND field=%s',(store.SETTINGS,store.FIELD)):
        frappe.db.set_single_value(store.SETTINGS,store.FIELD,frappe.as_json({k:{} for k in store.COLLECTIONS.values()}))
    previous=frappe.flags.in_opd_mutation;frappe.flags.in_opd_mutation=True
    try:
        store.lock()
        # Preserve any state from the earlier local implementation before removing its DocTypes.
        for doctype,kind in LEGACY.items():
            if not frappe.db.exists('DocType',doctype):continue
            for name in frappe.get_all(doctype,pluck='name'):
                old=frappe.db.get_value(doctype,name,'*',as_dict=True)
                for field in frappe.get_meta(doctype).fields:
                    if field.fieldtype=='Table':
                        old[field.fieldname]=frappe.db.get_values(field.options,{'parent':name,'parenttype':doctype,'parentfield':field.fieldname},'*',as_dict=True,order_by='idx')
                if kind=='Event' and store.event_for_request(old.request_key):continue
                if kind!='Event' and store.value(kind,name):continue
                store.save(store.Record(kind,old))
        if not store.all('Clinic'):
            store.save(store.Record('Clinic',{'name':'main-clinic','clinic_name':'Main Clinic','enabled':1}))
        # Remove only the superseded OPD metadata. Frappe retains old SQL tables as migration backups.
        for doctype in ['OPD Queue Event','OPD Visit','OPD Room Session','OPD Department Route','OPD Room','OPD Clinic',
                        'OPD Visit Stage','OPD Room Assignment','OPD Display User']:
            if frappe.db.exists('DocType',doctype):
                frappe.delete_doc('DocType',doctype,force=True,ignore_permissions=True,ignore_on_trash=True)
        if frappe.db.exists("Page","opd-queue"):
            frappe.delete_doc("Page","opd-queue",force=True,ignore_permissions=True)
    finally:frappe.flags.in_opd_mutation=previous
