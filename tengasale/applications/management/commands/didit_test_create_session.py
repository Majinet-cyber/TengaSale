"""
Test Didit session creation for a financing application (no secrets printed).

Usage:
    python manage.py didit_test_create_session --application-id 32
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from applications.models import FinancingApplication
from integrations.didit import (
    DiditAPIError,
    DiditConfigurationError,
    build_didit_session_payload,
    create_didit_session,
    sanitize_didit_session_payload_for_log,
    validate_didit_session_prerequisites,
)


class Command(BaseCommand):
    help = "Create a Didit verification session for an application and print safe diagnostics."

    def add_arguments(self, parser):
        parser.add_argument(
            "--application-id",
            type=int,
            required=True,
            help="FinancingApplication primary key",
        )

    def handle(self, *args, **options):
        app_id = options["application_id"]
        try:
            application = FinancingApplication.objects.get(pk=app_id)
        except FinancingApplication.DoesNotExist as exc:
            raise CommandError(f"Application {app_id} not found.") from exc

        self.stdout.write(f"Application {application.pk}: {application.customer_name or '(no name)'}")

        try:
            validate_didit_session_prerequisites(application)
        except DiditConfigurationError as exc:
            raise CommandError(str(exc)) from exc

        payload = build_didit_session_payload(application)
        sanitized = sanitize_didit_session_payload_for_log(payload)
        self.stdout.write("Sanitized payload:")
        self.stdout.write(json.dumps(sanitized, indent=2, default=str))
        forbidden = (
            "expected_details",
            "contact_details",
            "vendor_business_id",
        )
        included_forbidden = [key for key in forbidden if key in payload]
        if included_forbidden:
            raise CommandError(f"Payload must be minimal; found forbidden keys: {included_forbidden}")
        self.stdout.write("expected_details included: False (minimal payload enforced)")

        try:
            response = create_didit_session(application)
        except DiditConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        except DiditAPIError as exc:
            self.stdout.write(self.style.ERROR(f"Didit API failed (status={exc.status_code})"))
            self.stdout.write(json.dumps(exc.payload or {}, indent=2, default=str))
            if exc.response_text and not exc.payload:
                self.stdout.write(exc.response_text[:2000])
            if exc.request_payload_keys:
                self.stdout.write(f"Request payload keys: {exc.request_payload_keys}")
            raise CommandError(exc.user_message) from exc

        session_id = response.get("session_id") or response.get("id")
        url = response.get("url") or response.get("verification_url")
        self.stdout.write(self.style.SUCCESS("Didit session created."))
        self.stdout.write(f"session_id: {session_id}")
        self.stdout.write(f"url: {url}")
