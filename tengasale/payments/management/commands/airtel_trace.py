import json
from django.core.management.base import BaseCommand, CommandError
from payments.models import AirtelTransaction

def sanitize(value):
    sensitive={"authorization","cookie","pin","client_secret","private_key","secret","token","x-api-key"}
    if isinstance(value,dict):
        result={}
        for key,item in value.items():
            lower=str(key).lower()
            if lower in sensitive or any(word in lower for word in ("authorization","private_key","client_secret")): result[key]="***"
            elif lower in {"msisdn","phone","phone_number"}: result[key]=f"***{str(item)[-4:]}" if item else ""
            else: result[key]=sanitize(item)
        return result
    if isinstance(value,list): return [sanitize(item) for item in value]
    return value

def build_trace(tx):
    callbacks=[{"callback_id":log.pk,"received_at":log.created_at,"source_ip":log.source_ip,"request_id":log.request_id,"fingerprint":log.body_sha256,"provider_status":log.extracted_status,"candidates":log.candidate_identifiers,"matched_identifier":log.matched_identifier,"matched_field":log.matched_field,"processing_state":log.processing_state,"response_status":log.response_status,"response_body":log.response_body,"error":log.processing_error} for log in tx.callback_logs.order_by("created_at")]
    portal=tx.payment_transaction;contract=tx.contract;warnings=[]
    if not callbacks: warnings.append("No callback request matching this transaction was recorded by the TengaSale application. This does not prove Airtel made no delivery attempt; a request could fail before reaching Django.")
    if tx.reconciliation_required: warnings.append("Reconciliation is required; do not pay again.")
    masked=f"{tx.customer_msisdn[:6]}***{tx.customer_msisdn[-4:]}" if tx.customer_msisdn else "***"
    trace={"internal_reference":tx.internal_reference,"contract":getattr(contract,"contract_number",None),"amount":str(tx.amount),"currency":tx.currency,"masked_msisdn":masked,"airtel_payload_msisdn":f"*****{tx.customer_msisdn[-4:]}","initiation_payload":tx.raw_request,"initiation_response":tx.raw_response,"initiation_at":tx.created_at,"initiation_accepted_at":tx.initiation_accepted_at,"provider_identifiers":{"provider_reference":tx.provider_reference,"airtel_money_id":tx.airtel_money_id,"airtel_transaction_id":tx.airtel_transaction_id,"airtel_reference_id":tx.airtel_reference_id,"confirmed":tx.provider_id_confirmed,"source":tx.provider_identifier_source},"callbacks":callbacks,"enquiry":{"attempts":tx.enquiry_attempt_count,"last_at":tx.last_enquiry_at,"reference":tx.last_enquiry_reference,"path":tx.last_enquiry_path,"status":tx.last_enquiry_status,"error":tx.last_enquiry_error,"response":tx.last_enquiry_response},"posting":{"repayment_posted":tx.repayment_posted,"processed_success_at":tx.processed_success_at,"portal_status":getattr(portal,"status",None),"balance_before":str(getattr(portal,"balance_before","") or ""),"balance_after":str(getattr(portal,"balance_after","") or "")},"warnings":warnings}
    return sanitize(trace)

class Command(BaseCommand):
    help="Show a complete secret-safe Airtel transaction trace."
    def add_arguments(self,p): p.add_argument("reference")
    def handle(self,*args,**o):
        try: tx=AirtelTransaction.objects.select_related("contract","payment_transaction").get(internal_reference=o["reference"])
        except AirtelTransaction.DoesNotExist as exc: raise CommandError("Airtel transaction not found.") from exc
        self.stdout.write(json.dumps(build_trace(tx),indent=2,default=str))
