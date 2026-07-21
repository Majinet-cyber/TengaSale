import json
from datetime import datetime,timedelta
from django.core.management.base import BaseCommand,CommandError
from django.db.models import Q
from django.utils import timezone
from payments.models import AirtelCallbackLog

class Command(BaseCommand):
    help="Search the Airtel callback evidence ledger."
    def add_arguments(self,p):
        p.add_argument("--reference");p.add_argument("--provider-id");p.add_argument("--since");p.add_argument("--since-minutes",type=int);p.add_argument("--source-ip");p.add_argument("--request-id");p.add_argument("--fingerprint");p.add_argument("--state");p.add_argument("--unmatched",action="store_true");p.add_argument("--errors",action="store_true");p.add_argument("--latest",action="store_true");p.add_argument("--limit",type=int,default=100)
    def handle(self,*args,**o):
        qs=AirtelCallbackLog.objects.select_related("transaction").all()
        if o["reference"]: qs=qs.filter(Q(transaction__internal_reference=o["reference"])|Q(raw_body__icontains=o["reference"]))
        if o["provider_id"]: qs=qs.filter(Q(matched_identifier=o["provider_id"])|Q(extracted_airtel_money_id=o["provider_id"])|Q(provider_transaction_id=o["provider_id"]))
        if o["since"]:
            try: value=datetime.fromisoformat(o["since"])
            except ValueError as exc: raise CommandError("--since must be an ISO-8601 date/time.") from exc
            value=timezone.make_aware(value) if timezone.is_naive(value) else value; qs=qs.filter(created_at__gte=value)
        if o["since_minutes"] is not None:
            if o["since_minutes"]<0: raise CommandError("--since-minutes must be zero or greater.")
            qs=qs.filter(created_at__gte=timezone.now()-timedelta(minutes=o["since_minutes"]))
        if o["source_ip"]: qs=qs.filter(source_ip=o["source_ip"])
        if o["request_id"]: qs=qs.filter(Q(request_id=o["request_id"])|Q(provider_request_id=o["request_id"]))
        if o["fingerprint"]: qs=qs.filter(body_sha256__startswith=o["fingerprint"])
        if o["state"]: qs=qs.filter(processing_state=o["state"])
        if o["unmatched"]: qs=qs.filter(transaction__isnull=True)
        if o["errors"]: qs=qs.exclude(processing_error__in=[None,""])
        limit=1 if o["latest"] else max(0,o["limit"])
        for log in qs[:limit]:
            subscriber=f"*****{log.extracted_subscriber[-4:]}" if log.extracted_subscriber else ""
            self.stdout.write(json.dumps({"id":log.pk,"received_at":log.created_at.isoformat(),"reference":getattr(log.transaction,"internal_reference",None),"state":log.processing_state,"status":log.extracted_status,"airtel_money_id":log.extracted_airtel_money_id,"provider_transaction_id":log.provider_transaction_id,"subscriber":subscriber,"source_ip":log.source_ip,"request_id":log.request_id,"fingerprint":log.body_sha256,"response_status":log.response_status,"error":log.processing_error},default=str))
