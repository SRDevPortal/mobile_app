"""Run run() on the local test site. Uses committed, uniquely named fixtures and cleans them."""
import json
import subprocess
import sys
import frappe
from mobile_app.tests.test_opd_queue import TestOPDQueue
from mobile_app import opd_store as store

WORKER = r"""
import json, sys, frappe
from mobile_app.api import opd_queue as api
frappe.init(site=sys.argv[1]); frappe.connect(); frappe.set_user('Administrator')
args=json.loads(sys.argv[3])
try:
    result=getattr(api,sys.argv[2])(**args)
    frappe.db.commit()
    print(json.dumps({'ok':True,'result':result},default=str))
except Exception as e:
    frappe.db.rollback(); print(json.dumps({'ok':False,'error':type(e).__name__}))
finally: frappe.destroy()
"""

def run():
    fixture=TestOPDQueue(); fixture.setUp()
    patients=[]
    try:
        for i in range(3):
            patient='OPD concurrency '+fixture.req(); patients.append(patient)
            frappe.get_doc({'doctype':'Patient','name':patient,'patient_name':patient}).db_insert()
        frappe.db.commit()
        def race(method, args):
            processes=[subprocess.Popen([sys.executable,'-c',WORKER,frappe.local.site,method,json.dumps(a)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for a in args]
            results=[]
            for process in processes:
                out,err=process.communicate(timeout=45)
                if process.returncode: raise AssertionError(err)
                results.append(json.loads(out.splitlines()[-1]))
            frappe.db.rollback()
            return results
        args=[dict(clinic=fixture.clinic,request_id=fixture.req(),department=fixture.departments[0],doctor=fixture.doctors[0],patient=p) for p in patients[:2]]
        result=race('check_in',args)
        assert all(r['ok'] for r in result),result
        assert {r['result']['token'] for r in result}=={'001','002'},result
        same=dict(args[0],patient=patients[2],request_id=fixture.req())
        result=race('check_in',[same,same])
        assert all(r['ok'] for r in result),result
        assert len({r['result']['name'] for r in result})==1,result
        assert len(store.records('Visit',{'clinic':fixture.clinic}))==3
        session=fixture.open_room(1);frappe.db.commit()
        version=store.value('Session',session,'revision')
        args=[dict(clinic=fixture.clinic,request_id=fixture.req(),session=session,expected_version=version,action='call_next') for _ in range(2)]
        result=race('room_action',args)
        assert sum(r['ok'] for r in result)==1,result
        assert sum(s.state=='Called' and s.room==fixture.rooms['1'] for v in store.records('Visit',{'clinic':fixture.clinic}) for s in v.stages)==1
        print('Concurrency: distinct tokens, duplicate retry and exclusive patient claim passed')
    finally:
        frappe.db.rollback();frappe.set_user('Administrator')
        cleanup_clinic(fixture.clinic)
        for dt,names in [('Patient',patients),('Healthcare Practitioner',fixture.doctors),('Medical Department',fixture.departments)]:
            for name in names: frappe.db.delete(dt,{'name':name})
        frappe.db.commit();fixture.tearDown()


def cleanup_clinic(clinic):
    assert clinic.startswith('OPD-test-')
    previous=frappe.flags.in_opd_mutation;frappe.flags.in_opd_mutation=True
    try:
        store.lock();config=store.configuration()
        for kind,collection in store.COLLECTIONS.items():
            config[collection]={k:v for k,v in config.get(collection,{}).items() if not (k==clinic if kind=='Clinic' else v.get('clinic')==clinic)}
        store.write_configuration(config)
        for event in store.records('Event',{'clinic':clinic}):frappe.db.delete('Version',{'name':event.name})
        frappe.db.delete(store.WORKFLOW,{'opd_clinic':clinic})
    finally:frappe.flags.in_opd_mutation=previous
