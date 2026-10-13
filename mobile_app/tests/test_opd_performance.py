import json
import unittest
from unittest.mock import patch
import frappe
from mobile_app.tests import test_opd_queue
from mobile_app.api import opd_queue as api
from mobile_app import opd_store as store

class TestOPDPerformance(unittest.TestCase):
    def setUp(self):
        self.fixture=test_opd_queue.TestOPDQueue();self.fixture.setUp()
        with patch('mobile_app.token_notifications.queue_token'):
            visit=self.fixture.visit()
        self.template=dict(store.get('Visit',visit['name']))
    def tearDown(self):self.fixture.tearDown()
    def seed(self,count,historical=False):
        for i in range(count):
            name='opd-perf-'+frappe.generate_hash(length=18)
            data=dict(self.template,name=name,token_number=i+10,
                visit_date='2020-01-01' if historical else self.template['visit_date'],status='Completed' if historical else 'Active')
            frappe.get_doc({'doctype':store.WORKFLOW,'name':name,'appointment_key':name,
                'reference_doctype':'Patient','reference_name':data['patient'],'workflow_status':'Checked In',
                'opd_visit_id':name,'opd_clinic':data['clinic'],'opd_patient':data['patient'],
                'opd_status':data['status'],'opd_visit_date':data['visit_date'],
                'opd_queue_status':'Completed' if historical else 'Waiting','opd_payload':frappe.as_json(data)}).db_insert()
    def measure(self):
        original=frappe.db.sql;stats={'calls':0,'rows':0}
        def sql(*args,**kwargs):
            stats['calls']+=1;result=original(*args,**kwargs)
            if isinstance(result,(list,tuple)):stats['rows']+=len(result)
            return result
        with patch.object(frappe.db,'sql',side_effect=sql):result=api.daily_visits(self.fixture.clinic)
        return stats,result
    def test_historical_visits_do_not_increase_queries_or_rows_loaded(self):
        self.measure();before,result=self.measure();self.seed(200,True)
        after,current=self.measure()
        self.assertEqual(before,after)
        self.assertEqual(result,current)
    def test_database_pagination_preserves_daily_totals(self):
        self.seed(104)
        first=api.queue_snapshot(self.fixture.clinic)
        second=api.daily_visits(self.fixture.clinic,offset=100)
        self.assertEqual(len(first['visits']),100);self.assertTrue(first['daily']['has_more'])
        self.assertEqual(first['daily']['counts']['Waiting'],105)
        self.assertEqual(len(second['visits']),5);self.assertFalse(second['has_more'])
        self.assertFalse({v['name'] for v in first['visits']} & {v['name'] for v in second['visits']})
    def test_previous_active_visits_are_retained_for_end_day(self):
        visit=store.Record('Visit',self.template)
        visit.visit_date=frappe.utils.add_days(frappe.utils.today(),-1)
        frappe.flags.in_opd_mutation=True
        try:store.lock(self.fixture.clinic);store.save(visit)
        finally:frappe.flags.in_opd_mutation=False
        result=api.daily_visits(self.fixture.clinic)
        self.assertEqual(result['counts']['Total'],0);self.assertEqual(result['previous_active'],1)
        self.assertEqual(len(api.preview_end_day(self.fixture.clinic,frappe.utils.today())['visits']),1)

    def test_legacy_storage_migration_preserves_state_and_backfills_dates(self):
        from mobile_app.opd_setup import setup_partitioned_storage
        config=store.configuration()
        config.pop('storage_version',None)
        # Reconstruct a pre-upgrade settings row inside this rolled-back transaction.
        frappe.db.sql('UPDATE `tabSingles` SET value=%s WHERE doctype=%s AND field=%s',
            (frappe.as_json(config),store.SETTINGS,store.FIELD))
        frappe.db.delete(store.STATE, {'clinic':self.fixture.clinic})
        frappe.db.set_value(store.WORKFLOW, {'opd_visit_id':self.template['name']},
            {'opd_visit_date':None,'opd_queue_status':None})
        with patch.object(frappe,'reload_doc'),patch.object(frappe.db,'add_index'):
            setup_partitioned_storage()
            after=store.configuration()
            for collection in [*store.COLLECTIONS.values(),'appointment_branches']:
                self.assertEqual(config.get(collection,{}),after.get(collection,{}))
            self.assertEqual(api.daily_visits(self.fixture.clinic)['counts']['Waiting'],1)
            setup_partitioned_storage()
            self.assertEqual(after,store.configuration())

    def test_search_and_status_filters_cover_the_whole_day(self):
        self.seed(104)
        rows=store.records('Visit',{'clinic':self.fixture.clinic})
        target=rows[-1]
        target.patient_name='Distinct Patient Beyond First Page'
        target.status='Completed'
        frappe.flags.in_opd_mutation=True
        try:store.lock(self.fixture.clinic);store.save(store.Record('Visit',target))
        finally:frappe.flags.in_opd_mutation=False
        result=api.queue_snapshot(self.fixture.clinic,search='beyond first page',status='Completed')
        self.assertEqual(result['daily']['counts']['Total'],105)
        self.assertEqual(result['daily']['filtered_total'],1)
        self.assertEqual([v['name'] for v in result['visits']],[target.name])
        self.assertFalse(api.daily_visits(self.fixture.clinic,search='beyond first page',status='Waiting')['visits'])
        self.assertEqual(len(api.daily_visits(self.fixture.clinic,search='001')['visits']),1)

    def test_long_appointment_names_preserve_branch_assignments(self):
        name='a'*140
        frappe.flags.in_opd_mutation=True
        try:
            store.lock(self.fixture.clinic)
            store.set_appointment_branch('Mobile App Appointment',name,self.fixture.clinic)
        finally:frappe.flags.in_opd_mutation=False
        self.assertEqual(store.appointment_branch('Mobile App Appointment',name),self.fixture.clinic)
        self.assertEqual(store.configuration()['appointment_branches']['Mobile App Appointment:'+name],self.fixture.clinic)
