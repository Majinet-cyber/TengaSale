from io import BytesIO

from django.template.loader import render_to_string
from django.utils import timezone

from .models import DisciplineScorePeriod, KPITemplate, StaffDocument
from .services import audit_sensitive_action, monthly_volts_summary


DOCUMENT_TITLES = dict(StaffDocument.DOC_CHOICES)


def document_number(user, document_type):
    stamp = timezone.now().strftime("%Y%m%d%H%M%S")
    return f"TS-{document_type.upper().replace('_', '-')}-{user.id}-{stamp}"


def staff_document_context(user, document_type, prepared_by=None, period=None):
    try:
        profile = user.profile
    except Exception:
        profile = None
    month = period or timezone.localdate().replace(day=1)
    discipline = DisciplineScorePeriod.objects.filter(user=user, month=month).first()
    if not discipline:
        discipline = DisciplineScorePeriod.objects.filter(user=user).order_by("-month").first()
    kpis = KPITemplate.objects.filter(active=True)
    if profile:
        kpis = kpis.filter(role=profile.staff_role) | kpis.filter(department=profile.department, role__isnull=True)
    volts = monthly_volts_summary(user)
    return {
        "company_name": "TengaSale",
        "document_title": DOCUMENT_TITLES.get(document_type, "Staff Document"),
        "document_type": document_type,
        "document_number": document_number(user, document_type),
        "version": "1.0",
        "generated_at": timezone.now(),
        "prepared_by": prepared_by,
        "user": user,
        "profile": profile,
        "rank": getattr(profile, "rank", None),
        "department": getattr(profile, "department", None),
        "staff_role": getattr(profile, "staff_role", None),
        "equity": getattr(profile, "founder_equity_record", None),
        "volts": volts,
        "discipline": discipline,
        "kpis": kpis.distinct().order_by("name")[:30],
        "month": month,
    }


def generate_staff_document_pdf(user, document_type, prepared_by=None, request=None, period=None):
    context = staff_document_context(user, document_type, prepared_by=prepared_by, period=period)
    html = render_to_string("accounts/staff_document_pdf.html", context)
    try:
        from xhtml2pdf import pisa
        output = BytesIO()
        result = pisa.CreatePDF(html, dest=output, encoding="utf-8")
        if result.err:
            raise RuntimeError("Staff document PDF could not be generated.")
        pdf_bytes = output.getvalue()
    except ModuleNotFoundError:
        pdf_bytes = simple_text_pdf(context)
    record = StaffDocument.objects.create(
        user=user,
        document_type=document_type,
        document_number=context["document_number"],
        version=context["version"],
        prepared_by=prepared_by if getattr(prepared_by, "is_authenticated", False) else None,
        metadata={
            "title": context["document_title"],
            "month": context["month"].isoformat() if context["month"] else "",
        },
    )
    audit_sensitive_action(
        prepared_by,
        "staff_pdf_generated",
        record,
        detail={"document_type": document_type, "target_user": user.username},
        request=request,
    )
    return pdf_bytes, record


def simple_text_pdf(context):
    lines = [
        "TengaSale",
        context["document_title"],
        f"Document No: {context['document_number']}",
        f"Version: {context['version']}",
        f"Generated: {context['generated_at']:%Y-%m-%d %H:%M}",
        "",
        f"Employee: {context['profile'].display_name if context['profile'] else context['user'].username}",
        f"Role: {getattr(context['staff_role'], 'name', '-')}",
        f"Department: {getattr(context['department'], 'name', '-')}",
        f"Rank: {getattr(context['rank'], 'code', '-')}",
        "",
        "Compensation",
        f"Base salary MWK: {context['volts']['base_salary']}",
        f"Approved volts: {context['volts']['approved_volts']}",
        f"Estimated volts pay MWK: {context['volts']['estimated_pay']}",
        f"Final monthly pay MWK: {context['volts']['final_monthly_pay']}",
        "",
        "Discipline",
    ]
    discipline = context.get("discipline")
    if discipline:
        lines.extend([
            f"Month: {discipline.month:%Y-%m}",
            f"Final score: {discipline.final_score} - {discipline.band_label}",
            f"Status: {discipline.get_status_display()}",
            f"Response: {discipline.user_response_text or 'No response recorded.'}",
        ])
    else:
        lines.append("No discipline score recorded.")
    lines.extend(["", "Signature Blocks", "Employee: __________________", "Company: __________________", "Witness/Reviewer: __________________"])
    escaped_lines = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    text_ops = ["BT", "/F1 11 Tf", "50 790 Td"]
    for index, line in enumerate(escaped_lines):
        if index:
            text_ops.append("0 -15 Td")
        text_ops.append(f"({line[:105]}) Tj")
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(pdf.tell())
        pdf.write(f"{number} 0 obj\n".encode("ascii"))
        pdf.write(obj)
        pdf.write(b"\nendobj\n")
    xref = pdf.tell()
    pdf.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        pdf.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.write(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode("ascii"))
    return pdf.getvalue()
