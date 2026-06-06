"""
Management command: seed_legal_documents

Seeds the TengaSale legal document templates into the database:
  - Master Terms and Conditions v1.0
  - Customer Contract Summary v1.0

Idempotent: existing records are updated, not duplicated.

Usage:
    python manage.py seed_legal_documents
    python manage.py seed_legal_documents --dry-run
"""

import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from contracts.models import LegalDocumentTemplate


def _safe_read_legal_file(path: Path) -> str:
    """
    Read a legal document source file safely.

    - .txt and .html files: read as UTF-8 text
    - .docx files: extract text using python-docx (never decoded as UTF-8 directly)
    - Other formats: raise ValueError

    Raises:
        CommandError: if the file cannot be read or decoded correctly
        ValueError: if the file format is unsupported
    """
    suffix = path.suffix.lower()
    if suffix in (".txt", ".html"):
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CommandError(
                f"Cannot decode {path.name} as UTF-8. "
                "Ensure the file is saved with UTF-8 encoding. "
                f"Detail: {exc}"
            ) from exc
    elif suffix == ".docx":
        try:
            from docx import Document  # python-docx
        except ImportError as exc:
            raise CommandError(
                "python-docx is required to load .docx legal templates. "
                "Run: pip install python-docx"
            ) from exc
        try:
            doc = Document(path)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as exc:
            raise CommandError(
                f"Failed to parse DOCX file {path.name}: {exc}"
            ) from exc
    else:
        raise ValueError(
            f"Unsupported legal template format: {suffix}. "
            "Only .txt, .html, and .docx files are supported."
        )

MASTER_TERMS_V1_HTML = """
<div class="legal-doc">
<h1>TENGASALE MASTER TERMS AND CONDITIONS</h1>
<h2>VERSION 1.0</h2>
<p><strong>Effective Date:</strong> 1 June 2026</p>
<p>
These Master Terms and Conditions govern all instalment purchase, financing, device management
services, payment obligations, and related transactions facilitated through the TengaSale Platform.
</p>

<h3>1. DEFINITIONS</h3>
<p><strong>TengaSale</strong> means TengaSale Limited, a company incorporated under the laws of Malawi.<br>
<strong>Customer</strong> means the person who has applied for and/or accepted an instalment financing
arrangement through TengaSale.<br>
<strong>Merchant</strong> means an authorised agent or retailer partnered with TengaSale.<br>
<strong>Device</strong> means the smartphone or other device financed under the Agreement.<br>
<strong>Agreement</strong> means these Master Terms together with the Customer Contract Summary.</p>

<h3>2. BINDING ACCEPTANCE</h3>
<p>The Customer is legally bound by electronic acceptance, signing, payment, taking possession of
the Device, or using any TengaSale service. Any one of these acts constitutes full acceptance
of these Master Terms and the Customer Contract Summary.</p>

<h3>3. MERCHANT AS AUTHORISED AGENT</h3>
<p>The Merchant acts as an authorised agent of TengaSale for the purpose of facilitating applications,
collecting documentation, explaining terms, and distributing Devices. Any representation made by the
Merchant within the scope of their agency binds TengaSale.</p>

<h3>4. APPROVAL DISCRETION</h3>
<p>TengaSale has sole and absolute discretion to approve, reject, request further information, or
impose conditions on any application. Approval of one application does not create a precedent or
obligation for future applications.</p>

<h3>5. CUSTOMER WARRANTIES</h3>
<p>The Customer warrants that all information and identification documents provided to TengaSale are
true, genuine, accurate, and complete. Providing false or misleading information is a material breach
and may result in immediate termination of the Agreement and recovery action.</p>

<h3>6. OWNERSHIP OF DEVICE</h3>
<p>The Device remains the sole and absolute property of TengaSale until all payment obligations,
fees, charges, and any outstanding amounts under the Agreement are fully satisfied. The Customer has
no right to sell, encumber, pledge, or transfer the Device before ownership passes.</p>

<h3>7. PAYMENT AND RECEIPT</h3>
<p>Payments are only deemed received by TengaSale upon full confirmation and reflection in TengaSale's
official records and payment systems. Risk of payment transmission lies with the Customer.</p>

<h3>8. PAYMENT ALLOCATION</h3>
<p>TengaSale may allocate payments in the following order at its discretion: (a) recovery and
enforcement costs; (b) overdue amounts and late fees; (c) charges and administrative fees;
(d) current instalment amounts; and (e) outstanding principal balance.</p>

<h3>9. EARLY SETTLEMENT</h3>
<p>Any early settlement discount offered by TengaSale is entirely discretionary and does not constitute
a contractual right. TengaSale may withdraw or modify any early settlement arrangement without prior notice.</p>

<h3>10. DEVICE MANAGEMENT TECHNOLOGY</h3>
<p>The Customer expressly consents to TengaSale installing and operating device management technology
on the Device. This technology may include remote monitoring, access restriction, and device
deactivation capabilities as described in the Agreement.</p>

<h3>11. ACCESS RESTRICTION</h3>
<p>TengaSale may restrict access to or deactivate the Device where: (a) payments are overdue;
(b) fraud is suspected; (c) the Device is being unlawfully transferred or used; or (d) a material
breach of the Agreement occurs. Access restriction does not waive any right to recover outstanding amounts.</p>

<h3>12. PROHIBITED DEALINGS</h3>
<p>The Customer must not, without prior written consent of TengaSale: sell, pledge, mortgage, lease,
rent, lend, or transfer the Device or any interest in it before ownership has legally passed to the Customer.</p>

<h3>13. LOSS, THEFT, OR DAMAGE</h3>
<p>The Customer must notify TengaSale in writing within 72 hours of becoming aware of loss, theft, or
material damage to the Device. Failure to notify does not suspend the Customer's payment obligations.
TengaSale does not provide replacement devices in the event of loss, theft, or damage.</p>

<h3>14. EVENTS OF DEFAULT</h3>
<p>The following are Events of Default: (a) failure to keep payments current after the paid-through date;
(b) providing false, misleading, or fraudulent information; (c) suspected fraud by the
Customer or any connected party; (d) unlawful disposal, transfer, or encumbrance of the Device;
(e) material breach of any term of the Agreement; or (f) reasonable grounds to believe the Customer
is unable or unwilling to meet their obligations.</p>

<h3>15. CONSEQUENCES OF DEFAULT</h3>
<p>Upon an Event of Default, TengaSale may without further notice: (a) issue a formal notice of default;
(b) restrict or deactivate the Device; (c) demand immediate payment of all outstanding amounts;
(d) commence recovery proceedings; and (e) exercise all lawful remedies available under the laws of Malawi.</p>

<h3>16. REPOSSESSION</h3>
<p>If the Customer remains in default for 30 or more consecutive days, TengaSale may repossess the
Device without further notice. TengaSale may engage authorised agents or legal representatives to
effect repossession. Reasonable repossession costs may be charged to the Customer's account.</p>

<h3>17. DISPOSAL OF REPOSSESSED DEVICE</h3>
<p>TengaSale may dispose of a repossessed Device by sale, auction, or otherwise at its discretion.
Proceeds of disposal will be applied to reduce the Customer's outstanding balance. Any remaining
deficit remains recoverable from the Customer.</p>

<h3>18. IDENTITY AND FRAUD SCREENING</h3>
<p>The Customer authorises TengaSale to conduct identity verification, fraud screening, reference
checks, affordability assessments, and any other lawful checks as TengaSale considers necessary,
at application and at any time during the Agreement.</p>

<h3>19. DATA PROCESSING AND PRIVACY</h3>
<p>The Customer consents to TengaSale processing their personal data for the purposes of: contract
administration, customer support, payment processing and reconciliation, fraud prevention, regulatory
compliance, and risk management. TengaSale may share data with authorised agents, service providers,
and regulators as required by law.</p>

<h3>20. ELECTRONIC RECORDS AS EVIDENCE</h3>
<p>Electronic records maintained by TengaSale are prima facie evidence of transactions, payments
received, notices issued, signatures obtained, and account activity. In the absence of manifest error,
TengaSale records prevail.</p>

<h3>21. ELECTRONIC CONTRACTING AND SIGNATURES</h3>
<p>Electronic signatures, OTP verification, digital acceptance, and any other form of electronic
acknowledgement are legally binding and constitute valid acceptance of the Agreement. The Customer
waives any right to challenge the validity of the Agreement on the basis of electronic acceptance.</p>

<h3>22. COMMUNICATIONS</h3>
<p>TengaSale may communicate with the Customer by WhatsApp, SMS, email, phone call, or any other
electronic means. The Customer consents to receiving all notices, statements, documents, and
communications by electronic means to the contact details provided.</p>

<h3>23. CHANGES TO TERMS</h3>
<p>TengaSale may update these Master Terms from time to time. Continued use of TengaSale services
or continued possession of the Device after notice of changes constitutes acceptance of the updated terms.</p>

<h3>24. LIMITATION OF LIABILITY</h3>
<p>TengaSale's liability to the Customer is limited to the amount of payments made by the Customer
under the Agreement. TengaSale is not liable for any indirect, consequential, or special loss or damage.</p>

<h3>25. INDEMNITY</h3>
<p>The Customer indemnifies TengaSale against all costs, losses, and expenses arising from the Customer's
breach of the Agreement, including reasonable legal and recovery costs.</p>

<h3>26. GOVERNING LAW</h3>
<p>This Agreement is governed by and construed in accordance with the laws of the Republic of Malawi.</p>

<h3>27. DISPUTE RESOLUTION</h3>
<p>Any dispute arising from this Agreement must first be submitted to TengaSale's internal complaints
and dispute resolution process. If a dispute remains unresolved after the internal process, either
party may refer the matter to the competent courts of Malawi.</p>

<h3>28. ENTIRE AGREEMENT</h3>
<p>These Master Terms together with the Customer Contract Summary constitute the entire agreement
between TengaSale and the Customer and supersede all prior representations, negotiations, and agreements.</p>

<h3>29. CUSTOMER ACCEPTANCE</h3>
<p>By signing, electronically accepting, paying, taking possession of the Device, or using any
TengaSale service, the Customer confirms that they have read, understood, and agree to be legally
bound by these Master Terms and Conditions Version 1.0 and the Customer Contract Summary.
The Customer further confirms consent to electronic contracting and to the installation and operation
of device management technology on the Device.</p>
</div>
"""

CONTRACT_SUMMARY_V1_HTML = """
<div class="legal-doc">
<h1>TENGASALE DIGITAL INSTALMENT SALE AGREEMENT</h1>
<h2>Customer Contract Summary — Version 1.0</h2>
<p><strong>Effective Date:</strong> 1 June 2026</p>
<p>
This Customer Contract Summary is governed by and forms part of the TengaSale Master Terms and
Conditions Version 1.0. By signing or electronically accepting this Contract Summary, the Customer
confirms acceptance of the Master Terms.
</p>

<h3>1. PARTIES</h3>
<p><strong>Seller / Financier:</strong> TengaSale Limited<br>
<strong>Customer:</strong> As identified in the application and contract.<br>
<strong>Authorised Merchant / Agent:</strong> As identified in the contract.</p>

<h3>2. TRANSACTION SUMMARY</h3>
<p>This Contract Summary sets out the key financial and commercial terms of the Device instalment
purchase. The full legal terms governing this transaction are contained in the TengaSale Master
Terms and Conditions Version 1.0, which the Customer has accepted.</p>

<h3>3. OWNERSHIP</h3>
<p>The Device remains the property of TengaSale until all payments and obligations are fully settled.
The Customer must not sell, pledge, or transfer the Device until ownership has legally passed.</p>

<h3>4. DEFAULT AND DEVICE RESTRICTIONS</h3>
<p>Failure to keep payments current after the paid-through date constitutes a default event. After 30 consecutive days
of default, TengaSale may repossess the Device. Device access may be restricted at any time during default.</p>

<h3>5. ELECTRONIC ACCEPTANCE</h3>
<p>Electronic acceptance, OTP verification, and digital signatures are legally binding under the
laws of Malawi and constitute full acceptance of this Contract Summary and the Master Terms.</p>

<h3>6. GOVERNING DOCUMENT</h3>
<p>This Contract Summary is governed by the TengaSale Master Terms and Conditions Version 1.0.
In the event of any inconsistency, the Master Terms prevail.</p>
</div>
"""


class Command(BaseCommand):
    help = "Seed TengaSale legal document templates (Master Terms v1.0, Contract Summary v1.0). Idempotent."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be seeded without making changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        documents_to_seed = [
            {
                "document_type": LegalDocumentTemplate.TYPE_MASTER_TERMS,
                "version": "1.0",
                "title": "TengaSale Master Terms and Conditions",
                "effective_from": datetime.date(2026, 6, 1),
                "is_active": True,
                "body_html": MASTER_TERMS_V1_HTML.strip(),
            },
            {
                "document_type": LegalDocumentTemplate.TYPE_CONTRACT_SUMMARY,
                "version": "1.0",
                "title": "TengaSale Customer Contract Summary",
                "effective_from": datetime.date(2026, 6, 1),
                "is_active": True,
                "body_html": CONTRACT_SUMMARY_V1_HTML.strip(),
            },
        ]

        for doc_data in documents_to_seed:
            doc_type = doc_data["document_type"]
            version = doc_data["version"]
            label = f"{doc_data['title']} v{version}"

            if dry_run:
                exists = LegalDocumentTemplate.objects.filter(
                    document_type=doc_type, version=version
                ).exists()
                status = "EXISTS (would update)" if exists else "NEW (would create)"
                self.stdout.write(f"[DRY RUN] {label}: {status}")
                continue

            obj, created = LegalDocumentTemplate.objects.update_or_create(
                document_type=doc_type,
                version=version,
                defaults={
                    "title": doc_data["title"],
                    "effective_from": doc_data["effective_from"],
                    "is_active": doc_data["is_active"],
                    "body_html": doc_data["body_html"],
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action}: {label} (checksum: {obj.checksum[:12]}...)"))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS("Legal documents seeded successfully."))
