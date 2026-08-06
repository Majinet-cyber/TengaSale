from django.core.management.base import BaseCommand

from accounts.models import Department, StaffRole


ROLES = [
    ("recommerce_intake", "Recommerce Intake Officer", ["recommerce.intake"]),
    ("recommerce_assessor", "Device Assessor / Grader", ["recommerce.assessment"]),
    ("recommerce_technician", "Refurbishment Technician", ["recommerce.refurbishment"]),
    ("recommerce_qa", "Recommerce QA Officer", ["recommerce.qa"]),
    ("recommerce_inventory", "Recommerce Inventory / Sales Officer", ["recommerce.inventory"]),
    ("recommerce_supervisor", "Recommerce Supervisor", ["recommerce.supervision"]),
    ("recommerce_hq", "HQ Recommerce Admin", ["recommerce.hq"]),
]


class Command(BaseCommand):
    help = "Create the private recommerce staff roles without assigning users."

    def handle(self, *args, **options):
        department, _ = Department.objects.get_or_create(name="Recommerce", defaults={"slug": "recommerce", "description": "Private device lifecycle operations"})
        for code, name, modules in ROLES:
            StaffRole.objects.update_or_create(code=code, defaults={"name": name, "department": department, "portal_role": "hq", "module_permissions": modules, "active": True})
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(ROLES)} recommerce roles."))
