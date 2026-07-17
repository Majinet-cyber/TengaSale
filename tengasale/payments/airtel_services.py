from pathlib import Path
import py_compile

source_path = Path("/mnt/data/Pasted text(56).txt")
output_path = Path("/mnt/data/airtel_services_fixed.py")

text = source_path.read_text(encoding="utf-8")

old = '''        airtel_tx = AirtelTransaction.objects.select_for_update().select_related("contract", "payment_transaction").get(
            pk=airtel_tx.pk
        )
'''

new = '''        # Lock only the AirtelTransaction row. The related contract and
        # payment_transaction fields are nullable, so joining them in the
        # SELECT ... FOR UPDATE query causes PostgreSQL to reject the query.
        airtel_tx = AirtelTransaction.objects.select_for_update().get(pk=airtel_tx.pk)
'''

if old not in text:
    raise RuntimeError("Expected apply_success query was not found; no file was written.")

fixed = text.replace(old, new, 1)
output_path.write_text(fixed, encoding="utf-8")

# Validate Python syntax.
py_compile.compile(str(output_path), doraise=True)

print(f"Created {output_path.name} with the PostgreSQL FOR UPDATE fix.")
