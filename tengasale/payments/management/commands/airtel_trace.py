import json
from django.core.management.base import BaseCommand, CommandError
from payments.models import AirtelTransaction

HISTORICAL_INITIATION_NOTICE = "Original initiation response is unavailable because this historical record predates separated provider-response storage."

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

def _decimal_str(value):
    """Preserve an explicit zero balance ("0.00") instead of collapsing it to an
    empty string — a falsy-but-real Decimal("0") must never be confused with
    "no value recorded", which would misrepresent the trace during a UAT check."""
    return "" if value is None else str(value)

def build_trace(tx):
    callbacks=[]
    for log in tx.callback_logs.order_by("created_at"):
        callbacks.append({"callback_id":log.pk,"received_at":log.created_at,"source_ip":log.source_ip,"request_id":log.request_id,"fingerprint":log.body_sha256,"provider_status":log.extracted_status,"candidates":log.candidate_identifiers,"candidate_suggestions":log.candidate_suggestions,"matched_identifier":log.matched_identifier,"matched_field":log.matched_field,"processing_state":log.processing_state,"authentication_state":log.signature_validation_result,"signature_present":log.signature_present,"duplicate":log.duplicate,"response_status":log.response_status,"response_body":log.response_body,"error":log.processing_error})
    enquiry_history=[{"created_at":row.created_at,"reference":row.reference,"path":row.path,"http_status":row.http_status,"provider_status":row.provider_status,"response":row.response,"error_class":row.error_class,"error_message":row.error_message} for row in tx.enquiry_logs.all()]
    portal=tx.payment_transaction;contract=tx.contract;warnings=[]
    if not callbacks: warnings.append("No callback request matching this transaction was recorded by the TengaSale application. This does not prove Airtel made no delivery attempt; a request could fail before reaching Django.")
    if tx.reconciliation_required: warnings.append("Reconciliation is required; do not pay again.")
    if tx.potential_overpayment: warnings.append("Potential overpayment/manual review: a success arrived after another attempt in the same retry chain already posted.")
    initiation_response=tx.initiation_response
    if initiation_response is None:
        initiation_response={"unavailable":True,"notice":HISTORICAL_INITIATION_NOTICE}
        warnings.append(HISTORICAL_INITIATION_NOTICE)
    tx_id_classification="confirmed Airtel provider identifier" if tx.provider_id_confirmed else "unconfirmed / echoed merchant identifier"
    masked=f"{tx.customer_msisdn[:6]}***{tx.customer_msisdn[-4:]}" if tx.customer_msisdn else "***"
    # Authentication state: the tri-state outcome of the *most recent* callback's
    # signature verification (VERIFIED / NOT_CHECKED / INVALID / MISSING). NOT_CHECKED
    # (auth disabled, e.g. UAT) must never be reported or treated as VERIFIED.
    latest_callback = callbacks[-1] if callbacks else None
    authentication_state = latest_callback["authentication_state"] if latest_callback else "NO_CALLBACK_RECEIVED"
    deposit_access_days = getattr(contract, "deposit_access_days", None)
    access_policy = {
        "expected_deposit_access_days": 7,
        "contract_deposit_access_days": deposit_access_days,
        "matches_expected_days": deposit_access_days == 7,
        "access_expires_at": getattr(contract, "access_expires_at", None),
    }
    retry = {
        "retry_of": tx.retry_of.internal_reference if tx.retry_of_id else None,
        "superseded_by": tx.superseded_by.internal_reference if tx.superseded_by_id else None,
        "expired_at": tx.expired_at,
        "superseded_at": tx.superseded_at,
        "potential_overpayment": tx.potential_overpayment,
    }
    trace={"internal_reference":tx.internal_reference,"contract":getattr(contract,"contract_number",None),"amount":str(tx.amount),"currency":tx.currency,"masked_msisdn":masked,"airtel_payload_msisdn":f"*****{tx.customer_msisdn[-4:]}","initiation_payload":tx.raw_request,"initiation_response":initiation_response,"initiation_response_headers":initiation_response.get("sanitized_headers",{}) if isinstance(initiation_response,dict) else {},"provider_trace_id":initiation_response.get("provider_trace_id","") if isinstance(initiation_response,dict) else "","initiation_http_status":tx.initiation_http_status,"initiation_at":tx.created_at,"initiation_accepted_at":tx.initiation_accepted_at,"status":tx.status,"provider_identifiers":{"provider_reference":tx.provider_reference,"airtel_money_id":tx.airtel_money_id,"airtel_transaction_id":tx.airtel_transaction_id,"airtel_transaction_id_classification":tx_id_classification,"airtel_reference_id":tx.airtel_reference_id,"confirmed":tx.provider_id_confirmed,"source":tx.provider_identifier_source},"callbacks":callbacks,"authentication_state":authentication_state,"last_enquiry":{"attempts":tx.enquiry_attempt_count,"last_at":tx.last_enquiry_at,"reference":tx.last_enquiry_reference,"path":tx.last_enquiry_path,"status":tx.last_enquiry_status,"error":tx.last_enquiry_error,"http_status":tx.last_enquiry_http_status,"response":tx.last_enquiry_response},"enquiry_history":enquiry_history,"retry":retry,"access_policy":access_policy,"posting":{"repayment_posted":tx.repayment_posted,"processed_success_at":tx.processed_success_at,"portal_status":getattr(portal,"status",None),"balance_before":_decimal_str(getattr(portal,"balance_before",None)),"balance_after":_decimal_str(getattr(portal,"balance_after",None))},"warnings":warnings}
    return sanitize(trace)

class Command(BaseCommand):
    help="Show a complete secret-safe Airtel transaction trace."
    def add_arguments(self,p): p.add_argument("reference")
    def handle(self,*args,**o):
        try: tx=AirtelTransaction.objects.select_related("contract","payment_transaction").prefetch_related("enquiry_logs").get(internal_reference=o["reference"])
        except AirtelTransaction.DoesNotExist as exc: raise CommandError("Airtel transaction not found.") from exc
        self.stdout.write(json.dumps(build_trace(tx),indent=2,default=str))
