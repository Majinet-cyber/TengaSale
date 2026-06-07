from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import (
    CompanyShareStructure,
    CompensationCycle,
    Department,
    DisciplineEventType,
    FounderEquityRecord,
    KPITemplate,
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
    MODULE_DISCIPLINE,
    MODULE_EQUITY,
    MODULE_KPIS,
    MODULE_LEGAL_RECOVERY,
    MODULE_MERCHANT_ADMIN,
    MODULE_MERCHANTS,
    MODULE_PAYMENTS,
    MODULE_PAYOUTS,
    MODULE_SALES_LEADS,
    MODULE_SETTINGS,
    MODULE_STAFF_DOCUMENTS,
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
    "Collections & Recoveries",
    "Customer Support",
    "Merchant Administration",
]

RANKS = [
    ("A1", "Trainee", Decimal("90000"), Decimal("13.50"), Decimal("1.00"), Decimal("750000"), False),
    ("A2", "Rising Star", Decimal("126000"), Decimal("19.00"), Decimal("1.15"), Decimal("1500000"), False),
    ("A3", "Strong Junior", Decimal("150000"), Decimal("22.00"), Decimal("1.30"), Decimal("3000000"), False),
    ("B1", "Officer", Decimal("200000"), Decimal("25.00"), Decimal("1.50"), Decimal("4000000"), False),
    ("B2", "Senior Officer", Decimal("300000"), Decimal("30.00"), Decimal("1.70"), Decimal("5000000"), False),
    ("B3", "Lead Officer", Decimal("400000"), Decimal("35.00"), Decimal("1.90"), Decimal("6000000"), False),
    ("C1", "Manager", Decimal("500000"), Decimal("40.00"), Decimal("2.20"), Decimal("7000000"), False),
    ("C2", "Senior Manager", Decimal("650000"), Decimal("45.00"), Decimal("2.50"), Decimal("8000000"), False),
    ("C3", "Head of Department", Decimal("800000"), Decimal("50.00"), Decimal("2.80"), Decimal("10000000"), False),
    ("D1", "Executive", Decimal("1000000"), Decimal("60.00"), Decimal("3.20"), None, True),
    ("D2", "CEO / Managing Director", Decimal("0"), Decimal("0"), Decimal("4.00"), None, True),
]

ROLE_DATA = [
    ("CEO / Strategy Lead", "Executive & Strategy", StaffRole.PORTAL_HQ, ALL_HQ_MODULES),
    ("Technology & Product Lead", "Technology & Product", StaffRole.PORTAL_HQ, [MODULE_TECH, MODULE_STAFF_ROLES, MODULE_AUDIT_LOGS, MODULE_SETTINGS, MODULE_DISCIPLINE, MODULE_KPIS, MODULE_STAFF_DOCUMENTS]),
    ("Finance, Risk & Administration Lead", "Finance, Risk & Underwriting", StaffRole.PORTAL_HQ, [MODULE_UNDERWRITING, MODULE_PAYMENTS, MODULE_COLLECTIONS, MODULE_VOLTS, MODULE_ANALYTICS, MODULE_AUDIT_LOGS, MODULE_DISCIPLINE, MODULE_KPIS, MODULE_PAYOUTS, MODULE_STAFF_DOCUMENTS]),
    ("Legal & Compliance Lead", "Legal, Compliance & Recovery", StaffRole.PORTAL_HQ, [MODULE_CONTRACTS, MODULE_LEGAL_RECOVERY, MODULE_COLLECTIONS, MODULE_AUDIT_LOGS, MODULE_DISCIPLINE, MODULE_STAFF_DOCUMENTS]),
    ("Sales & Marketing Lead", "Sales, Marketing & Growth", StaffRole.PORTAL_HQ, [MODULE_SALES_LEADS, MODULE_MERCHANTS, MODULE_ANALYTICS, MODULE_DISCIPLINE, MODULE_KPIS]),
    ("Merchant Operations Lead", "Merchant Operations & Customer Success", StaffRole.PORTAL_HQ, [MODULE_MERCHANTS, MODULE_MERCHANT_ADMIN, MODULE_SALES_LEADS, MODULE_DISCIPLINE, MODULE_KPIS]),
    ("Research & Data Lead", "Research, Innovation & Data Analytics", StaffRole.PORTAL_HQ, [MODULE_ANALYTICS, MODULE_MERCHANTS, MODULE_APPLICATIONS, MODULE_KPIS]),
    ("Merchant Administrator", "Merchant Administration", StaffRole.PORTAL_MERCHANT_ADMIN, [MODULE_MERCHANT_ADMIN, MODULE_MERCHANTS, MODULE_KPIS]),
    ("Underwriter Lead", "Finance, Risk & Underwriting", StaffRole.PORTAL_UNDERWRITER, [MODULE_UNDERWRITING, MODULE_APPLICATIONS]),
    ("Underwriter", "Finance, Risk & Underwriting", StaffRole.PORTAL_UNDERWRITER, [MODULE_UNDERWRITING, MODULE_APPLICATIONS]),
    ("Collections Officer", "Collections & Recoveries", StaffRole.PORTAL_HQ, [MODULE_COLLECTIONS, MODULE_PAYMENTS, MODULE_KPIS]),
    ("Recovery Officer", "Collections & Recoveries", StaffRole.PORTAL_HQ, [MODULE_LEGAL_RECOVERY, MODULE_COLLECTIONS]),
    ("Customer Support Officer", "Customer Support", StaffRole.PORTAL_SUPPORT, [MODULE_MERCHANTS]),
    ("Field Verification Officer", "Merchant Operations & Customer Success", StaffRole.PORTAL_HQ, [MODULE_MERCHANTS, MODULE_MERCHANT_ADMIN]),
    ("QA Officer", "Technology & Product", StaffRole.PORTAL_HQ, [MODULE_TECH, MODULE_AUDIT_LOGS]),
    ("Merchant", "Merchant Operations & Customer Success", StaffRole.PORTAL_MERCHANT, []),
    ("Customer", "Customer Support", StaffRole.PORTAL_CUSTOMER, []),
]

VOLTS_ACTIONS = [
    ("Merchant account created correctly", "Merchant Operations & Customer Success", 50),
    ("Merchant documents verified", "Merchant Operations & Customer Success", 80),
    ("Merchant trained and activated", "Merchant Operations & Customer Success", 120),
    ("Inactive merchant reactivated", "Merchant Operations & Customer Success", 150),
    ("Fraudulent merchant flagged", "Merchant Operations & Customer Success", 300),
    ("Weekly merchant report completed", "Merchant Operations & Customer Success", 100),
    ("Merchant commission dispute resolved", "Merchant Administration", 100),
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
    ("Broken promise followed up", "Collections & Recoveries", 40),
    ("Collection reason captured correctly", "Collections & Recoveries", 25),
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
    ("Monthly investor report submitted", "Executive & Strategy", 300),
    ("Runway improved", "Executive & Strategy", 1000),
    ("Legal/compliance risk closed", "Executive & Strategy", 700),
]

DISCIPLINE_EVENT_TYPES = [
    ("Late application review", "late-application-review", "Assigned application review deadline breach.", 2, None, "low", False, True),
    ("Ignored assigned queue", "ignored-assigned-queue", "Assigned tasks untouched beyond SLA.", 5, None, "medium", False, True),
    ("No meaningful activity during working window", "no-meaningful-activity", "Login without completed assigned work.", 5, None, "medium", False, True),
    ("Missing KYC fields", "missing-kyc-fields", "Application QC failure for missing KYC.", 5, None, "medium", False, True),
    ("Wrong customer phone number", "wrong-customer-phone-number", "Customer phone corrected by QC/support.", 7, None, "high", False, True),
    ("Wrong IMEI", "wrong-imei", "IMEI corrected in contract/device workflow.", 15, None, "critical", False, True),
    ("Fake or unverified guarantor accepted", "fake-unverified-guarantor", "Unverified guarantor accepted.", 20, None, "critical", True, False),
    ("Poor documentation", "poor-documentation", "QC send-back for weak documentation.", 5, None, "medium", False, True),
    ("Broken follow-up", "broken-follow-up", "Due/overdue customer without contact log.", 5, None, "medium", False, True),
    ("Unexplained absence", "unexplained-absence", "No approved absence evidence.", 10, None, "high", True, False),
    ("Early default linked to negligence", "early-default-negligence", "Early default requiring review.", 15, None, "critical", True, False),
    ("Fraud or collusion", "fraud-or-collusion", "Locks payout pending investigation.", 100, None, "critical", True, False),
    ("Perfect attendance / availability", "perfect-attendance", "Monthly attendance bonus.", 0, 3, "low", False, False),
    ("Queue cleared within SLA for full month", "queue-cleared-sla", "Monthly SLA bonus.", 0, 5, "low", False, False),
    ("Zero QC errors", "zero-qc-errors", "Monthly quality bonus.", 0, 5, "low", False, False),
    ("High-risk fraud prevented", "high-risk-fraud-prevented", "Fraud prevention bonus.", 0, 5, "medium", True, False),
    ("Emergency support beyond role", "emergency-support-beyond-role", "Emergency support bonus.", 0, 3, "medium", True, False),
]

KPI_NAMES = {
    "Underwriter": [
        "applications reviewed", "average review time", "% applications reviewed under 5 minutes",
        "% applications reviewed under 25 minutes", "approval rate", "rejection rate", "send-back rate",
        "% contracts passed QC", "first payment missed rate", "1D40 rate", "fraud prevented",
        "backup/escalation rate", "customer education accuracy", "documentation error rate",
        "early default rate", "volts earned", "penalties",
    ],
    "Merchant Administrator": [
        "merchants onboarded", "active merchants last 30 days", "merchant sales last 30 days",
        "merchant conversion rate", "merchant 1D40", "missing documents", "merchant application error rate",
        "merchant reactivation count", "fraudulent merchants flagged", "merchant commission disputes resolved",
        "training completion rate", "merchant ranking",
    ],
    "Collections Officer": [
        "customers due today contacted", "customers reminded before due date", "overdue customers contacted",
        "recovery promises made", "promises kept", "promises broken", "payments recovered",
        "overdue customers restored to active", "locked devices", "devices recovered lawfully",
        "arrears reasons captured", "unreachable customers", "stolen/lost phone cases",
    ],
    "CEO / Strategy Lead": [
        "capital raised", "investor meetings advanced", "supplier credit secured", "strategic partnerships signed",
        "monthly revenue growth", "cash runway", "default rate", "collection rate", "product uptime",
        "legal/compliance readiness", "team performance", "investor reporting completed",
        "capital discipline score", "founder volts", "major risks removed",
    ],
}

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
        for code, title, base_salary, volt_rate, multiplier, ceiling, board_controlled in RANKS:
            ranks[code], _ = Rank.objects.update_or_create(
                code=code,
                defaults={
                    "title": title,
                    "base_salary_default_mwk": base_salary,
                    "volt_rate_default_mwk": volt_rate,
                    "multiplier": multiplier,
                    "rank_multiplier": multiplier,
                    "monthly_ceiling_mwk": ceiling,
                    "is_board_controlled": board_controlled,
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
                    "code": slugify(name),
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

        CompensationCycle.objects.update_or_create(
            name="Default monthly compensation cycle",
            defaults={"start_day": 25, "end_day": 24, "active": True},
        )

        severity_map = {
            "low": DisciplineEventType.SEVERITY_LOW,
            "medium": DisciplineEventType.SEVERITY_MEDIUM,
            "high": DisciplineEventType.SEVERITY_HIGH,
            "critical": DisciplineEventType.SEVERITY_CRITICAL,
        }
        for name, code, description, deduction, bonus, severity, evidence_required, auto_generated in DISCIPLINE_EVENT_TYPES:
            DisciplineEventType.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "description": description,
                    "deduction_percentage": Decimal(str(deduction)),
                    "bonus_percentage": Decimal(str(bonus)) if bonus is not None else None,
                    "severity": severity_map[severity],
                    "evidence_required": evidence_required,
                    "auto_generated": auto_generated,
                    "active": True,
                },
            )

        for role_name, kpi_names in KPI_NAMES.items():
            role = staff_roles.get(role_name)
            for index, kpi_name in enumerate(kpi_names, start=1):
                lower_is_better = any(term in kpi_name for term in ["time", "missed", "1D40", "error", "default", "penalties", "unreachable"])
                KPITemplate.objects.update_or_create(
                    role=role,
                    department=role.department if role else None,
                    name=kpi_name,
                    defaults={
                        "description": f"{role_name} KPI: {kpi_name}.",
                        "target_value": Decimal("100"),
                        "weight": Decimal("1.00"),
                        "lower_is_better": lower_is_better,
                        "data_source": "system",
                        "affects_quality_score": index % 3 == 0,
                        "affects_discipline_score": "penalties" in kpi_name or "attendance" in kpi_name,
                        "affects_results_score": True,
                        "active": True,
                    },
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
            profile.can_approve_volts = True
            profile.can_approve_discipline = True
            profile.can_approve_payouts = staff_role_name in {"CEO / Strategy Lead", "Finance, Risk & Administration Lead"}
            profile.can_override_scores = staff_role_name == "CEO / Strategy Lead"
            profile.date_joined_company = profile.date_joined_company or start_date
            profile.base_salary_mwk = profile.base_salary_mwk if profile.base_salary_mwk is not None else profile.rank.base_salary_default_mwk
            profile.volt_rate_mwk = profile.volt_rate_mwk if profile.volt_rate_mwk is not None else profile.rank.volt_rate_default_mwk
            profile.monthly_ceiling_mwk = profile.monthly_ceiling_mwk if profile.monthly_ceiling_mwk is not None else profile.rank.monthly_ceiling_mwk
            profile.save()
            equity, _ = FounderEquityRecord.objects.update_or_create(
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
            profile.founder_equity_record = equity
            profile.save(update_fields=["founder_equity_record"])

        self.stdout.write(self.style.SUCCESS(
            "Seeded founder/staff departments, roles, ranks, volts, penalties, and equity records."
        ))
