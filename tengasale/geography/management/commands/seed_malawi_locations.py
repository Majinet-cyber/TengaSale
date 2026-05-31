"""
Management command: seed_malawi_locations
=========================================
Seeds Malawi regions, districts, and Traditional Authorities (TAs).

Usage:
  python manage.py seed_malawi_locations              # seed all data
  python manage.py seed_malawi_locations --dry-run    # preview without writing
  python manage.py seed_malawi_locations --clear      # clear then re-seed (CAREFUL!)

Idempotent: safe to run multiple times — uses get_or_create throughout.
Do NOT auto-run this in migrations. Run manually on production when ready.
"""

from django.core.management.base import BaseCommand
from geography.models import District, Region, TraditionalAuthority

# ---------------------------------------------------------------------------
# Malawi location data
# Region → {district: [list of TAs]}
# ---------------------------------------------------------------------------
MALAWI_DATA = {
    "Northern Region": {
        "Chitipa": [
            "Chikulamayembe", "Kameme", "Mwabulambya", "Nthalire", "Wenya",
        ],
        "Karonga": [
            "Kyungu", "Mwirang'ombe", "Mwenewenya", "Kilupula", "Mwakaboko",
        ],
        "Likoma": [
            "Likoma Island",
        ],
        "Mzimba": [
            "Mzimba Boma", "Mzukuzuku", "Mtwalo", "Kampingo Sibande",
            "Chindi", "Munthali", "Kacheche", "Mabulabo",
        ],
        "Mzuzu City": [
            "Mzuzu Urban",
        ],
        "Nkhata Bay": [
            "Timbiri", "Nkhata Bay Boma", "Fukamalaza", "Mlowe", "Usisya",
        ],
        "Rumphi": [
            "Rumphi Boma", "Katumbi", "Chirobwe", "Mwankhunikira", "Mkukula",
        ],
    },
    "Central Region": {
        "Dedza": [
            "Dedza Boma", "Chongoni", "Kachere", "Kasina", "Linthipe",
            "Mtakataka", "Njolomole", "Pemba",
        ],
        "Dowa": [
            "Dowa Boma", "Bowe", "Chakhaza", "Chiwere", "Kayembe", "Kayira",
            "Mponela", "Kayembe Maluwa",
        ],
        "Kasungu": [
            "Kasungu Boma", "Chilowamatambe", "Chulu", "Lukwa", "Mwansambo",
            "Njombwa", "Wimbe", "Lifidzi",
        ],
        "Lilongwe": [
            "Lilongwe City", "Chitukula", "Chimutu", "Chilobwe", "Kabudula",
            "Kalolo", "Kunenekude", "Liwonde", "Malembo", "Mazengera",
            "M'bwatalika", "Njewa", "Tsabango",
        ],
        "Mchinji": [
            "Mchinji Boma", "Dambe", "Kalolo", "Mkanda", "Mlonyeni",
            "Zulu", "Kapelula", "Chanje",
        ],
        "Nkhotakota": [
            "Nkhotakota Boma", "Dwambazi", "Kafuzira", "Malengachanzi",
            "Mazengera", "Mkandawire",
        ],
        "Ntcheu": [
            "Ntcheu Boma", "Chikweo", "Kanduku", "Kwataine", "Lizulu",
            "Njolomole", "Tsangano",
        ],
        "Ntchisi": [
            "Ntchisi Boma", "Chikho", "Kaluluma", "Kasakula", "Nthondo",
        ],
        "Salima": [
            "Salima Boma", "Chifunda", "Kalonga", "Kuluunda", "Lifuwu",
            "Pemba",
        ],
    },
    "Southern Region": {
        "Balaka": [
            "Balaka Boma", "Amidu", "Kalembo", "Nkaya", "Ntonda",
        ],
        "Blantyre": [
            "Blantyre City", "Chitera", "Kapeni", "Lunzu", "Machinjiri",
            "Makata", "Mwanza", "Nkukula", "Thiramula",
        ],
        "Chikwawa": [
            "Chikwawa Boma", "Chapananga", "Katunga", "Lundu", "Makhuwira",
            "Mbewe", "Ngabu", "Nsambe",
        ],
        "Chiradzulu": [
            "Chiradzulu Boma", "Kadewere", "Likhubula", "Manchwe",
            "Mpama", "Naisi",
        ],
        "Machinga": [
            "Machinga Boma", "Liwonde", "Chowe", "Kaponda", "Nankumba",
            "Mponda", "Nyambi",
        ],
        "Mangochi": [
            "Mangochi Boma", "Jalasi", "Katuli", "Makanjira", "Nankumba",
            "Ntonda", "Chimwala", "Bwananyambi",
        ],
        "Mulanje": [
            "Mulanje Boma", "Chikumbu", "Mabuka", "Nessa", "Ruo",
        ],
        "Mwanza": [
            "Mwanza Boma", "Kandeu", "Neno",
        ],
        "Neno": [
            "Neno Boma", "Dambe", "Mlauli", "Tambala",
        ],
        "Nsanje": [
            "Nsanje Boma", "Chimombo", "Kasisi", "Makhanga", "Mlolo",
            "Ngabu",
        ],
        "Phalombe": [
            "Phalombe Boma", "Jenala", "Mkhumba",
        ],
        "Thyolo": [
            "Thyolo Boma", "Bvumbwe", "Chimaliro", "Kunenekude",
            "Makwasa", "Nchima", "Nguludi",
        ],
        "Zomba": [
            "Zomba City", "Chiradzi", "Jali", "Kuntumanji", "Mlumbe",
            "Nantumbo", "Nsanama",
        ],
    },
}


class Command(BaseCommand):
    help = "Seed Malawi regions, districts, and Traditional Authorities"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be created without writing to the database.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing geography data before seeding. USE WITH CAUTION.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        clear = options["clear"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no database changes will be made.\n"))

        if clear and not dry_run:
            self.stdout.write(self.style.WARNING(
                "⚠  --clear specified. Deleting all regions, districts, and TAs..."
            ))
            TraditionalAuthority.objects.all().delete()
            District.objects.all().delete()
            Region.objects.all().delete()
            self.stdout.write(self.style.WARNING("Cleared.\n"))

        region_created = 0
        district_created = 0
        ta_created = 0

        for region_name, districts in MALAWI_DATA.items():
            if dry_run:
                self.stdout.write(f"  Region: {region_name}")
            else:
                region, r_new = Region.objects.get_or_create(name=region_name)
                if r_new:
                    region_created += 1

            for district_name, tas in districts.items():
                if dry_run:
                    self.stdout.write(f"    District: {district_name} ({len(tas)} TAs)")
                else:
                    region_obj = Region.objects.get(name=region_name)
                    district, d_new = District.objects.get_or_create(
                        region=region_obj,
                        name=district_name,
                    )
                    if d_new:
                        district_created += 1

                    for ta_name in tas:
                        _ta, ta_new = TraditionalAuthority.objects.get_or_create(
                            district=district,
                            name=ta_name,
                        )
                        if ta_new:
                            ta_created += 1

        if dry_run:
            total_regions = len(MALAWI_DATA)
            total_districts = sum(len(d) for d in MALAWI_DATA.values())
            total_tas = sum(
                len(tas)
                for districts in MALAWI_DATA.values()
                for tas in districts.values()
            )
            self.stdout.write(self.style.SUCCESS(
                f"\nDry run complete. Would seed: "
                f"{total_regions} regions, {total_districts} districts, {total_tas} TAs."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\n✓ Seeding complete. "
                f"Created: {region_created} regions, "
                f"{district_created} districts, "
                f"{ta_created} TAs. "
                f"(Existing records were skipped.)"
            ))
