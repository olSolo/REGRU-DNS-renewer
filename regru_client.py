"""Minimal REG.RU API v2 client (DNS zone operations)."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


API_BASE = "https://api.reg.ru/api/regru2"


class RegRuError(RuntimeError):
    pass


def resolve_ssl_paths(
    cert: str | None = None,
    key: str | None = None,
) -> tuple[Path, Path]:
    if cert and key:
        cert_path = Path(cert).expanduser()
        key_path = Path(key).expanduser()
    elif Path.home().joinpath("Desktop/regru.crt").is_file():
        cert_path = Path.home() / "Desktop/regru.crt"
        key_path = Path.home() / "Desktop/regru.key"
    elif Path("/root/regru.crt").is_file():
        cert_path = Path("/root/regru.crt")
        key_path = Path("/root/regru.key")
    else:
        cert_path = Path(cert or Path.home() / "Desktop/regru.crt").expanduser()
        key_path = Path(key or Path.home() / "Desktop/regru.key").expanduser()

    if not cert_path.is_file() or not key_path.is_file():
        raise RegRuError(
            f"REG.RU SSL cert/key not found ({cert_path}, {key_path}). "
            "Upload regru.crt from Reg.ru API settings."
        )
    return cert_path, key_path


class RegRuClient:
    def __init__(
        self,
        username: str,
        password: str,
        *,
        ssl_cert: str | None = None,
        ssl_key: str | None = None,
    ) -> None:
        self.username = username
        self.password = password
        cert_path, key_path = resolve_ssl_paths(ssl_cert, ssl_key)
        self._ssl_context = ssl.create_default_context()
        self._ssl_context.load_cert_chain(str(cert_path), str(key_path))

    def call(self, category_fn: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = urllib.parse.urlencode(
            {
                "input_format": "json",
                "input_data": json.dumps(payload, ensure_ascii=False),
            }
        ).encode("utf-8")
        url = f"{API_BASE}/{category_fn}"
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, context=self._ssl_context, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RegRuError(f"HTTP {exc.code} for {category_fn}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RegRuError(f"Network error for {category_fn}: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RegRuError(f"Invalid JSON from {category_fn}: {raw[:500]}") from exc

        if data.get("result") != "success":
            raise RegRuError(f"API error for {category_fn}: {data}")
        return data

    def get_resource_records(self, domain: str) -> list[dict[str, Any]]:
        data = self.call(
            "zone/get_resource_records",
            {
                "username": self.username,
                "password": self.password,
                "domains": [{"dname": domain}],
                "output_content_type": "json",
            },
        )
        domains = (data.get("answer") or {}).get("domains") or []
        if not domains:
            return []
        first = domains[0]
        if first.get("result") != "success":
            raise RegRuError(f"get_resource_records failed for {domain}: {first}")
        return first.get("rrs") or []

    def add_a(self, domain: str, subdomain: str, ipaddr: str) -> None:
        self._add_record("zone/add_alias", domain, subdomain, ipaddr)

    def add_aaaa(self, domain: str, subdomain: str, ipaddr: str) -> None:
        self._add_record("zone/add_aaaa", domain, subdomain, ipaddr)

    def remove_record(
        self,
        domain: str,
        subdomain: str,
        record_type: str,
        content: str,
    ) -> None:
        data = self.call(
            "zone/remove_record",
            {
                "username": self.username,
                "password": self.password,
                "domains": [{"dname": domain}],
                "subdomain": subdomain,
                "record_type": record_type.upper(),
                "content": content,
                "output_content_type": "json",
            },
        )
        domains = (data.get("answer") or {}).get("domains") or []
        if not domains or domains[0].get("result") != "success":
            raise RegRuError(
                f"remove_record failed for {subdomain}.{domain} {record_type} {content}: {data}"
            )

    def _add_record(
        self,
        endpoint: str,
        domain: str,
        subdomain: str,
        ipaddr: str,
    ) -> None:
        data = self.call(
            endpoint,
            {
                "username": self.username,
                "password": self.password,
                "domains": [{"dname": domain}],
                "subdomain": subdomain,
                "ipaddr": ipaddr,
                "output_content_type": "json",
            },
        )
        domains = (data.get("answer") or {}).get("domains") or []
        if not domains or domains[0].get("result") != "success":
            raise RegRuError(f"{endpoint} failed for {subdomain}.{domain}: {data}")
