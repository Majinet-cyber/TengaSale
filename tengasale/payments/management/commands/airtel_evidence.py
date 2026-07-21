import json
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from payments.management.commands.airtel_trace import build_trace
from payments.models import AirtelTransaction
class Command(BaseCommand):
    help="Export a sanitized Airtel support evidence report."
    def add_arguments(self,p): p.add_argument("reference");p.add_argument("--output",required=True)
    def handle(self,*args,**o):
        try: tx=AirtelTransaction.objects.select_related("contract","payment_transaction").get(internal_reference=o["reference"])
        except AirtelTransaction.DoesNotExist as exc: raise CommandError("Airtel transaction not found.") from exc
        data=build_trace(tx); output=Path(o["output"])
        text=json.dumps(data,indent=2,default=str) if output.suffix.lower()==".json" else "\n".join(f"{k}: {json.dumps(v,default=str)}" for k,v in data.items())
        output.write_text(text,encoding="utf-8");self.stdout.write(str(output))
