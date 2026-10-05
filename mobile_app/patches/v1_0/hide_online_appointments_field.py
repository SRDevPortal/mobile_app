"""Keep online eligibility stored, but manage it only in Doctor Clinical."""
from mobile_app.patches.v1_0.add_online_appointments import execute as update_field


def execute():
    update_field()
