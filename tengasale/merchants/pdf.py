"""
Generate a signed Merchant Participation Agreement PDF using xhtml2pdf.
"""
import logging
import os
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string

logger = logging.getLogger("tengasale.merchants.pdf")

COMPANY_NAME = getattr(settings, "TENGASALE_COMPANY_NAME", "Emajinet Limited t/a TengaSale")
COMPANY_ADDRESS = getattr(settings, "TENGASALE_COMPANY_ADDRESS", "Blantyre, Malawi")
COMPANY_EMAIL = getattr(settings, "TENGASALE_COMPANY_EMAIL", "info@tengasale.emajinet.africa")
COMPANY_PHONE = getattr(settings, "TENGASALE_COMPANY_PHONE", "+265 (0) XXX XXX XXX")
GOVERNING_LAW = getattr(settings, "TENGASALE_GOVERNING_LAW", "the laws of the Republic of Malawi")
MEDIA_URL = getattr(settings, "MEDIA_URL", "/media/")


def generate_merchant_agreement_pdf(agreement):
    """
    Render the agreement HTML and convert to PDF, saving to agreement.pdf_file.
    Returns True on success, raises on failure.
    """
    try:
        from xhtml2pdf import pisa
    except ImportError:
        logger.error("xhtml2pdf not installed — cannot generate merchant agreement PDF")
        raise

    context = {
        "agreement": agreement,
        "merchant": agreement.merchant,
        "company_name": COMPANY_NAME,
        "company_address": COMPANY_ADDRESS,
        "company_email": COMPANY_EMAIL,
        "company_phone": COMPANY_PHONE,
        "governing_law": GOVERNING_LAW,
        "MEDIA_URL": MEDIA_URL,
    }

    html_string = render_to_string("merchants/agreement_pdf.html", context)

    buffer = BytesIO()
    pisa_status = pisa.CreatePDF(
        html_string,
        dest=buffer,
        encoding="utf-8",
    )

    if pisa_status.err:
        logger.error(
            "xhtml2pdf error generating merchant agreement PDF for %s: %s",
            agreement.reference_number,
            pisa_status.err,
        )
        raise RuntimeError(f"PDF generation failed: {pisa_status.err}")

    pdf_content = buffer.getvalue()
    buffer.close()

    filename = f"merchant-agreement-{agreement.merchant.id}-{agreement.reference_number}.pdf"
    agreement.pdf_file.save(filename, ContentFile(pdf_content), save=True)
    logger.info("Generated merchant agreement PDF: %s", filename)
    return True
