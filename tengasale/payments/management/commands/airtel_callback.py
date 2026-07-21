import json
from django.core.management.base import BaseCommand,CommandError
from payments.models import AirtelCallbackLog
class Command(BaseCommand):
    help="Inspect one Airtel callback evidence record."
    def add_arguments(self,p): p.add_argument("callback_id",type=int)
    def handle(self,*args,**o):
        try: log=AirtelCallbackLog.objects.select_related("transaction").get(pk=o["callback_id"])
        except AirtelCallbackLog.DoesNotExist as exc: raise CommandError("Callback not found.") from exc
        data={f.name:getattr(log,f.name) for f in log._meta.fields if f.name not in {"transaction"}}
        data["transaction"]=getattr(log.transaction,"internal_reference",None);data["received_headers"]=log.received_headers
        self.stdout.write(json.dumps(data,indent=2,default=str))
