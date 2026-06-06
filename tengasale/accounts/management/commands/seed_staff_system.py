from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import (
    CompanyShareStructure,
    Department,
    FounderEquityRecord,
    PenaltyType,
    Rank,
    StaffRole,
    UserProfile,
    VoltsActionType,
)
from accounts.services import (
    ALL_HQ_MODULES,
    MODULE_ANALYTICS,
    MODULE_APPLICATIONS,
    MODULE_AUDIT_LOGS,
    MODULE_COLLECTIONS,
    MODULE_CONTRACTS,
    MODULE_EQUITY,
    MODULE_LEGAL_RECOVERY,
    MODULE_MERCHANT_ADMIN,
    MODULE_MERCHANTS,
    MODULE_PAYMENTS,
    MODULE_SALES_LEADS,
    MODULE_SETTINGS,
    MODULE_STAFF_ROLES,
    MODULE_TECH,
    MODULE_UNDERWRITING,
    MODULE_VOLTS,
)


DEPARTMENTS = [
    "Executive & Strategy",
    "Technology & Product",
    "Finance, Risk & Underwriting",
    "Legal, Compliance & Recovery",
    "Sales, Marketing & Growth",
    "Merchant Operations & Customer Success",
    "Research, Innovation & Data Analytics",
]

RANKS = [
    ("A1", "Associate I", Decimal("1.00"), Decimal("750000")),
    ("A2", "Associate II", Decimal("1.15"), Decimal("1500000")),
    ("A3", "Associate III", Decimal("1.30"), Decimal("3000000")),
    ("B1", "Manager I", Decimal("1.50"), Decimal("4000000")),
    ("B2", "Manager II", Decimal("1.70"), Decimal("5000000")),
    ("B3", "Manager III", Decimal("1.90"), Decimal("6000000")),
    ("C1", "Department Lead I", Decimal("2.20"), Decimal("7000000")),
    ("C2", "Department Lead II", Decimal("2.50"), Decimal("8000000")),
    ("C3", "Department Lead III", Decimal("2.80"), Decimal("10000000")),
    ("D1", "Executive I", Decimal("3.20"), None),
    ("D2", "Executive II", Decimal("4.00"), None),
]

ROLE_DATA = [
    ("CEO / Strategy Lead", "Executive & Strategy", StaffRole.PORTAL_HQ, ALL_HQ_MODULES),
    ("Technology & Product Lead", "Technology & Product", StaffRole.PORTAL_HQ, [MODULE_TECH, MODULE_STAFF_ROLES, MODULE_AUDIT_LOGS, MODULE_SETTINGS]),
    ("Finance, Risk & Administration Lead", "Finance, Risk & Underwriting", StaffRole.PORTAL_HQ, [MODULE_UNDERWRITING, MODULE_PAYMENTS, MODULE_COLLECTIONS, MODULE_VOLTS, MODULE_ANALYTICS, MODULE_AUDIT_LOGS]),
    ("Legal & Compliance Lead", "Legal, Compliance & Recovery", StaffRole.PORTAL_HQ, [MODULE_CONTRACTS, MODULE_LEGAL_RECOVERY, MODULE_COLLECTIONS, MODULE_AUDIT_LOGS]),
    ("Sales & Marketing Lead", "Sales, Marketing & Growth", StaffRole.PORTAL_HQ, [MODULE_SALES_LEADS, MODULE_MERCHANTS, MODULE_ANALYTICS]),
    ("Merchant Operations Lead", "Merchant Operations & Customer Success", StaffRole.PORTAL_HQ, [MODULE_MERCHANTS, MODULE_MERCHANT_ADMIN, MODULE_SALES_LEADS]),
    ("Research & Data Lead", "Research, Innovation & Data Analytics", StaffRole.PORTAL_HQ, [MODULE_ANALYTICS, MODULE_MERCHANTS, MODULE_APPLICATIONS]),
    ("Merchant Administrator", "Merchant Operations & Customer Success", StaffRole.PORTAL_MERCHANT_ADMIN, [MODULE_MERCHANT_ADMIN, MODULE_MERCHANTS]),
    ("Underwriter Lead", "Finance, Risk & Underwriting", StaffRole.PORTAL_UNDERWRITER, [MODULE_UNDERWRITING, MODULE_APPLICATIONS]),
    ("Underwriter", "Finance, Risk & Underwriting", StaffRole.PORTAL_UNDERWRITER, [MODULE_UNDERWRITING, MODULE_APPLICATIONS]),
    ("Collections Officer", "Legal, Compliance & Recovery", StaffRole.PORTAL_HQ, [MODULE_COLLECTIONS, MODULE_PAYMENTS]),
    ("Recovery Officer", "Legal, Compliance & Recovery", StaffRole.PORTAL_HQ, [MODULE_LEGAL_RECOVERY, MODULE_COLLECTIONS]),
    ("Customer Support Officer", "Merchant Operations & Customer Success", StaffRole.PORTAL_SUPPORT, [MODULE_MERCHANTS]),
    ("Field Verification Officer", "Merchant Operations & Customer Success", StaffRole.PORTAL_HQ, [MODULE_MERCHANTS, MODULE_MERCHANT_ADMIN]),
    ("QA Officer", "Technology & Product", StaffRole.PORTAL_HQ, [MODULE_TECH, MODULE_AUDIT_LOGS]),
    ("Merchant", "Merchant Operations & Customer Success", StaffRole.PORTAL_MERCHANT, []),
    ("Customer", "Merchant Operations & Customer Success", StaffRole.PORTAL_CUSTOMER, []),
]

VOLTS_ACTIONS = [
    ("Merchant account created correctly", "Merchant Operations & Customer Success", 50),
    ("Merchant documents verified", "Merchant Operations & Customer Success", 80),
    ("Merchant trained and activated", "Merchant Operations & Customer Success", 120),
    ("Inactive merchant reactivated", "Merchant Operations & Customer Success", 150),
    ("Fraudulent merchant flagged", "Merchant Operations & Customer Success", 300),
    ("Weekly merchant report completed", "Merchant Operations & Customer Success", 100),
    ("Customer application reviewed", "Finance, Risk & Underwriting", 40),
    ("Clean approval", "Finance, Risk & Underwriting", 100),
    ("Send-back with clear reason", "Finance, Risk & Underwriting", 30),
    ("Fraud prevented", "Finance, Risk & Underwriting", 300),
    ("Bad customer rejected correctly", "Finance, Risk & Underwriting", 150),
    ("Contract performs for 30 days", "Finance, Risk & Underwriting", 150),
    ("Contract performs for 90 days", "Finance, Risk & Underwriting", 300),
    ("Customer reminded before due date", "Legal, Compliance & Recovery", 20),
    ("Payment recovered", "Legal, Compliance & Recovery", 100),
    ("Overdue customer restored to active", "Legal, Compliance & Recovery", 200),
    ("Device recovered lawfully", "Legal, Compliance & Recovery", 400),
    ("Bug fixed", "Technology & Product", 100),
    ("Major feature shipped", "Technology & Product", 500),
    ("Integration completed", "Technology & Product", 800),
    ("Critical downtime prevented", "Technology & Product", 500),
    ("Security vulnerability fixed", "Technology & Product", 700),
    ("Supplier credit secured", "Executive & Strategy", 1000),
    ("Investor meeting advanced", "Executive & Strategy", 500),
    ("Funding secured", "Executive & Strategy", 5000),
    ("Strategic partner signed", "Executive & Strategy", 2000),
    ("Major risk removed", "Executive & Strategy", 1500),
]

PENALTIES = [
    ("Ignored assigned queue", 100),
    ("Missing KYC fields", 80),
    ("Wrong customer phone number", 120),
    ("Wrong IMEI", 200),
    ("Fake guarantor accepted", 300),
    ("Poor documentation", 100),
    ("Unexplained inactivity", 150),
    ("Early default caused by negligence", 400),
    ("Fraud or collusion", 1000),
]

FOUNDER_ALLOCATIONS = [
    ("CEO / Strategy / Founder Vision", "CEO / Strategy Lead", 16800),
    ("Technology & Product Lead", "Technology & Product Lead", 12600),
    ("Finance, Risk & Administration Lead", "Finance, Risk & Administration Lead", 9800),
    ("Legal, Compliance & Corporate Affairs Lead", "Legal & Compliance Lead", 9100),
    ("Sales, Marketing & Brand Lead", "Sales & Marketing Lead", 7700),
    ("Merchant Operations & Customer Success Lead", "Merchant Operations Lead", 7000),
    ("Research, Innovation & Data Analytics Lead", "Research & Data Lead", 7000),
]


class Command(BaseCommand):
    help = "Seed founder/staff roles, ranks, volts actions, penalties, and equity records."

    def handle(self, *args, **options):
        User = get_user_model()
        groups = [
            "Merchant",
            "Merchant Administrator",
            "Underwriter",
            "Tech Support",
            "HQ",
            "Customer",
            "Collections Officer",
            "Recovery Officer",
            "Customer Support Officer",
            "Field Verification Officer",
            "QA Officer",
        ]
        for group_name in groups:
            Group.objects.get_or_create(name=group_name)

        departments = {}
        for name in DEPARTMENTS:
            departments[name], _ = Department.objects.update_or_create(
                slug=slugify(name),
                defaults={"name": name, "active": True},
            )

        ranks = {}
        for code, title, multiplier, ceiling in RANKS:
            ranks[code], _ = Rank.objects.update_or_create(
                code=code,
                defaults={
                    "title": title,
                    "multiplier": multiplier,
                    "monthly_ceiling_mwk": ceiling,
                    "description": f"{code} rank with {multiplier}x volts earning power.",
                    "active": True,
                },
            )

        staff_roles = {}
        for name, department_name, portal_role, modules in ROLE_DATA:
            staff_roles[name], _ = StaffRole.objects.update_or_create(
                code=slugify(name),
                defaults={
                    "name": name,
                    "department": departments.get(department_name),
                    "portal_role": portal_role,
                    "module_permissions": modules,
                    "active": True,
                },
            )

        for name, department_name, volts in VOLTS_ACTIONS:
            VoltsActionType.objects.update_or_create(
                name=name,
                defaults={
                    "department": departments.get(department_name),
                    "base_volts": volts,
                    "requires_approval": True,
                    "active": True,
                },
            )

        for name, volts in PENALTIES:
            PenaltyType.objects.update_or_create(
                name=name,
                defaults={"default_volts_deducted": volts, "active": True},
            )

        CompanyShareStructure.objects.update_or_create(
            pk=1,
            defaults={
                "total_authorized_shares": 100000,
                "founder_pool": 70000,
                "investor_reserve": 20000,
                "employee_advisor_pool": 10000,
            },
        )

        start_date = timezone.localdate()
        cliff = start_date + timedelta(days=365)
        for role_title, staff_role_name, shares in FOUNDER_ALLOCATIONS:
            username = f"founder_{slugify(staff_role_name).replace('-', '_')}"
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": f"{username}@tengasale.local",
                    "first_name": role_title.split()[0],
                    "last_name": "Founder",
                    "is_active": False,
                },
            )
            if created:
                user.set_unusable_password()
                user.save(update_fields=["password"])
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = UserProfile.ROLE_HQ
            profile.user_type = UserProfile.USER_TYPE_FOUNDER
            profile.status = UserProfile.STATUS_INACTIVE
            profile.is_founder = True
            profile.department = staff_roles[staff_role_name].department
            profile.staff_role = staff_roles[staff_role_name]
            profile.rank = ranks["D1"] if staff_role_name == "CEO / Strategy Lead" else ranks["C2"]
            profile.date_joined_company = profile.date_joined_company or start_date
            profile.save()
            FounderEquityRecord.objects.update_or_create(
                founder_user=user,
                defaults={
                    "role_title": role_title,
                    "allocated_shares": shares,
                    "vested_shares": 0,
                    "vesting_start_date": start_date,
                    "cliff_date": cliff,
                    "vesting_years": 4,
                    "vesting_status": FounderEquityRecord.STATUS_ACTIVE,
                },
            )

        self.stdout.write(self.style.SUCCESS(
            "Seeded founder/staff departments, roles, ranks, volts, penalties, and equity records."
        ))
