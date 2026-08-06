from django.db import migrations


ROLES = [
    ("recommerce_intake", "Recommerce Intake Officer", ["recommerce.intake"]),
    ("recommerce_assessor", "Device Assessor / Grader", ["recommerce.assessment"]),
    ("recommerce_technician", "Refurbishment Technician", ["recommerce.refurbishment"]),
    ("recommerce_qa", "Recommerce QA Officer", ["recommerce.qa"]),
    ("recommerce_inventory", "Recommerce Inventory / Sales Officer", ["recommerce.inventory"]),
    ("recommerce_supervisor", "Recommerce Supervisor", ["recommerce.supervision"]),
    ("recommerce_hq", "HQ Recommerce Admin", ["recommerce.hq"]),
]


def seed_roles(apps, schema_editor):
    Department = apps.get_model("accounts", "Department")
    StaffRole = apps.get_model("accounts", "StaffRole")
    department, _ = Department.objects.get_or_create(name="Recommerce", defaults={"slug": "recommerce", "description": "Private device lifecycle operations"})
    for code, name, modules in ROLES:
        StaffRole.objects.update_or_create(code=code, defaults={"name": name, "department": department, "portal_role": "hq", "module_permissions": modules, "active": True})


class Migration(migrations.Migration):
    dependencies = [("recommerce", "0001_initial"), ("accounts", "0008_staffdocument_issued_at_staffdocument_signed_at_and_more")]
    operations = [migrations.RunPython(seed_roles, migrations.RunPython.noop)]
