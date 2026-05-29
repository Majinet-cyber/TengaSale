# Seed Device Finance Catalog

This project includes a production-safe Django management command for seeding editable device finance deals:

```bash
python manage.py seed_device_catalog
```

The command seeds common Tecno, Itel, Redmi/Xiaomi, and Samsung device deals into `DeviceDeal` records. It uses conservative MWK placeholder prices, a 12-month term, the existing TengaSale loan multiplier, active/in-stock status, `New` condition, lock-ready metadata, and editable finance fields in Django admin/HQ.

## Safety

- The command uses `update_or_create()` so it can be run repeatedly without creating duplicates.
- Matching uses brand, model name, specs, and condition.
- `--dry-run` prints what would be created or updated without saving.
- `--update-only` updates existing matching records and skips missing devices.
- `--clear` deletes only records marked with `catalog_source=seed_device_catalog`; it does not delete user-created catalog records.

## Render Commands

Run migrations first:

```bash
python manage.py migrate
```

Preview the seed:

```bash
python manage.py seed_device_catalog --dry-run
```

Seed the catalog:

```bash
python manage.py seed_device_catalog
```

Safe production refresh command:

```bash
python manage.py seed_device_catalog --update-only
```

Clear only seeded catalog rows:

```bash
python manage.py seed_device_catalog --clear
```
