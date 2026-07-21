from dataclasses import replace
from django.core.management.base import BaseCommand,CommandError
from payments.airtel_client import AirtelConfig
from payments.airtel_services import AirtelCallbackService,extract_airtel_amount,extract_airtel_status
from django.utils import timezone
from payments.models import AirtelCallbackLog,AirtelCallbackProcessingAttempt,AirtelTransaction,PaymentAuditLog
class Command(BaseCommand):
    help="Dry-run or explicitly reprocess a retained Airtel callback."
    def add_arguments(self,p):
        p.add_argument("callback_id",type=int);p.add_argument("--dry-run",action="store_true");p.add_argument("--apply",action="store_true");p.add_argument("--reason");p.add_argument("--confirm")
    def handle(self,*args,**o):
        try: log=AirtelCallbackLog.objects.select_related("transaction").get(pk=o["callback_id"])
        except AirtelCallbackLog.DoesNotExist as exc: raise CommandError("Callback not found.") from exc
        if not isinstance(log.parsed_body,dict): raise CommandError("Callback has no valid JSON payload.")
        service=AirtelCallbackService();tx,match=service.find_transaction_match(log.parsed_body);status=extract_airtel_status(log.parsed_body);amount=extract_airtel_amount(log.parsed_body)
        self.stdout.write(f"callback={log.pk} match={match} status={status} amount={amount} current={getattr(tx,'status',None)}")
        if not o["apply"]: return
        if o["confirm"]!="REPROCESS" or not o["reason"]: raise CommandError("Apply requires --confirm REPROCESS and a non-empty --reason.")
        if not tx: raise CommandError("Refusing ambiguous or unmatched callback.")
        if status in {AirtelTransaction.STATUS_UNKNOWN,AirtelTransaction.STATUS_PENDING}: raise CommandError("Refusing non-final or unknown callback status.")
        if amount is not None and amount!=tx.amount: raise CommandError("Refusing callback amount mismatch.")
        config=AirtelConfig.from_settings()
        if config.is_production and not log.signature_valid: raise CommandError("Refusing unverified production callback.")
        before={"status":tx.status,"posted":tx.repayment_posted}
        attempt=AirtelCallbackProcessingAttempt.objects.create(callback=log,attempt_number=log.processing_attempts.count()+1,reason=o["reason"],initiated_by="management_command")
        replay=AirtelCallbackService(config=replace(config,callback_auth_enabled=False))
        new_log=AirtelCallbackLog.objects.create(replay_of=log,received_headers=log.received_headers,raw_body=log.raw_body,request_path=log.request_path,query_string=log.query_string,request_method=log.request_method,content_type=log.content_type,body_size=log.body_size,source_ip=log.source_ip,forwarded_for=log.forwarded_for,real_ip=log.real_ip,user_agent=log.user_agent,request_id=log.request_id,provider_request_id=log.provider_request_id,signature_present=log.signature_present,authentication_mode="AUTHORIZED_REPROCESS",body_sha256=log.body_sha256,processing_state="RECEIVED")
        try:
            new_log,_,code=replay.handle_callback(log.raw_body.encode("utf-8"),{"X-TengaSale-Reprocess":"true"},evidence_log=new_log)
            attempt.state=new_log.processing_state;attempt.response_status=code;attempt.details={"replay_callback_id":new_log.pk};attempt.completed_at=timezone.now();attempt.save()
        except Exception as exc:
            attempt.state="PROCESSING_FAILED";attempt.error_class=exc.__class__.__name__;attempt.error_message=str(exc)[:1000];attempt.completed_at=timezone.now();attempt.save();raise
        tx.refresh_from_db();after={"status":tx.status,"posted":tx.repayment_posted}
        PaymentAuditLog.objects.create(action="airtel_callback_reprocessed",transaction_ref=tx.internal_reference[:40],notes=o["reason"],metadata={"source_callback_id":log.pk,"replay_callback_id":new_log.pk,"before":before,"after":after,"response_status":code})
        self.stdout.write(f"before={before} after={after} replay_callback={new_log.pk} response_status={code}")
