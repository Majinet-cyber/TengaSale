"""
IMEI Check API client for TengaSale.

All API calls are made server-side. The API key is never exposed to
browser templates, JavaScript, or network payloads.

Supports two endpoint styles:
  - alpha.imeicheck.com PHP-API (primary)
  - DHRU-compatible endpoint (fallback/alternative)
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


def _mask_key(key: str) -> str:
    """Return a masked API key safe for log output."""
    if not key:
        return "***"
    return key[:4] + "***" + key[-4:] if len(key) > 8 else "***"


class ImeiCheckClient:
    """
    Client for the alpha.imeicheck.com / DHRU IMEI verification API.

    Usage::

        client = ImeiCheckClient()
        result = client.check_imei("358089361347363")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        username: Optional[str] = None,
        base_url: Optional[str] = None,
        alpha_base_url: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self.api_key = api_key or getattr(settings, "IMEI_CHECK_API_KEY", "")
        self.username = username or getattr(settings, "IMEI_CHECK_USERNAME", "")
        self.base_url = (base_url or getattr(settings, "IMEI_CHECK_ENDPOINT_BASE", "https://dhru.checkimei.com")).rstrip("/")
        self.alpha_base_url = (
            alpha_base_url or getattr(settings, "IMEI_CHECK_ALPHA_ENDPOINT_BASE", "https://alpha.imeicheck.com")
        ).rstrip("/")
        self.timeout = timeout or getattr(settings, "IMEI_CHECK_TIMEOUT_SECONDS", 20)

    # ── Internal HTTP helper ─────────────────────────────────────────────────

    def _get_json(self, url: str) -> dict:
        """
        Make a GET request to *url* and return the parsed JSON body.
        Never raises — returns an error dict on failure.
        """
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "TengaSale-IMEICheck/1.0",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            logger.warning(
                "IMEI API HTTP %s for %s (key=%s): %s",
                exc.code,
                url.split("?")[0],
                _mask_key(self.api_key),
                body[:300],
            )
            try:
                return json.loads(body)
            except Exception:
                return {"error": f"HTTP {exc.code}", "_raw": body[:300]}
        except urllib.error.URLError as exc:
            logger.error(
                "IMEI API connection error (key=%s): %s",
                _mask_key(self.api_key),
                exc.reason,
            )
            return {"error": f"Connection error: {exc.reason}"}
        except json.JSONDecodeError as exc:
            logger.warning("IMEI API returned non-JSON response: %s", exc)
            return {"error": "Invalid JSON response from API"}
        except Exception as exc:
            logger.exception("IMEI API unexpected error (key=%s)", _mask_key(self.api_key))
            return {"error": str(exc)}

    # ── Public API methods ───────────────────────────────────────────────────

    def get_balance(self) -> dict:
        """
        Retrieve current account balance/credits.
        Uses alpha.imeicheck.com PHP-API endpoint.
        """
        url = (
            f"{self.alpha_base_url}/api/php-api/balance"
            f"?key={urllib.parse.quote(self.api_key, safe='')}"
        )
        return self._get_json(url)

    def get_services(self) -> dict:
        """
        Retrieve the list of available IMEI check services.
        """
        url = (
            f"{self.alpha_base_url}/api/php-api/services"
            f"?key={urllib.parse.quote(self.api_key, safe='')}"
        )
        return self._get_json(url)

    def submit_imei_check(self, imei: str, service_id: Optional[str] = None) -> dict:
        """
        Submit an IMEI for checking.
        Returns the raw API response dict.
        """
        sid = service_id or getattr(settings, "IMEI_CHECK_SERVICE_ID", "") or ""
        if not sid:
            logger.warning(
                "IMEI check submitted without a service_id — "
                "result may be generic. Set IMEI_CHECK_SERVICE_ID in settings."
            )

        params = {
            "key": self.api_key,
            "imei": imei,
            "serviceId": sid,
        }
        query = urllib.parse.urlencode(params)
        url = f"{self.alpha_base_url}/api/php-api/order?{query}"
        return self._get_json(url)

    def get_order_result(self, order_id: str) -> dict:
        """
        Retrieve the result of a previously submitted order by its ID.
        """
        params = {
            "key": self.api_key,
            "orderId": order_id,
        }
        query = urllib.parse.urlencode(params)
        url = f"{self.alpha_base_url}/api/php-api/result?{query}"
        return self._get_json(url)

    def check_imei(self, imei: str, service_id: Optional[str] = None) -> dict:
        """
        Submit an IMEI check and return a normalised result dictionary.

        Return shape::

            {
                "success": bool,
                "imei": str,
                "order_id": str,
                "status": "success" | "pending" | "failed" | "error",
                "raw_result": str,
                "object": dict,
                "price": str,
                "duration": str,
                "error": str,
            }
        """
        raw = self.submit_imei_check(imei, service_id=service_id)
        return self._normalise(raw, imei)

    # ── Internal normalisation ───────────────────────────────────────────────

    def _normalise(self, raw: dict, imei: str) -> dict:
        """
        Normalise any raw API response into a stable shape regardless of
        which backend variant was used.
        """
        base = {
            "success": False,
            "imei": imei,
            "order_id": "",
            "status": "error",
            "raw_result": "",
            "object": {},
            "price": "",
            "duration": "",
            "error": "",
        }

        if not raw or not isinstance(raw, dict):
            base["error"] = str(raw) if raw else "Empty API response"
            return base

        # If we only have an error and no order data, surface it
        if "error" in raw and not raw.get("orderId") and not raw.get("order_id"):
            base["error"] = str(raw["error"])
            return base

        base["order_id"] = str(raw.get("orderId", raw.get("order_id", "")))
        base["status"] = raw.get("status", "error")
        base["raw_result"] = raw.get("result", raw.get("raw_result", ""))
        base["object"] = raw.get("object", {}) or {}
        base["price"] = str(raw.get("price", ""))
        base["duration"] = str(raw.get("duration", ""))
        base["success"] = base["status"] == "success"

        if "error" in raw and raw["error"]:
            base["error"] = str(raw["error"])

        return base
