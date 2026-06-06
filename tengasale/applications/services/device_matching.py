import re


def normalize_device_name(name):
    text = str(name or "").lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\b(telecom|mobile|limited|ltd|galaxy)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    aliases = {
        "tecnomobile": "tecno",
        "tecno": "tecno",
        "infinix": "infinix",
        "itel": "itel",
        "samsung": "samsung",
    }
    for alias, canonical in aliases.items():
        if text.startswith(alias):
            text = canonical + text[len(alias):]
            break
    return text


def _first_int(value):
    match = re.search(r"(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


def _extract_ram_rom_from_specs(specs):
    text = str(specs or "").lower()
    numbers = [int(num) for num in re.findall(r"(\d+)\s*(?:gb|g)\b", text)]
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    if "+" in text:
        parts = [_first_int(part) for part in text.split("+", 1)]
        if all(parts):
            return parts[0], parts[1]
    return (numbers[0], None) if numbers else (None, None)


def device_descriptor(*, brand="", model="", specs="", ram=None, rom=None):
    spec_ram, spec_rom = _extract_ram_rom_from_specs(specs)
    return {
        "brand": str(brand or "").strip(),
        "model": str(model or "").strip(),
        "ram": ram if ram is not None else spec_ram,
        "rom": rom if rom is not None else spec_rom,
    }


def descriptor_from_deal(deal):
    if not deal:
        return device_descriptor()
    brand = getattr(getattr(deal, "brand", None), "name", "")
    return device_descriptor(
        brand=brand,
        model=getattr(deal, "model_name", ""),
        specs=getattr(deal, "specs", ""),
    )


def compare_devices(*, deal_device, scanned_device, imei=""):
    invalid_imei = bool(imei) and not (str(imei).isdigit() and len(str(imei)) == 15)
    deal_brand_model = normalize_device_name(f"{deal_device.get('brand', '')} {deal_device.get('model', '')}")
    scanned_brand_model = normalize_device_name(f"{scanned_device.get('brand', '')} {scanned_device.get('model', '')}")
    model_match = bool(deal_brand_model and scanned_brand_model and deal_brand_model == scanned_brand_model)
    ram_match = (
        deal_device.get("ram") is None
        or scanned_device.get("ram") is None
        or int(deal_device.get("ram")) == int(scanned_device.get("ram"))
    )
    rom_match = (
        deal_device.get("rom") is None
        or scanned_device.get("rom") is None
        or int(deal_device.get("rom")) == int(scanned_device.get("rom"))
    )
    matches = bool(not invalid_imei and model_match and ram_match and rom_match)
    return {
        "matches": matches,
        "invalid_imei": invalid_imei,
        "model_match": model_match,
        "ram_match": ram_match,
        "rom_match": rom_match,
        "deal_device": deal_device,
        "scanned_device": scanned_device,
    }


def compare_deal_to_imei_result(deal, *, api_brand="", api_model="", api_specs="", imei=""):
    return compare_devices(
        deal_device=descriptor_from_deal(deal),
        scanned_device=device_descriptor(brand=api_brand, model=api_model, specs=api_specs),
        imei=imei,
    )
