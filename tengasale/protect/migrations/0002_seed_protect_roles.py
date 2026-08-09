from django.db import migrations


ROLE_NAMES = [
    "Tenga Protect Officer",
    "Tenga Protect Supervisor",
    "Tenga Protect Compliance Officer",
    "Tenga Protect Administrator",
]


def seed_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in ROLE_NAMES:
        Group.objects.get_or_create(name=name)


def remove_roles(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name__in=ROLE_NAMES).delete()


class Migration(migrations.Migration):
    dependencies = [("protect", "0001_initial")]
    operations = [migrations.RunPython(seed_roles, remove_roles)]
