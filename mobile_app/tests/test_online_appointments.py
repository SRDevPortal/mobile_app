"""Doctor reassignment permissions and booking validation on a rollback-only site."""
import unittest
from unittest.mock import patch
import frappe
from mobile_app.api import appointment_calendar as api
from mobile_app.tests import test_appointment_calendar as fixtures


class TestOnlineDoctorChanges(unittest.TestCase):
    tearDown = fixtures.TestAppointmentCalendar.tearDown

    def setUp(self):
        fixtures.TestAppointmentCalendar.setUp(self)
        self.roles[self.agent] = ['Appointment Agent']
        self.roles[self.other] = ['Appointment Manager']
        self.new_doctor = 'online-doctor-' + self.suffix
        frappe.get_doc({'doctype':'Healthcare Practitioner','name':self.new_doctor,
            'first_name':'Online Doctor','practitioner_name':'Online Doctor','status':'Active',
            'custom_accept_online_appointments':1,'user_id':self.other}).db_insert()
        frappe.db.set_value('Mobile App Appointment',self.name,
            {'consultation_type':'https://meet.google.com/abc-defg-hij','booking_id':self.name})
        self.availability=patch('mobile_app.api.practitioners._availability',
            return_value=[{'time':'10:00:00','schedule_id':'','duration':30}])
        self.availability.start();self.addCleanup(self.availability.stop)

    def change(self, **changes):
        args=dict(doctype='Mobile App Appointment',name=self.name,doctor=self.new_doctor,
            expected_doctor=self.doctor,expected_status='Pending',reason='Doctor unavailable')
        args.update(changes)
        return api.change_online_doctor(**args)

    def test_agent_changes_doctor_without_changing_workflow_or_time(self):
        frappe.set_user(self.agent)
        result=self.change()
        self.assertEqual(result['doctor_id'],self.new_doctor)
        self.assertEqual(result['status'],'Pending')
        self.assertEqual(result['time'],'10:00:00')
        self.assertTrue(result['online'])
        self.assertIn('Doctor changed:',result['history'][0]['content'])
        self.assertIn('Doctor unavailable',result['history'][0]['content'])
        saved=frappe.get_doc('Mobile App Appointment',self.name)
        self.assertEqual(saved.doctor_user,self.other)
        self.assertEqual(saved.status,'Booked')

    def test_manager_can_change_another_agents_booking(self):
        frappe.db.set_value('Mobile App Appointment',self.name,'assigned_agent',self.agent)
        frappe.set_user(self.other)
        self.assertEqual(self.change()['doctor_id'],self.new_doctor)

    def test_only_named_roles_can_change_doctor(self):
        for role in ['Agent','Appointment Receptionist','OPD Staff','Mobile App Doctor','System Manager']:
            self.roles[self.other]=[role];frappe.set_user(self.other)
            with self.assertRaises(frappe.PermissionError):self.change()
        self.assertFalse(frappe.db.get_value('Mobile App Appointment',self.name,'practitioner_id'))

    def test_agent_cannot_change_another_agents_booking(self):
        frappe.db.set_value('Mobile App Appointment',self.name,'assigned_agent',self.other)
        frappe.set_user(self.agent)
        with self.assertRaises(frappe.PermissionError):self.change()

    def test_stale_doctor_and_status_rejected(self):
        frappe.set_user(self.agent)
        for changes in [dict(expected_doctor='old-doctor'),dict(expected_status='Approved')]:
            with self.assertRaises(frappe.TimestampMismatchError):self.change(**changes)

    def test_invalid_target_reason_and_offline_booking_rejected(self):
        frappe.set_user(self.agent)
        with self.assertRaises(frappe.ValidationError):self.change(reason='')
        frappe.db.set_value('Healthcare Practitioner',self.new_doctor,'status','Disabled')
        with self.assertRaises(frappe.ValidationError):self.change()
        frappe.db.set_value('Healthcare Practitioner',self.new_doctor,{'status':'Active','custom_accept_online_appointments':0})
        with self.assertRaises(frappe.ValidationError):self.change()
        frappe.db.set_value('Mobile App Appointment',self.name,'is_online',0)
        with self.assertRaises(frappe.ValidationError):self.change()

    def test_doctor_change_is_only_available_before_approval(self):
        frappe.set_user(self.other)
        self.assertTrue(api.get_appointment('Mobile App Appointment', self.name)['can_change_doctor'])
        key = api._key('Mobile App Appointment', self.name)
        state = frappe.get_doc({'doctype': api.WORKFLOW, 'appointment_key': key,
            'reference_doctype': 'Mobile App Appointment', 'reference_name': self.name,
            'workflow_status': 'Approved'}).insert(ignore_permissions=True)
        for status in ('Approved', 'Checked In', 'Completed', 'Cancelled'):
            with self.subTest(status=status):
                frappe.db.set_value(api.WORKFLOW, state.name, 'workflow_status', status)
                self.assertFalse(api.get_appointment('Mobile App Appointment', self.name)['can_change_doctor'])
                with self.assertRaises(frappe.ValidationError):
                    self.change(expected_status=status)
        self.assertFalse(frappe.db.get_value('Mobile App Appointment', self.name, 'practitioner_id'))

    def test_closed_booking_cannot_change_doctor(self):
        key=api._key('Mobile App Appointment',self.name)
        frappe.get_doc({'doctype':api.WORKFLOW,'appointment_key':key,'reference_doctype':'Mobile App Appointment',
            'reference_name':self.name,'workflow_status':'Completed'}).insert(ignore_permissions=True)
        frappe.set_user(self.other)
        with self.assertRaises(frappe.ValidationError):self.change(expected_status='Completed')

    def test_unavailable_slot_is_not_bypassed(self):
        frappe.set_user(self.agent)
        with patch('mobile_app.api.practitioners._availability',return_value=[]):
            with self.assertRaises(frappe.ValidationError):self.change()
        self.assertFalse(frappe.db.get_value('Mobile App Appointment',self.name,'practitioner_id'))

    def test_client_flag_cannot_grant_practitioner_access(self):
        from mobile_app.api.practitioners import validate_appointment
        frappe.set_user(self.agent)
        doc=frappe.get_doc('Mobile App Appointment',self.name)
        doc.practitioner_id=self.new_doctor
        doc.flags._online_doctor_change=True
        with self.assertRaises(frappe.PermissionError):validate_appointment(doc)

    def test_doctor_lookup_is_role_gated_and_only_returns_online_doctors(self):
        frappe.set_user(self.agent)
        rows=api.online_doctor_query('Healthcare Practitioner',self.new_doctor,'name',0,20)
        self.assertEqual(rows[0][0],self.new_doctor)
        frappe.db.set_value('Healthcare Practitioner',self.new_doctor,'custom_accept_online_appointments',0)
        self.assertFalse(api.online_doctor_query('Healthcare Practitioner',self.new_doctor,'name',0,20))
        self.roles[self.agent]=['Appointment Receptionist']
        with self.assertRaises(frappe.PermissionError):
            api.online_doctor_query('Healthcare Practitioner','','name',0,20)
