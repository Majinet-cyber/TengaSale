import hashlib,json
from django.core.management.base import BaseCommand
from payments.airtel_client import AirtelConfig
from payments.airtel_services import AirtelCallbackService,_collect_reference_values,extract_airtel_amount,extract_airtel_status,extract_airtel_subscriber
from payments.models import AirtelCallbackLog,AirtelTransaction
from payments.callback_diagnostics import candidate_suggestions,extract_callback_source_ip
class Command(BaseCommand):
    help="Backfill evidence-derived callback diagnostics without fabricating missing facts."
    def add_arguments(self,p): p.add_argument("--apply",action="store_true");p.add_argument("--dry-run",action="store_true")
    def handle(self,*args,**o):
        changed=0
        environment=AirtelConfig.from_settings().environment
        for log in AirtelCallbackLog.objects.all().iterator():
            updates={}; raw=(log.raw_body or "").encode()
            if not log.body_sha256: updates["body_sha256"]=hashlib.sha256(raw).hexdigest()
            payload=log.parsed_body if isinstance(log.parsed_body,dict) else None
            headers={str(k).lower():str(v) for k,v in (log.received_headers or {}).items()}
            if not log.user_agent and headers.get("user-agent"): updates["user_agent"]=headers["user-agent"][:1000]
            proven_source_ip=extract_callback_source_ip(headers)
            if proven_source_ip and str(log.source_ip or "") != proven_source_ip: updates["source_ip"]=proven_source_ip
            if payload:
                values=sorted(_collect_reference_values(payload))
                if not log.candidate_identifiers: updates["candidate_identifiers"]=values
                if not log.extracted_status: updates["extracted_status"]=extract_airtel_status(payload)
                if log.extracted_amount is None: updates["extracted_amount"]=extract_airtel_amount(payload)
                if not log.transaction_id and not log.candidate_suggestions:
                    subscriber=extract_airtel_subscriber(payload); amount=extract_airtel_amount(payload)
                    pool=AirtelTransaction.objects.filter(environment=environment,status__in=[AirtelTransaction.STATUS_INITIATED,AirtelTransaction.STATUS_PENDING])
                    if subscriber: pool=pool.filter(customer_msisdn__endswith=subscriber)
                    suggestions=candidate_suggestions(transactions=pool.order_by("-created_at")[:20],subscriber=subscriber,amount=amount,received_at=log.created_at,environment=environment)
                    if suggestions: updates["candidate_suggestions"]=suggestions
                if not log.transaction_id:
                    tx,match=AirtelCallbackService().find_transaction_match(payload)
                    if tx: updates.update(transaction=tx,matched_identifier=match["value"],matched_field=match["field"])
            if not log.processing_state or log.processing_state=="RECEIVED":
                updates["processing_state"]="PROCESSED" if log.processed else "UNMATCHED" if not log.transaction_id else "RECEIVED"
            if updates:
                changed+=1;self.stdout.write(f"{log.pk}: {', '.join(sorted(updates))}")
                if o["apply"]:
                    AirtelCallbackLog.objects.filter(pk=log.pk).update(**updates)
        self.stdout.write(f"Summary: records_with_changes={changed}, applied={bool(o['apply'])}")
