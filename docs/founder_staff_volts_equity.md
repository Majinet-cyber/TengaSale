# Founder, Staff, Volts, and Equity System

TengaSale uses one login identity with role-based access. The portal a user sees is controlled by their portal role, staff role, department, rank, and module permissions.

## Core Principles

- Login gives identity.
- Role gives access.
- Rank gives earning power.
- Volts measure work.
- Penalties punish harmful behavior or negligence.
- Equity tracks ownership, but signed legal documents govern actual ownership.
- Audit logs protect the company.

## Main Records

- `Department`: operational department such as Technology & Product or Finance, Risk & Underwriting.
- `StaffRole`: functional role with backend module permissions.
- `Rank`: A1 through D2 earning multiplier and monthly ceiling.
- `UserProfile`: portal role, staff role, rank, status, supervisor, user type, founder flag, and company join date.
- `VoltsActionType`: repeatable work event and base volts value.
- `VoltsTransaction`: user work record with quality, discipline, result scores, final volts, estimated MWK value, approval state, and evidence.
- `PenaltyType` and `PenaltyTransaction`: deductions for negligence, inactivity, fraud, wrong IMEI, poor documentation, and similar issues.
- `FounderEquityRecord`: founder allocation, vesting dates, vested/unvested shares, status and flags.
- `CompanyShareStructure`: authorized shares, founder pool, investor reserve, and employee/advisor pool.

## Access Rules

- CEO / Strategy Lead and superusers can see all staff modules.
- Tech Lead can access technology, staff/user support, settings, and audit logs but not equity or finance payouts by default.
- Finance/Risk Lead can access underwriting, payments, collections, volts approval, analytics, and audit logs.
- Legal/Compliance Lead can access contracts, legal/recovery, collections, and audit logs.
- Sales/Marketing Lead can access sales/leads, merchants, and analytics.
- Merchant Operations Lead can access merchants, merchant admin, and sales/leads.
- Research/Data Lead can access analytics, applications, and merchant/customer performance data.
- Merchant Administrators can manage merchant onboarding records but cannot approve loans, edit equity, or change global finance rules.
- Underwriters continue to use the underwriter portal and assigned queues.
- Merchants continue to use the merchant portal.

All checks are enforced backend-side through account service helpers and protected views.

## Volts Formula

```text
final_volts = base_volts * rank_multiplier * quality_score * discipline_score * results_score
estimated_amount_mwk = final_volts * monetary_value_per_volt
```

Monthly payout calculation then applies the rank monthly ceiling. D1 and D2 ranks have board-controlled ceilings.

No user can approve their own volts transaction.

## Seeding

Run:

```text
python manage.py seed_staff_system
```

`seed_roles` also calls this command so existing setup flows create departments, ranks, roles, volts actions, penalty types, company share structure, and founder allocation records.
