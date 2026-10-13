import copy
import unittest
from unittest.mock import patch
import frappe
from mobile_app.api import v1

class TestSyncPerformance(unittest.TestCase):
    def setUp(self):
        self.user=frappe.session.user;frappe.set_user('Administrator')
        self.id='sync-perf-'+frappe.generate_hash(length=12)
        self.payload={'external_id':self.id,'full_name':'Synthetic sync', 'appointments':[
            {'appointment_external_id':self.id+'-'+str(i),'booking_id':self.id+'-'+str(i),
             'appointment_date':'2099-01-07','appointment_time':'10:00','status':'Confirmed'} for i in range(3)]}
    def tearDown(self):
        frappe.db.rollback();frappe.set_user(self.user)
    def sync(self,payload):
        with patch.object(v1,'require_app_token'),patch.object(v1,'_parse_body',return_value=payload),patch.object(frappe.db,'commit'):
            return v1.users_full_sync()
    def appointments(self):
        return {r.name:r.modified for r in frappe.get_all('Mobile App Appointment',filters={'mobile_app_user':self.id},fields=['name','modified'])}
    def test_identical_sync_preserves_parent_child_and_appointment_versions(self):
        self.sync(self.payload)
        before=frappe.get_doc('Mobile App User',self.id)
        versions=self.appointments()
        self.sync(copy.deepcopy(self.payload))
        after=frappe.get_doc('Mobile App User',self.id)
        self.assertEqual(before.modified,after.modified)
        self.assertEqual([(r.name,r.modified) for r in before.appointments],[(r.name,r.modified) for r in after.appointments])
        self.assertEqual(versions,self.appointments())
    def test_partial_sync_does_not_sync_or_rewrite_appointment_children(self):
        self.sync(self.payload)
        before=frappe.get_doc('Mobile App User',self.id)
        with patch.object(v1,'sync_appointments_from_user') as sync:
            self.sync({'external_id':self.id,'full_name':'Changed'})
            sync.assert_not_called()
        after=frappe.get_doc('Mobile App User',self.id)
        self.assertEqual(after.full_name,'Changed')
        self.assertEqual([(r.name,r.modified) for r in before.appointments],[(r.name,r.modified) for r in after.appointments])
    def test_only_changed_appointment_is_written_and_deletion_preserves_other_ids(self):
        self.sync(self.payload);before=frappe.get_doc('Mobile App User',self.id);versions=self.appointments()
        self.payload['appointments'][1]['status']='Cancelled';self.sync(self.payload)
        after=frappe.get_doc('Mobile App User',self.id);new_versions=self.appointments()
        self.assertEqual([r.name for r in before.appointments],[r.name for r in after.appointments])
        for i,row in enumerate(before.appointments):
            self.assertEqual(new_versions[row.appointment_external_id]==versions[row.appointment_external_id],i!=1)
            self.assertEqual(after.appointments[i].modified==row.modified,i!=1)
        self.payload['appointments']=self.payload['appointments'][:1];self.sync(self.payload)
        remaining=frappe.get_doc('Mobile App User',self.id).appointments
        self.assertEqual([r.name for r in remaining],[before.appointments[0].name])

    def test_standalone_appointment_updates_only_its_parent_child_row(self):
        self.sync(self.payload)
        before=frappe.get_doc('Mobile App User',self.id)
        doc=frappe.get_doc('Mobile App Appointment',self.id+'-1')
        doc.status='Cancelled';doc.save()
        after=frappe.get_doc('Mobile App User',self.id)
        self.assertEqual(after.appointments[1].status,'Cancelled')
        for i in (0,2):self.assertEqual(before.appointments[i].modified,after.appointments[i].modified)
        modified=after.modified
        doc.save()
        self.assertEqual(frappe.db.get_value('Mobile App User',self.id,'modified'),modified)
