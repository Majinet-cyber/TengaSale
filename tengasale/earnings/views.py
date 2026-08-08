from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.decorators import merchant_required, underwriter_required
from accounts.utils import is_merchant, is_underwriter
from applications.models import FinancingApplication
from commissions.models import Commission
from contracts.models import Contract
from core.formatting import format_mwk
from portal.models import PaymentTransaction
from rewards.models import SpinReward, SpinWallet
from rewards.services import NoSpinsAvailable, SpinDisabled, perform_spin

from .models import Wallet, WalletTransaction


COMPLETED_STATUSES = ["approved", "completed"]


def _sum_decimal(queryset, field="amount"):
    return queryset.aggregate(total=Sum(field))["total"] or Decimal("0.00")


def _merchant_wallet_summary(user, wallet):
    commissions = Commission.objects.filter(user=user, role=Commission.ROLE_MERCHANT)
    unpaid_commissions = commissions.filter(status__in=[Commission.STATUS_PENDING, Commission.STATUS_APPROVED])
    valid_commissions = commissions.exclude(status=Commission.STATUS_CANCELLED)

    spin_rewards = SpinReward.objects.filter(user=user)
    try:
        from payments.models import SpinRewardPayout

        paid_spin_reward_ids = SpinRewardPayout.objects.filter(
            user=user,
            status=SpinRewardPayout.STATUS_PAID,
        ).values_list("spin_reward_id", flat=True)
        pending_spin_payouts = SpinRewardPayout.objects.filter(
            user=user,
            status__in=[SpinRewardPayout.STATUS_PENDING, SpinRewardPayout.STATUS_PROCESSING],
        )
        pending_spin_reward_ids = pending_spin_payouts.values_list("spin_reward_id", flat=True)
        unpaid_spin_rewards = spin_rewards.exclude(id__in=paid_spin_reward_ids).exclude(id__in=pending_spin_reward_ids)
        spin_pending_payout = _sum_decimal(pending_spin_payouts, "amount")
        spin_paid_out = _sum_decimal(
            SpinRewardPayout.objects.filter(user=user, status=SpinRewardPayout.STATUS_PAID),
            "amount",
        )
    except Exception:
        unpaid_spin_rewards = spin_rewards
        spin_pending_payout = Decimal("0.00")
        spin_paid_out = Decimal("0.00")

    pending_payout = getattr(wallet, "pending_payout", Decimal("0.00")) or Decimal("0.00")
    paid_out = (wallet.total_paid or Decimal("0.00")) + spin_paid_out
    wallet_transactions_available = _sum_decimal(
        wallet.transactions.filter(
            Q(transaction_type__in=["manual_credit", "bonus", "adjustment"], amount__gt=0)
            | Q(transaction_type__in=["payout", "wallet_payout", "payout_debit"], amount__lt=0)
        )
    )

    available = (
        _sum_decimal(unpaid_commissions)
        + _sum_decimal(unpaid_spin_rewards)
        + wallet_transactions_available
        - pending_payout
    )

    return {
        "wallet_balance": max(Decimal("0.00"), available),
        "total_earned": _sum_decimal(valid_commissions) + _sum_decimal(spin_rewards),
        "spin_rewards": _sum_decimal(unpaid_spin_rewards),
        "pending_payout": pending_payout + spin_pending_payout,
        "paid_out": paid_out,
    }


def _spin_access_allowed(user):
    return is_merchant(user) or is_underwriter(user)


@merchant_required
def earnings_home(request):
    wallet, created = Wallet.objects.get_or_create(user=request.user)
    wallet_summary = _merchant_wallet_summary(request.user, wallet)
    transactions = wallet.transactions.all()[:20]
    commissions = Commission.objects.filter(user=request.user).select_related("application", "application__contract")
    pending_commissions = commissions.filter(status=Commission.STATUS_PENDING)
    paid_commissions = commissions.filter(status=Commission.STATUS_PAID)
    spin_wallet, _ = SpinWallet.objects.get_or_create(user=request.user)
    latest_rewards = SpinReward.objects.filter(user=request.user)[:5]
    contract_numbers = [tx.contract_number for tx in transactions if tx.contract_number]
    contracts_by_number = {
        contract.contract_number: contract
        for contract in Contract.objects.filter(contract_number__in=contract_numbers, merchant=request.user)
    }
    transaction_rows = []

    tx_type_labels = {
        "commission": "Payment Commission",
        "manual_credit": "Manual Credit",
        "payout": "Wallet Payout",
        "wallet_payout": "Wallet Payout",
        "tax": "WHT Deduction",
        "wht_deduction": "WHT Deduction",
        "arrears_deduction": "Arrears Deduction",
        "bonus": "Manual Credit",
        "spin_reward": "Spin Reward",
    }

    for commission in commissions[:20]:
        contract = getattr(commission.application, "contract", None)
        amount = commission.amount
        if amount == 0:
            continue
        transaction_rows.append(
            {
                "created_at": commission.created_at,
                "type_label": "Payment Commission",
                "contract_number": contract.contract_number if contract else "",
                "contract": contract,
                "amount": amount,
                "status": commission.get_status_display(),
            }
        )

    for reward in latest_rewards:
        contract = getattr(reward.application, "contract", None) if reward.application_id else None
        transaction_rows.append(
            {
                "created_at": reward.spin_date,
                "type_label": "Spin Reward",
                "contract_number": contract.contract_number if contract else "",
                "contract": contract,
                "amount": reward.amount,
                "status": reward.get_reward_tier_display(),
            }
        )

    for tx in transactions:
        if tx.amount == 0:
            continue
        transaction_rows.append(
            {
                "created_at": tx.created_at,
                "type_label": tx_type_labels.get(tx.transaction_type, tx.get_transaction_type_display()),
                "contract_number": tx.contract_number,
                "contract": contracts_by_number.get(tx.contract_number),
                "amount": tx.amount,
                "status": "",
            }
        )

    transaction_rows.sort(key=lambda row: row["created_at"], reverse=True)
    payout_rows = [row for row in transaction_rows if row["type_label"] == "Wallet Payout"]
    active_tab = request.GET.get("tab", "overview")
    if active_tab == "earnings":
        active_tab = "overview"
    if active_tab not in {"overview", "transactions", "payouts", "rewards"}:
        active_tab = "overview"
    earnings_tabs = [
        {"key": key, "label": label, "href": f"{reverse('earnings_home')}?tab={key}"}
        for key, label in (("overview", "Overview"), ("transactions", "Transactions"), ("payouts", "Payouts"), ("rewards", "Rewards"))
    ]

    return render(request, "earnings/home.html", {
        "wallet": wallet,
        "wallet_summary": wallet_summary,
        "transactions": transactions,
        "transaction_rows": transaction_rows[:30],
        "payout_rows": payout_rows,
        "active_tab": active_tab,
        "earnings_tabs": earnings_tabs,
        "commissions": commissions[:20],
        "pending_commission_total": pending_commissions.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"),
        "paid_commission_total": paid_commissions.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"),
        "total_commission_earned": commissions.exclude(status=Commission.STATUS_CANCELLED).aggregate(total=Sum("amount"))[
            "total"
        ]
        or Decimal("0.00"),
        "spin_wallet": spin_wallet,
        "latest_rewards": latest_rewards,
    })


@merchant_required
def payments_home(request):
    query = request.GET.get("q", "").strip()
    rows_per_page = int(request.GET.get("rows", "10") or 10)
    if rows_per_page not in [5, 10, 25]:
        rows_per_page = 10

    contracts = Contract.objects.select_related("application").filter(merchant=request.user).order_by("-created_at")
    portal_payments = PaymentTransaction.objects.select_related(
        "payment_contract",
        "payment_contract__source_application",
    ).filter(
        payment_contract__source_application__created_by=request.user,
    ).order_by("-created_at")
    commissions = Commission.objects.select_related("application", "application__contract").filter(user=request.user).order_by(
        "-created_at"
    )

    if query:
        from django.db.models import Q

        contracts = contracts.filter(
            Q(contract_number__icontains=query)
            | Q(customer_name__icontains=query)
            | Q(customer_phone__icontains=query)
        )
        portal_payments = portal_payments.filter(
            Q(internal_reference__icontains=query)
            | Q(provider_reference__icontains=query)
            | Q(provider__icontains=query)
            | Q(network__icontains=query)
            | Q(status__icontains=query)
            | Q(payment_contract__contract_number__icontains=query)
            | Q(amount__icontains=query)
        )
        commissions = commissions.filter(
            Q(application__application_number__icontains=query)
            | Q(application__contract__contract_number__icontains=query)
            | Q(status__icontains=query)
            | Q(amount__icontains=query)
        )

    device_sales = [
        {
            "date": tx.paid_at or tx.created_at,
            "status": tx.status,
            "status_label": tx.get_status_display(),
            "amount": tx.amount,
            "institution": tx.get_provider_display() if tx.provider else "TengaSale",
            "account": tx.phone or tx.payment_contract.customer_phone or "-",
            "reference": tx.provider_reference or tx.internal_reference,
            "contract_number": tx.payment_contract.contract_number,
            "contract": getattr(tx.payment_contract.source_application, "contract", None),
        }
        for tx in portal_payments
    ]
    if not device_sales:
        device_sales = [
            {
                "date": contract.created_at,
                "status": "pending",
                "status_label": "Pending",
                "amount": contract.deposit_amount,
                "institution": "TengaSale",
                "account": contract.customer_phone or "-",
                "reference": contract.contract_number,
                "contract_number": contract.contract_number,
                "contract": contract,
            }
            for contract in contracts
        ]
    commission_rows = [
        {
            "date": commission.paid_at or commission.created_at,
            "status": "paid" if commission.status == Commission.STATUS_PAID else "pending",
            "status_label": commission.get_status_display(),
            "amount": commission.amount,
            "institution": "TengaSale",
            "account": request.user.get_full_name() or request.user.username,
            "reference": commission.application.application_number,
            "contract": getattr(commission.application, "contract", None),
            "contract_number": getattr(getattr(commission.application, "contract", None), "contract_number", commission.application.application_number),
            "application_number": commission.application.application_number,
        }
        for commission in commissions
    ]

    device_page = Paginator(device_sales, rows_per_page).get_page(request.GET.get("device_page"))
    commission_page = Paginator(commission_rows, rows_per_page).get_page(request.GET.get("commission_page"))

    return render(
        request,
        "earnings/payments.html",
        {
            "query": query,
            "rows_per_page": rows_per_page,
            "device_page": device_page,
            "commission_page": commission_page,
        },
    )


def leaderboard_rows():
    rows = list(
        FinancingApplication.objects.filter(status__in=COMPLETED_STATUSES)
        .values("created_by", "created_by__username", "created_by__first_name", "created_by__last_name")
        .annotate(sales_count=Count("id"))
        .order_by("-sales_count", "created_by__username")
    )
    earnings_by_user = {
        row["user"]: row["total"] or Decimal("0.00")
        for row in Commission.objects.filter(role=Commission.ROLE_MERCHANT)
        .exclude(status=Commission.STATUS_CANCELLED)
        .values("user")
        .annotate(total=Sum("amount"))
    }

    for index, row in enumerate(rows, start=1):
        full_name = f"{row['created_by__first_name']} {row['created_by__last_name']}".strip()
        row["rank"] = index
        row["merchant_name"] = full_name or row["created_by__username"]
        row["total_commission"] = earnings_by_user.get(row["created_by"], Decimal("0.00"))
    return rows


@merchant_required
def merchant_leaderboard(request):
    rows = leaderboard_rows()
    current_user_row = next((row for row in rows if row["created_by"] == request.user.id), None)
    return render(
        request,
        "earnings/leaderboard.html",
        {
            "leaders": rows[:10],
            "current_user_row": current_user_row,
            "period_label": "All-time",
        },
    )


def _render_spin_page(request):
    spin_wallet, _ = SpinWallet.objects.get_or_create(user=request.user)
    won_reward = None
    if request.method == "POST":
        try:
            won_reward = perform_spin(request.user)
            messages.success(request, f"You won {format_mwk(won_reward.amount)}.")
            spin_url = "sales_spin" if is_underwriter(request.user) else "spin_rewards"
            return redirect(f"{reverse(spin_url)}?won={won_reward.id}")
        except NoSpinsAvailable:
            messages.error(request, "No spins available.")
        except SpinDisabled:
            messages.error(request, "Spin rewards are currently disabled.")

    won_id = request.GET.get("won")
    if won_id:
        won_reward = SpinReward.objects.filter(id=won_id, user=request.user).first()
    spin_wallet.refresh_from_db()

    template = "sales/spin.html" if is_underwriter(request.user) else "earnings/spin.html"
    back_url = reverse("sales_wallet") if is_underwriter(request.user) else reverse("earnings_home")

    return render(
        request,
        template,
        {
            "spin_wallet": spin_wallet,
            "latest_rewards": SpinReward.objects.filter(user=request.user)[:10],
            "won_reward": won_reward,
            "back_url": back_url,
        },
    )


@merchant_required
def spin_rewards(request):
    if not _spin_access_allowed(request.user):
        messages.error(request, "Spin rewards are not available for your role.")
        return redirect("home")
    return _render_spin_page(request)


@underwriter_required
def sales_spin(request):
    return _render_spin_page(request)
