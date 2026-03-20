"""
BOMKart API Client

HTTP client for the BOMKart backend API.
Uses only Python stdlib (urllib) — no external dependencies required.
KiCad's embedded Python doesn't ship pip packages, so we stay stdlib-only.
"""

import json
import urllib.request
import urllib.error
import urllib.parse
from typing import Optional, List, Dict, Any


class BOMKartAPIError(Exception):
    def __init__(self, message: str, status_code: int = 0, detail: str = ""):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(self.message)


class BOMKartAPI:
    """REST client for BOMKart backend."""

    def __init__(self, base_url: str = "http://localhost:8000/v1", api_key: str = "", install_id: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.install_id = install_id
        self.timeout = 300  # BOM checks hit external LCSC API per item; large BOMs need time

    def _request(self, method: str, path: str, data: Any = None) -> dict:
        """Make HTTP request, return parsed JSON response."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "BOMKart-KiCad/0.2.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.install_id:
            headers["X-Install-ID"] = self.install_id

        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")
            except Exception:
                pass
            raise BOMKartAPIError(
                f"HTTP {e.code}: {e.reason}", status_code=e.code, detail=detail
            )
        except urllib.error.URLError as e:
            raise BOMKartAPIError(f"Connection failed: {e.reason}")
        except json.JSONDecodeError as e:
            raise BOMKartAPIError(f"Invalid JSON response: {e}")
        except Exception as e:
            raise BOMKartAPIError(f"Request failed: {str(e)}")

    # ── BOM Endpoints ──────────────────────────────────

    def check_bom(self, bom_payload: dict) -> dict:
        """
        POST /bom/check
        Send BOM items, get pricing + availability from all distributors.
        """
        return self._request("POST", "/bom/check", data=bom_payload)

    # ── Search Endpoint (Component Search Engine) ──────

    def search_component(self, query: str, category: str = "", limit: int = 20) -> dict:
        """
        GET /components/search?q=...
        Search the component database. Used for the in-KiCad component search feature.
        """
        params = urllib.parse.urlencode({
            "q": query,
            "category": category,
            "limit": limit,
        })
        return self._request("GET", f"/components/search?{params}")

    def get_component_detail(self, mpn: str) -> dict:
        """
        GET /components/{mpn}
        Get detailed info for a specific component: all distributors, pricing,
        datasheet link, alternatives.
        """
        return self._request("GET", f"/components/{urllib.parse.quote(mpn)}")

    def get_alternatives(self, mpn: str, value: str = "", footprint: str = "") -> dict:
        """
        GET /components/{mpn}/alternatives
        Get compatible alternative parts.
        """
        params = urllib.parse.urlencode({"value": value, "footprint": footprint})
        return self._request("GET", f"/components/{urllib.parse.quote(mpn)}/alternatives?{params}")

    # ── Order Endpoints ────────────────────────────────

    def place_order(self, order_data: dict) -> dict:
        """POST /orders/place"""
        return self._request("POST", "/orders/place", data=order_data)

    def get_order_status(self, order_id: str) -> dict:
        """GET /orders/{order_id}"""
        return self._request("GET", f"/orders/{order_id}")

    # ── Analytics ──────────────────────────────────────

    def register_install(self, payload: dict) -> dict:
        """POST /analytics/register — called on settings save."""
        return self._request("POST", "/analytics/register", data=payload)

    # ── Health ─────────────────────────────────────────

    def health(self) -> bool:
        try:
            r = self._request("GET", "/health")
            return r.get("status") == "ok"
        except Exception:
            return False
