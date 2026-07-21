import hashlib,json
import ipaddress
from django.core.management.base import BaseCommand
from payments.airtel_services import AirtelCallbackService,_collect_reference_values,extract_airtel_amount,extract_airtel_status
from payments.models import AirtelCallbackLog,AirtelTransaction
class Command(BaseCommand):
    help="Backfill evidence-derived callback diagnostics without fabricating missing facts."
    def add_arguments(self,p): p.add_argument("--apply",action="store_true");p.add_argument("--dry-run",action="store_true")
    def handle(self,*args,**o):
        changed=0
        for log in AirtelCallbackLog.objects.all().iterator():
            updates={}; raw=(log.raw_body or "").encode()
            if not log.body_sha256: updates["body_sha256"]=hashlib.sha256(raw).hexdigest()
            payload=log.parsed_body if isinstance(log.parsed_body,dict) else None
            headers={str(k).lower():str(v) for k,v in (log.received_headers or {}).items()}
            if not log.user_agent and headers.get("user-agent"): updates["user_agent"]=headers["user-agent"][:1000]
            if not log.source_ip:
                candidate=(headers.get("x-forwarded-for","").split(",")[0].strip() or headers.get("x-real-ip","").strip())
                try:
                    if candidate: updates["source_ip"]=str(ipaddress.ip_address(candidate))
                except ValueError: pass
            if payload:
                values=sorted(_collect_reference_values(payload))
                if not log.candidate_identifiers: updates["candidate_identifiers"]=values
                if not log.extracted_status: updates["extracted_status"]=extract_airtel_status(payload)
                if log.extracted_amount is None: updates["extracted_amount"]=extract_airtel_amount(payload)
                if not log.transaction_id:
                    tx,match=AirtelCallbackService().find_transaction_match(payload)
                    if tx: updates.update(transaction=tx,matched_identifier=match["value"],matched_field=match["field"])
            if not log.processing_state or log.processing_state=="RECEIVED":
                updates["processing_state"]="PROCESSED" if log.processed else "UNMATCHED" if not log.transaction_id else "RECEIVED"
            if updates:
                changed+=1;self.stdout.write(f"{log.pk}: {', '.join(sorted(updates))}")
                if o["apply"]:
                    for key,value in updates.items(): setattr(log,key,value)
                    log.save(update_fields=list(updates))
        self.stdout.write(f"Summary: records_with_changes={changed}, applied={bool(o['apply'])}")
