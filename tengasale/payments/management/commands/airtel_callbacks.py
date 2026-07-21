import json
from datetime import datetime
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from payments.models import AirtelCallbackLog

class Command(BaseCommand):
    help="Search the Airtel callback evidence ledger."
    def add_arguments(self,p):
        p.add_argument("--reference");p.add_argument("--provider-id");p.add_argument("--since");p.add_argument("--source-ip");p.add_argument("--request-id");p.add_argument("--unmatched",action="store_true");p.add_argument("--errors",action="store_true");p.add_argument("--limit",type=int,default=100)
    def handle(self,*args,**o):
        qs=AirtelCallbackLog.objects.select_related("transaction").all()
        if o["reference"]: qs=qs.filter(Q(transaction__internal_reference=o["reference"])|Q(raw_body__icontains=o["reference"]))
        if o["provider_id"]: qs=qs.filter(Q(matched_identifier=o["provider_id"])|Q(raw_body__icontains=o["provider_id"]))
        if o["since"]:
            value=datetime.fromisoformat(o["since"]); value=timezone.make_aware(value) if timezone.is_naive(value) else value; qs=qs.filter(created_at__gte=value)
        if o["source_ip"]: qs=qs.filter(source_ip=o["source_ip"])
        if o["request_id"]: qs=qs.filter(Q(request_id=o["request_id"])|Q(provider_request_id=o["request_id"]))
        if o["unmatched"]: qs=qs.filter(transaction__isnull=True)
        if o["errors"]: qs=qs.exclude(processing_error__in=[None,""])
        for log in qs[:max(0,o["limit"])]:
            self.stdout.write(json.dumps({"id":log.pk,"received_at":log.created_at.isoformat(),"reference":getattr(log.transaction,"internal_reference",None),"state":log.processing_state,"status":log.extracted_status,"source_ip":log.source_ip,"request_id":log.request_id,"fingerprint":log.body_sha256,"response_status":log.response_status,"error":log.processing_error},default=str))
