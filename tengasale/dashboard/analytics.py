"""Small, database-backed chart datasets for HQ operational dashboards."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone


def _percent(value, maximum):
    if not maximum:
        return 0
    return round((float(value) / float(maximum)) * 100, 1)


def collections_trend(payment_queryset, *, days=7, today=None):
    """Return a gap-filled daily series for successful payments."""
    today = today or timezone.localdate()
    start = today - timedelta(days=days - 1)
    rows = (
        payment_queryset.filter(status="paid", paid_at__date__range=(start, today))
        .annotate(day=TruncDate("paid_at"))
        .values("day")
        .annotate(amount=Coalesce(Sum("amount"), Decimal("0")), count=Count("id"))
        .order_by("day")
    )
    by_day = {row["day"]: row for row in rows}
    series = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = by_day.get(day, {})
        series.append({
            "date": day.isoformat(),
            "label": day.strftime("%a"),
            "amount": row.get("amount", Decimal("0")),
            "count": row.get("count", 0),
        })
    maximum = max((item["amount"] for item in series), default=Decimal("0"))
    for item in series:
        item["height"] = _percent(item["amount"], maximum)
    return series


def payment_comparison_series(payment_queryset, *, days=7, today=None):
    """Return aligned current/previous successful-payment periods in two queries."""
    today = today or timezone.localdate()
    current_start = today - timedelta(days=days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)

    def totals(start, end):
        rows = (
            payment_queryset.filter(status="paid", paid_at__date__range=(start, end))
            .annotate(day=TruncDate("paid_at"))
            .values("day")
            .annotate(amount=Coalesce(Sum("amount"), Decimal("0")))
            .order_by("day")
        )
        return {row["day"]: row["amount"] for row in rows}

    current = totals(current_start, today)
    previous = totals(previous_start, previous_end)
    labels, current_values, previous_values = [], [], []
    for offset in range(days):
        current_day = current_start + timedelta(days=offset)
        previous_day = previous_start + timedelta(days=offset)
        labels.append(current_day.strftime("%d %b"))
        current_values.append(float(current.get(current_day, Decimal("0"))))
        previous_values.append(float(previous.get(previous_day, Decimal("0"))))
    return {
        "labels": labels,
        "current": current_values,
        "previous": previous_values,
        "trend": current_values,
        "current_label": f"{current_start:%d %b}–{today:%d %b}",
        "previous_label": f"{previous_start:%d %b}–{previous_end:%d %b}",
    }


def application_trend(application_queryset, *, days=14, today=None):
    """Return submitted and approved application counts, grouped by submission day."""
    today = today or timezone.localdate()
    start = today - timedelta(days=days - 1)
    rows = (
        application_queryset.filter(submitted_at__date__range=(start, today))
        .annotate(day=TruncDate("submitted_at"))
        .values("day")
        .annotate(
            applications=Count("id"),
            approvals=Count("id", filter=Q(status="approved")),
        )
        .order_by("day")
    )
    by_day = {row["day"]: row for row in rows}
    series = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = by_day.get(day, {})
        series.append({
            "date": day.isoformat(),
            "label": day.strftime("%d %b"),
            "applications": row.get("applications", 0),
            "approvals": row.get("approvals", 0),
        })
    maximum = max(
        (max(item["applications"], item["approvals"]) for item in series),
        default=0,
    )
    for item in series:
        item["application_height"] = _percent(item["applications"], maximum)
        item["approval_height"] = _percent(item["approvals"], maximum)
    return series


def grouped_breakdown(queryset, field, *, labels=None):
    """Return count and percentage rows for an entire queryset."""
    rows = list(queryset.values(field).annotate(count=Count("id")).order_by("-count", field))
    total = sum(row["count"] for row in rows)
    result = []
    for row in rows:
        value = row[field] or "other"
        result.append({
            "key": value,
            "label": (labels or {}).get(value, str(value).replace("_", " ").title()),
            "count": row["count"],
            "percent": _percent(row["count"], total),
        })
    return result
