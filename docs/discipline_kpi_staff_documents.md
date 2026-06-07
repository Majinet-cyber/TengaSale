# TengaSale Discipline, KPI, Payout and Staff Documents

This release adds evidence-first discipline governance, role KPI templates, payout approval support, staff document PDF generation, and expanded staff compensation/profile fields.

## Seed Data

Run:

```bash
python tengasale/manage.py seed_staff_system
```

The command seeds:

- 10 departments, including Collections, Customer Support, and Merchant Administration.
- A1-D2 ranks with default base salary, volt rate, rank multiplier, monthly ceiling, and board-controlled flags.
- Founder/executive and operational staff roles with module permissions.
- Discipline penalty and bonus event types.
- Role KPI templates for Underwriters, Merchant Administrators, Collections, and CEO/Strategy.
- Volts action types and the default 25th-to-24th compensation cycle.

## Governance Rules

- Users cannot approve their own Volts, discipline score, KPI result, dispute, or payout impact.
- CEO/super admin can view all governance modules, but self-approval is still blocked.
- Department heads review discipline inside their department.
- Finance/Risk or an authorized executive approves payout impact.
- Staff can view their own discipline, KPIs, Volts, documents, and disputes.
- Compensation visibility is restricted to the person, CEO/super admin, Finance/Risk, or payout-authorized users.

## Staff Documents

Staff documents are generated from live database fields and recorded in `StaffDocument`.

Supported PDFs:

- Employment Agreement
- Compensation Annexure A
- KPI & Volts Annexure B
- Monthly Volts Statement
- Monthly Performance Review
- Discipline Score Report
- Promotion/Rank Review
- Warning Letter
- Termination Letter

If `xhtml2pdf` is installed, branded HTML is rendered to PDF. If it is unavailable, the app falls back to a minimal built-in PDF renderer so document generation still works.
