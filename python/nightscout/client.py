#!/usr/bin/env python3
"""Read-only Nightscout API client; tokens and api-secrets never reach error text."""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

_TOKEN_PARAM = re.compile(r"(token=)[^&\s\"']+")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class NightscoutError(RuntimeError):

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _sanitize_base_url(base_url: str) -> str:
    """Normalize a pasted URL: no query/fragment, no control characters, no trailing slash."""
    url = base_url.strip()
    if _CONTROL_CHARS.search(url):
        raise NightscoutError("Nightscout base URL contains control characters")
    parts = urllib.parse.urlsplit(url)
    cleaned = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return cleaned.rstrip("/")


def _iso_ms(epoch_ms: int) -> str:
    """Format epoch milliseconds as the ISO-8601 UTC string Nightscout stores in created_at."""
    moment = datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class NightscoutClient:

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        api_secret: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = _sanitize_base_url(base_url)
        self.token = token
        self.api_secret = api_secret
        self.timeout = timeout

    def status(self) -> dict:
        """Return the Nightscout instance status document."""
        return self._get("/api/v1/status.json", {})

    def profile(self) -> list[dict]:
        """Return all profile documents."""
        return self._get("/api/v1/profile.json", {})

    def entries(self, start_ms: int, end_ms: int, count: int = 20000) -> list[dict]:
        """Return CGM sgv entries in the inclusive epoch-ms window."""
        return self._get(
            "/api/v1/entries.json",
            {
                "find[type]": "sgv",
                "find[date][$gte]": str(start_ms),
                "find[date][$lte]": str(end_ms),
                "count": count,
            },
        )

    def treatments(self, start_ms: int, end_ms: int, count: int = 5000) -> list[dict]:
        """Return treatments in the inclusive epoch-ms window."""
        return self._get(
            "/api/v1/treatments.json",
            {
                "find[created_at][$gte]": _iso_ms(start_ms),
                "find[created_at][$lte]": _iso_ms(end_ms),
                "count": count,
            },
        )

    def devicestatus(self, start_ms: int, end_ms: int, count: int = 5000) -> list[dict]:
        """Return device status documents in the inclusive epoch-ms window."""
        return self._get(
            "/api/v1/devicestatus.json",
            {
                "find[created_at][$gte]": _iso_ms(start_ms),
                "find[created_at][$lte]": _iso_ms(end_ms),
                "count": count,
            },
        )

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        query = dict(params)
        if self.token:
            query["token"] = self.token
        status, body = self._request(path, query, {})
        if status == 401 and self.api_secret:
            query.pop("token", None)
            digest = hashlib.sha1(self.api_secret.encode()).hexdigest()
            status, body = self._request(path, query, {"api-secret": digest})
        if status >= 400:
            raise NightscoutError(self._redact(f"GET {path} failed with HTTP {status}: {body}"), status)
        return body

    def _request(self, path: str, params: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        try:
            request = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, self._decode(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, self._decode(exc.read())
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise NightscoutError(self._redact(f"GET {path} failed: {reason}")) from None

    def _decode(self, raw: bytes) -> Any:
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _redact(self, text: str) -> str:
        redacted = text.replace(self.base_url, "***") if self.base_url else text
        netloc = urllib.parse.urlsplit(self.base_url).netloc
        if netloc:
            redacted = redacted.replace(netloc, "***")
        redacted = _TOKEN_PARAM.sub(r"\1***", redacted)
        redacted = redacted.replace(self.api_secret, "***") if self.api_secret else redacted
        redacted = redacted.replace(self.token, "***") if self.token else redacted
        return redacted.replace("api-secret", "***")
