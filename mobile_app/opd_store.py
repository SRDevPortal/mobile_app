"""OPD state in existing Healthcare Settings, Mobile Appointment Workflow and Version.

These are domain records, not Frappe DocTypes. Every write is made under the
Healthcare Settings configuration row lock by the permission-checked queue API.
"""
import hashlib
import json
import frappe
from frappe.utils import now_datetime

SETTINGS = "Healthcare Settings"
FIELD = "custom_opd_configuration"
WORKFLOW = "Mobile Appointment Workflow"
KINDS = {"Clinic", "Room", "Route", "Session", "Visit", "Event"}
COLLECTIONS = {"Clinic":"clinics", "Room":"rooms", "Route":"routes", "Session":"sessions"}
VISIT_FIELDS = {"name":"opd_visit_id", "clinic":"opd_clinic", "patient":"opd_patient",
    "status":"opd_status", "source_key":"opd_source_key", "token_key":"opd_token_key"}


def locked(): return bool(frappe.flags.in_opd_mutation)


def configuration():
    rows = frappe.db.sql("SELECT value FROM `tabSingles` WHERE doctype=%s AND field=%s" + (" FOR UPDATE" if locked() else ""), (SETTINGS, FIELD))
    return json.loads(rows[0][0]) if rows and rows[0][0] else {key:{} for key in COLLECTIONS.values()}


def lock():
    rows = frappe.db.sql("SELECT value FROM `tabSingles` WHERE doctype=%s AND field=%s FOR UPDATE", (SETTINGS, FIELD))
    if not rows: frappe.throw("OPD setup is not installed. Run the mobile_app migration.")


def write_configuration(value):
    if not locked(): frappe.throw("Use Doctor Clinical to update OPD.", frappe.PermissionError)
    frappe.db.sql("UPDATE `tabSingles` SET value=%s WHERE doctype=%s AND field=%s", (frappe.as_json(value), SETTINGS, FIELD))


def _nested(value):
    if isinstance(value, dict): return frappe._dict({k:_nested(v) for k,v in value.items()})
    if isinstance(value, list): return [_nested(v) for v in value]
    return value


class Record(frappe._dict):
    def __init__(self, kind, data=None):
        super().__init__(_nested(data or {})); self.kind=kind
        if kind == 'Clinic':
            for key in ('config_version','event_sequence','last_token'): self.setdefault(key,0)
            self.setdefault('display_users',[])
        if kind == 'Room': self.setdefault('assignments',[])
        if kind == 'Visit':
            self.setdefault('stages',[]);self.setdefault('priority',0)
        self.setdefault('revision',0)
    def is_new(self): return not bool(self.name)
    def set(self,key,value): self[key]=_nested(value)
    def save(self, **kwargs): return save(self)
    def insert(self, **kwargs): return save(self)


def new(kind): return Record(kind)


def get(kind, name):
    rows = records(kind, {'name':name})
    if not rows: frappe.throw(f"{kind} record not found.",frappe.DoesNotExistError)
    return Record(kind,rows[0])


def _match(row, filters):
    if isinstance(filters,str): filters={'name':filters}
    for key,expected in (filters or {}).items():
        actual=row.get(key)
        if isinstance(expected,(list,tuple)):
            op,value=expected
            if op=='!=' and actual==value:return False
            if op=='in' and actual not in value:return False
            if op=='is' and bool(actual)!=(value=='set'):return False
        elif actual!=expected:return False
    return True


def records(kind, filters=None):
    if isinstance(filters,str):filters={'name':filters}
    if kind in COLLECTIONS:
        rows=list(configuration().get(COLLECTIONS[kind],{}).values())
    elif kind=='Visit':
        query={VISIT_FIELDS[k]:v for k,v in (filters or {}).items() if k in VISIT_FIELDS}
        query['opd_payload']=['is','set']
        rows=[json.loads(v) for v in frappe.db.get_values(WORKFLOW,query,'opd_payload',pluck=True,for_update=locked()) if v]
    elif kind=='Event':
        query={'ref_doctype':SETTINGS,'docname':SETTINGS}
        if filters and filters.get('request_key'):query['name']='opd-'+filters['request_key']
        if filters and filters.get('name'):query['name']=filters['name']
        rows=[]
        for data in frappe.db.get_values('Version',query,'data',pluck=True,for_update=locked()):
            event=json.loads(data or '{}').get('opd_event')
            if event:rows.append(event)
    else:raise ValueError(kind)
    return [_nested(row) for row in rows if _match(row,filters)]


def all(kind,filters=None,fields=None,pluck=None,order_by=None):
    rows=records(kind,filters)
    if order_by:
        key=order_by.split()[0];rows.sort(key=lambda r:str(r.get(key) or ''),reverse='desc' in order_by.lower())
    if pluck:return [row.get(pluck) for row in rows]
    return [frappe._dict({key:row.get(key) for key in (fields or ['name'])}) for row in rows]


def value(kind,filters=None,fieldname='name',as_dict=False,**kwargs):
    rows=records(kind,filters)
    if not rows:return None
    row=rows[0]
    if fieldname=='*':return row
    if isinstance(fieldname,(list,tuple)):
        return frappe._dict({k:row.get(k) for k in fieldname}) if as_dict else tuple(row.get(k) for k in fieldname)
    return row.get(fieldname)


def save(record):
    if not locked():frappe.throw('Use Doctor Clinical to update OPD.',frappe.PermissionError)
    record.setdefault('name',frappe.generate_hash(length=20));record.setdefault('creation',str(now_datetime()))
    record['modified']=str(now_datetime())
    data=json.loads(frappe.as_json(dict(record)))
    if record.kind in COLLECTIONS:
        config=configuration();collection=config.setdefault(COLLECTIONS[record.kind],{})
        if record.kind=='Session' and record.status=='Closed':collection.pop(record.name,None)
        else:collection[record.name]=data
        write_configuration(config)
    elif record.kind=='Visit':
        existing=frappe.db.get_value(WORKFLOW,{'opd_visit_id':record.name},'name',for_update=True)
        if not existing and record.source_doctype:
            from mobile_app.api.appointment_calendar import _key
            candidate=_key(record.source_doctype,record.source_name)
            existing=frappe.db.get_value(WORKFLOW,candidate,'name',for_update=True)
        if existing:
            doc=frappe.get_doc(WORKFLOW,existing,for_update=True)
        else:
            source_doctype=record.source_doctype or 'Patient'
            source_name=record.source_name or record.patient
            if record.source_doctype:
                from mobile_app.api.appointment_calendar import _key
                key=_key(source_doctype,source_name)
            else:key=hashlib.sha256(('opd-walkin:'+record.name).encode()).hexdigest()
            doc=frappe.get_doc({'doctype':WORKFLOW,'appointment_key':key,'reference_doctype':source_doctype,
                'reference_name':source_name,'workflow_status':'Checked In' if not record.source_doctype else 'Approved'})
        doc.opd_payload=frappe.as_json(data)
        for key,field in VISIT_FIELDS.items():doc.set(field,record.get(key))
        doc.flags.ignore_version=True;doc.save(ignore_permissions=True)
    elif record.kind=='Event':
        record.name='opd-'+record.request_key;data['name']=record.name
        frappe.get_doc({'doctype':'Version','ref_doctype':SETTINGS,'docname':SETTINGS,
            'data':frappe.as_json({'opd_event':data})}).insert(ignore_permissions=True,set_name=record.name)
    return record


def delete(kind,name):
    if not locked():frappe.throw('Use Doctor Clinical to update OPD.',frappe.PermissionError)
    config=configuration();config.get(COLLECTIONS[kind],{}).pop(name,None);write_configuration(config)


def protect_workflow(doc,method=None):
    if locked() or frappe.flags.in_migrate:return
    old=frappe.db.get_value(WORKFLOW,doc.name,['opd_payload',*VISIT_FIELDS.values()],as_dict=True,for_update=True) if not doc.is_new() else {}
    if any((doc.get(field) or '') != ((old or {}).get(field) or '') for field in ['opd_payload',*VISIT_FIELDS.values()]):
        frappe.throw('Manage OPD visits through Doctor Clinical.',frappe.PermissionError)


def protect_settings(doc,method=None):
    if locked() or frappe.flags.in_migrate:return
    rows=frappe.db.sql('SELECT value FROM `tabSingles` WHERE doctype=%s AND field=%s FOR UPDATE',(SETTINGS,FIELD))
    # A settings form opened before a queue action must not overwrite newer room state.
    # Ignore writes to this hidden field from the generic settings form/API.
    if rows:doc.set(FIELD,rows[0][0])


def event_for_request(key):
    data=frappe.db.get_value('Version','opd-'+key,'data',for_update=True)
    return _nested(json.loads(data).get('opd_event')) if data else None
