"""
Repayment confidence scoring service for TengaSale.

Rule-based signal — NOT machine learning.
Combines application-level factors to estimate repayment likelihood.

Label as:
  "Repayment confidence estimate (rule-based signal)"
  "This recommendation supports review. Final approval remains with the underwriter."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from django.utils import timezone


@dataclass
class RepaymentSignal:
    score: int               # 0–100
    risk_band: str           # "low" | "medium" | "high"
    recommendation: str      # "Approve" | "Review" | "Decline caution"
    reasons: list = field(default_factory=list)
    flags: list = field(default_factory=list)

    @property
    def badge_class(self) -> str:
        return {
            "low": "status-badge--approved",
            "medium": "status-badge--review",
            "high": "status-badge--rejected",
        }.get(self.risk_band, "status-badge--pending")

    @property
    def badge_label(self) -> str:
        return {
            "Approve": "Strong candidate",
            "Review": "Needs review",
            "Decline caution": "High caution",
        }.get(self.recommendation, self.recommendation)

    @property
    def score_color(self) -> str:
        if self.score >= 65:
            return "#16a34a"
        if self.score >= 40:
            return "#d97706"
        return "#dc2626"


def calculate_repayment_confidence(application) -> RepaymentSignal:
    """
    Calculate a rule-based repayment confidence score (0–100).

    Scoring breakdown:
      KYC completeness            up to 20 pts
      Deposit percentage          up to 20 pts
      Guarantor / next-of-kin     up to 15 pts
      Income / employment         up to 15 pts
      Application completeness    up to 10 pts
      Prior contract history      up to 20 pts (bonus or penalty)
    """
    score = 0
    reasons = []
    flags = []

    # ── 1. KYC completeness (up to 20 pts) ────────────────────────────────
    kyc_fields = [
        application.customer_name,
        application.national_id,
        application.customer_phone,
        application.customer_face_image,
        application.id_front_image,
        application.id_back_image,
    ]
    kyc_filled = sum(1 for f in kyc_fields if f)
    kyc_score = round((kyc_filled / len(kyc_fields)) * 20)
    score += kyc_score
    if kyc_score >= 18:
        reasons.append("Complete KYC documentation submitted.")
    elif kyc_score >= 12:
        reasons.append("Partial KYC — some documents present.")
        flags.append("Incomplete KYC")
    else:
        flags.append("Insufficient KYC documentation")

    # ── 2. Deposit percentage (up to 20 pts) ──────────────────────────────
    try:
        deposit_pct = float(application.selected_deposit_percent or 0)
    except (TypeError, ValueError):
        deposit_pct = 0

    if deposit_pct >= 30:
        score += 20
        reasons.append(f"High deposit ({deposit_pct:.0f}%) reduces financing risk.")
    elif deposit_pct >= 20:
        score += 14
        reasons.append(f"Standard deposit ({deposit_pct:.0f}%).")
    elif deposit_pct >= 13:
        score += 8
        flags.append(f"Minimum deposit only ({deposit_pct:.0f}%)")
    else:
        flags.append("Below-minimum deposit")

    # ── 3. Guarantor / next-of-kin (up to 15 pts) ─────────────────────────
    kin1_ok = bool(
        application.next_of_kin_1_name
        and application.next_of_kin_1_phone
        and application.next_of_kin_1_relationship
    )
    kin2_ok = bool(
        application.next_of_kin_2_name
        and application.next_of_kin_2_phone
        and application.next_of_kin_2_relationship
    )
    if kin1_ok and kin2_ok:
        score += 15
        reasons.append("Two complete next-of-kin references provided.")
    elif kin1_ok:
        score += 8
        reasons.append("One next-of-kin reference provided.")
        flags.append("Only one guarantor reference")
    else:
        flags.append("No guarantor references")

    # ── 4. Income / employment signals (up to 15 pts) ─────────────────────
    income_pts = 0
    try:
        monthly_income = float(application.exact_monthly_income or application.monthly_income or 0)
    except (TypeError, ValueError):
        monthly_income = 0

    if monthly_income > 0:
        income_pts += 5
        reasons.append(f"Monthly income declared: MWK {monthly_income:,.0f}.")
    if application.occupation:
        income_pts += 5
    if application.proof_of_income_type:
        income_pts += 5
        reasons.append("Proof of income type recorded.")

    score += min(income_pts, 15)
    if income_pts == 0:
        flags.append("No income or employment information")

    # ── 5. Application completeness (up to 10 pts) ────────────────────────
    completeness_fields = [
        application.district,
        application.traditional_authority,
        application.precise_location,
        application.occupation,
        application.gender,
        application.date_of_birth,
        application.agreed_to_terms,
    ]
    completeness_filled = sum(1 for f in completeness_fields if f)
    completeness_pts = round((completeness_filled / len(completeness_fields)) * 10)
    score += completeness_pts
    if completeness_pts < 5:
        flags.append("Application profile incomplete")

    # ── 6. Prior contract history (bonus/penalty up to 20) ────────────────
    try:
        from portal.models import PaymentContract
        prior_qs = (
            PaymentContract.objects.filter(customer_phone=application.customer_phone)
            if application.customer_phone else PaymentContract.objects.none()
        )
        total_prior = prior_qs.count()
        completed_prior = prior_qs.filter(status="completed").count()
        overdue_prior = prior_qs.filter(status="overdue").count()
        defaulted_prior = prior_qs.filter(status__in=["defaulted", "written_off"]).count()

        if total_prior > 0:
            if defaulted_prior > 0:
                score = max(0, score - 20)
                flags.append(f"Prior defaulted contract(s) ({defaulted_prior})")
            elif overdue_prior > 0:
                score = max(0, score - 10)
                flags.append(f"Prior overdue payment(s) ({overdue_prior})")
            elif completed_prior > 0:
                bonus = min(20, completed_prior * 8)
                score = min(100, score + bonus)
                reasons.append(f"Prior completed contract(s): {completed_prior} — positive history.")
    except Exception:
        pass

    score = max(0, min(100, score))

    if score >= 65:
        risk_band = "low"
        recommendation = "Approve"
    elif score >= 40:
        risk_band = "medium"
        recommendation = "Review"
    else:
        risk_band = "high"
        recommendation = "Decline caution"

    return RepaymentSignal(
        score=score,
        risk_band=risk_band,
        recommendation=recommendation,
        reasons=reasons,
        flags=flags,
    )


def save_confidence_to_application(application) -> RepaymentSignal:
    """Calculate and persist the confidence signal to the application record."""
    signal = calculate_repayment_confidence(application)
    reasons_text = " ".join(
        [f"[+] {r}" for r in signal.reasons]
        + [f"[!] {f}" for f in signal.flags]
    )
    application.repayment_confidence_score = signal.score
    application.risk_band = signal.risk_band
    application.recommendation_note = reasons_text
    application.confidence_calculated_at = timezone.now()
    application.save(update_fields=[
        "repayment_confidence_score",
        "risk_band",
        "recommendation_note",
        "confidence_calculated_at",
    ])
    return signal
