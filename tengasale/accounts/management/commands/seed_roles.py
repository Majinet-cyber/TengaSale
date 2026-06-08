from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create TengaSale role groups — comprehensive role system."

    ROLE_GROUPS = [
        # ── Core operational roles ─────────────────────────────────────────
        "Merchant",
        "Merchant Administrator",
        "Underwriter",
        "Senior Underwriter",
        "Underwriting Supervisor",
        "Tech Support",
        "HQ",
        "Admin",
        "Super Admin",

        # ── Executive / Governance ─────────────────────────────────────────
        "CEO",
        "CFO",
        "CTO",
        "COO",
        "Head of Credit",
        "Head of Operations",
        "Head of Finance",
        "Head of Risk",
        "Compliance Officer",
        "Legal Officer",
        "HR Manager",
        "Merchant Success Manager",

        # ── Collections / Recovery ─────────────────────────────────────────
        "Collections Officer",
        "Recovery Officer",

        # ── Quality / Compliance ───────────────────────────────────────────
        "QA / Quality Control Officer",
        "Field Verification Officer",
        "Auditor",

        # ── Support / Operations ───────────────────────────────────────────
        "Support Agent",
        "Read-only Analyst",

        # ── System / Engineering ───────────────────────────────────────────
        "Developer / Engineering",
    ]

    def handle(self, *args, **options):
        created = 0
        existed = 0
        for group_name in self.ROLE_GROUPS:
            _, is_new = Group.objects.get_or_create(name=group_name)
            if is_new:
                created += 1
            else:
                existed += 1

        try:
            call_command("seed_staff_system")
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"seed_staff_system: {exc}"))

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(self.ROLE_GROUPS)} TengaSale roles "
            f"({created} new, {existed} already existed)."
        ))
