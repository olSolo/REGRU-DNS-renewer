#!/usr/bin/env python3
"""Update A/AAAA DNS records at REG.RU to match this machine's public IP."""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from regru_client import RegRuClient, RegRuError

LOG = logging.getLogger("dns-renewer")

IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")

DEFAULT_IPV4_URLS = ["https://api.ipify.org", "https://ifconfig.me/ip"]
DEFAULT_IPV6_URLS = ["https://api6.ipify.org", "https://ifconfig.me/ip"]


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def resolve_env_file(
    *,
    cli_path: str,
    config_path: str | None,
    project_dir: Path | None = None,
    exclusive: bool = False,
) -> tuple[Path | None, dict[str, str]]:
    candidates: list[Path] = []
    if cli_path:
        candidates.append(Path(cli_path).expanduser())
    if config_path:
        cfg = Path(config_path).expanduser()
        candidates.append(cfg)
        if project_dir and not cfg.is_absolute():
            candidates.append(project_dir / cfg)
    if not exclusive:
        candidates.extend(
            [
                Path.home() / ".regru_api_env",
            ]
        )
        if project_dir:
            candidates.append(project_dir / ".env")
        candidates.append(Path("/root/regru_api.env"))

    seen: set[Path] = set()
    for path in candidates:
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        env = load_env_file(path)
        if env.get("REGU_USER") and env.get("REGU_PASS"):
            return path, env

    return None, {}


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def fetch_url_text(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "dns-renewer/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8").strip()


def is_public_ipv4(value: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
    )


def is_public_ipv6(value: str) -> bool:
    try:
        ip = ipaddress.IPv6Address(value)
    except ipaddress.AddressValueError:
        return False
    return not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified)


def fetch_ipv4_from_url(url: str) -> str | None:
    try:
        text = fetch_url_text(url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        LOG.warning("IPv4 URL %s failed: %s", url, exc)
        return None
    candidate = text.split()[0].strip()
    if is_public_ipv4(candidate):
        return candidate
    LOG.warning("IPv4 URL %s returned invalid value: %r", url, text[:80])
    return None


def fetch_ipv6_from_url(url: str) -> str | None:
    try:
        text = fetch_url_text(url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        LOG.warning("IPv6 URL %s failed: %s", url, exc)
        return None
    candidate = text.split()[0].strip()
    if is_public_ipv6(candidate):
        return str(ipaddress.IPv6Address(candidate))
    LOG.warning("IPv6 URL %s returned invalid value: %r", url, text[:80])
    return None


def detect_local_ipv4() -> str | None:
    import subprocess

    try:
        proc = subprocess.run(
            ["ip", "-4", "route", "get", "1.1.1.1"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"\bsrc\s+(\d{1,3}(?:\.\d{1,3}){3})\b", proc.stdout)
    if not match:
        return None
    candidate = match.group(1)
    if is_public_ipv4(candidate):
        return candidate
    return None


def detect_local_ipv6() -> str | None:
    import subprocess

    try:
        proc = subprocess.run(
            ["ip", "-6", "route", "get", "2001:4860:4860::8888"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"\bsrc\s+([0-9a-fA-F:]+)\b", proc.stdout)
    if not match:
        return None
    candidate = match.group(1)
    if is_public_ipv6(candidate):
        return str(ipaddress.IPv6Address(candidate))
    return None


def resolve_public_ips(ip_cfg: dict[str, Any]) -> tuple[str | None, str | None]:
    ipv4_cfg = ip_cfg.get("ipv4") or {}
    ipv6_cfg = ip_cfg.get("ipv6") or {}

    ipv4: str | None = None
    for method in ipv4_cfg.get("methods") or ["local", "url"]:
        if method == "local" and ipv4_cfg.get("use_local_interface", True):
            ipv4 = detect_local_ipv4()
        elif method == "url":
            for url in ipv4_cfg.get("urls") or DEFAULT_IPV4_URLS:
                ipv4 = fetch_ipv4_from_url(url)
                if ipv4:
                    break
        if ipv4:
            break

    ipv6: str | None = None
    if ipv6_cfg.get("enabled", True):
        for method in ipv6_cfg.get("methods") or ["local", "url"]:
            if method == "local" and ipv6_cfg.get("use_local_interface", True):
                ipv6 = detect_local_ipv6()
            elif method == "url":
                for url in ipv6_cfg.get("urls") or DEFAULT_IPV6_URLS:
                    ipv6 = fetch_ipv6_from_url(url)
                    if ipv6:
                        break
            if ipv6:
                break

    return ipv4, ipv6


def normalize_zone(zone: dict[str, Any]) -> dict[str, Any]:
    domain = zone.get("domain")
    if not domain:
        raise ValueError("each zone requires 'domain'")

    subdomains = zone.get("subdomains")
    if subdomains is None:
        subdomains = ["@"]

    record_types = zone.get("record_types") or {}
    update_a = record_types.get("a", True)
    update_aaaa = record_types.get("aaaa", True)

    zone_regru = zone.get("regru") if isinstance(zone.get("regru"), dict) else {}
    regru_env_file = zone.get("regru_env_file") or zone_regru.get("env_file") or ""
    zone_ssl = zone_regru.get("ssl") if isinstance(zone_regru.get("ssl"), dict) else {}
    regru_ssl_cert = zone.get("regru_ssl_cert") or zone_ssl.get("cert") or ""
    regru_ssl_key = zone.get("regru_ssl_key") or zone_ssl.get("key") or ""

    return {
        "domain": domain,
        "subdomains": [str(s).strip() or "@" for s in subdomains],
        "update_a": update_a,
        "update_aaaa": update_aaaa,
        "regru_env_file": str(regru_env_file).strip(),
        "regru_ssl_cert": str(regru_ssl_cert).strip(),
        "regru_ssl_key": str(regru_ssl_key).strip(),
    }


def load_zones(config: dict[str, Any]) -> list[dict[str, Any]]:
    if zones := config.get("zones"):
        return [normalize_zone(z) for z in zones]
    if domain := config.get("domain"):
        return [normalize_zone(config)]
    raise ValueError("config requires 'zones' or top-level 'domain'")


def fqdn_for_sub(domain: str, subdomain: str) -> str:
    if subdomain in ("@", ""):
        return domain
    return f"{subdomain}.{domain}"


def records_index(rrs: list[dict[str, Any]]) -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = {}
    for rr in rrs:
        sub = (rr.get("subname") or "@").strip() or "@"
        rtype = (rr.get("rectype") or "").upper()
        content = (rr.get("content") or "").strip()
        if not rtype or not content:
            continue
        out.setdefault((sub, rtype), set()).add(content)
    return out


def normalize_ipv6(value: str) -> str:
    return str(ipaddress.IPv6Address(value))


def update_zone(
    client: RegRuClient,
    zone: dict[str, Any],
    ipv4: str | None,
    ipv6: str | None,
    *,
    dry_run: bool,
) -> int:
    domain = zone["domain"]
    changes = 0
    rrs = client.get_resource_records(domain)
    current = records_index(rrs)

    for sub in zone["subdomains"]:
        fqdn = fqdn_for_sub(domain, sub)

        if zone["update_a"] and ipv4:
            cur_a = current.get((sub, "A"), set())
            if cur_a == {ipv4}:
                LOG.info("%s A already %s", fqdn, ipv4)
            else:
                LOG.info("%s A: %s -> %s", fqdn, sorted(cur_a) or ["<none>"], ipv4)
                if not dry_run:
                    for stale in sorted(cur_a):
                        if stale != ipv4:
                            LOG.info("Removing stale %s A %s", fqdn, stale)
                            client.remove_record(domain, sub, "A", stale)
                    if ipv4 not in cur_a:
                        client.add_a(domain, sub, ipv4)
                changes += 1

        if zone["update_aaaa"] and ipv6:
            cur_aaaa = {normalize_ipv6(x) for x in current.get((sub, "AAAA"), set())}
            want = normalize_ipv6(ipv6)
            if cur_aaaa == {want}:
                LOG.info("%s AAAA already %s", fqdn, want)
            else:
                LOG.info("%s AAAA: %s -> %s", fqdn, sorted(cur_aaaa) or ["<none>"], want)
                if not dry_run:
                    for stale in sorted(cur_aaaa):
                        if stale != want:
                            LOG.info("Removing stale %s AAAA %s", fqdn, stale)
                            client.remove_record(domain, sub, "AAAA", stale)
                    if want not in cur_aaaa:
                        client.add_aaaa(domain, sub, want)
                changes += 1

    return changes


def resolve_zone_account(
    *,
    zone: dict[str, Any],
    config: dict[str, Any],
    default_env: dict[str, str],
    default_env_path: Path | None,
    project_dir: Path,
    cli_env_file: str,
) -> dict[str, Any]:
    """Credentials and SSL paths for one zone's Reg.ru account."""
    regru_cfg = config.get("regru") or {}
    zone_env_hint = zone.get("regru_env_file") or ""
    if zone_env_hint:
        env_path, env = resolve_env_file(
            cli_path=cli_env_file,
            config_path=zone_env_hint,
            project_dir=project_dir,
            exclusive=True,
        )
        if not env.get("REGU_USER") or not env.get("REGU_PASS"):
            raise RegRuError(
                f"Zone {zone['domain']}: missing REGU_USER/REGU_PASS in {zone_env_hint} "
                f"(copy from {zone_env_hint}.example)"
            )
    else:
        env_path, env = default_env_path, default_env

    username = env.get("REGU_USER") or regru_cfg.get("username") or config.get("username")
    password = env.get("REGU_PASS") or regru_cfg.get("password") or config.get("password")
    if not username or not password:
        raise RegRuError(
            f"Set REGU_USER/REGU_PASS for zone {zone['domain']} "
            f"(regru.env_file={zone_env_hint or 'default'})"
        )

    ssl_cfg = regru_cfg.get("ssl") if isinstance(regru_cfg.get("ssl"), dict) else {}
    global_ssl = config.get("ssl") if isinstance(config.get("ssl"), dict) else {}
    ssl_cert = (
        env.get("REGU_SSL_CERT")
        or zone.get("regru_ssl_cert")
        or ssl_cfg.get("cert")
        or global_ssl.get("cert")
        or None
    )
    ssl_key = (
        env.get("REGU_SSL_KEY")
        or zone.get("regru_ssl_key")
        or ssl_cfg.get("key")
        or global_ssl.get("key")
        or None
    )
    if ssl_cert:
        ssl_cert = str(Path(ssl_cert).expanduser())
    if ssl_key:
        ssl_key = str(Path(ssl_key).expanduser())

    return {
        "username": username,
        "password": password,
        "ssl_cert": ssl_cert,
        "ssl_key": ssl_key,
        "env_label": str(env_path or zone_env_hint or "default"),
    }


def build_regru_client(
    *,
    zone: dict[str, Any],
    config: dict[str, Any],
    default_env: dict[str, str],
    default_env_path: Path | None,
    project_dir: Path,
    cli_env_file: str,
) -> RegRuClient:
    """Reg.ru API client for one zone (own account/env or shared default)."""
    acc = resolve_zone_account(
        zone=zone,
        config=config,
        default_env=default_env,
        default_env_path=default_env_path,
        project_dir=project_dir,
        cli_env_file=cli_env_file,
    )
    LOG.debug(
        "Zone %s: account=%s env=%s ssl=%s",
        zone["domain"],
        acc["username"],
        acc["env_label"],
        acc["ssl_cert"] or "<auto>",
    )
    return RegRuClient(
        acc["username"],
        acc["password"],
        ssl_cert=acc["ssl_cert"],
        ssl_key=acc["ssl_key"],
    )


def account_cache_key(acc: dict[str, Any]) -> tuple[str, str, str]:
    return (acc["username"], acc["ssl_cert"] or "", acc["ssl_key"] or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-c",
        "--config",
        default=str(Path(__file__).resolve().parent / "config.json"),
        help="Path to config.json",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="File with REGU_USER and REGU_PASS (default: from config or ~/.regru_api_env)",
    )
    parser.add_argument(
        "--zone",
        action="append",
        dest="zones_filter",
        metavar="DOMAIN",
        help="Update only this domain (repeatable)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only show planned changes")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config_path = Path(args.config)
    if not config_path.is_file():
        LOG.error("Config not found: %s (copy config.json.example)", config_path)
        return 1

    try:
        config = load_config(config_path)
        zones = load_zones(config)
    except ValueError as exc:
        LOG.error("%s", exc)
        return 1

    if args.zones_filter:
        wanted = set(args.zones_filter)
        zones = [z for z in zones if z["domain"] in wanted]
        if not zones:
            LOG.error("No matching zones for: %s", ", ".join(sorted(wanted)))
            return 1

    regru_cfg = config.get("regru") or {}
    project_dir = Path(__file__).resolve().parent

    needs_default_account = any(not z["regru_env_file"] for z in zones)
    default_env: dict[str, str] = {}
    default_env_path: Path | None = None
    if needs_default_account:
        default_env_path, default_env = resolve_env_file(
            cli_path=args.env_file,
            config_path=regru_cfg.get("env_file"),
            project_dir=project_dir,
        )
        if not default_env.get("REGU_USER") or not default_env.get("REGU_PASS"):
            LOG.error(
                "Set REGU_USER and REGU_PASS in one of: %s, %s, %s",
                project_dir / ".env",
                Path.home() / ".regru_api_env",
                "/root/regru_api.env",
            )
            return 1
        LOG.debug("Default Reg.ru account from %s", default_env_path)

    ip_cfg = config.get("ip_detection") or config
    ipv4, ipv6 = resolve_public_ips(ip_cfg)
    if not ipv4:
        LOG.error("Could not detect this machine's public IPv4")
        return 1

    ipv6_enabled = (ip_cfg.get("ipv6") or {}).get("enabled", True)
    if not ipv6_enabled:
        ipv6 = None
        LOG.info("IPv6 updates disabled in config")
    elif not ipv6:
        LOG.warning("Public IPv6 not detected on this machine; skipping AAAA updates")

    LOG.info("This machine: IPv4=%s IPv6=%s", ipv4, ipv6 or "<skip>")

    clients: dict[tuple[str, str, str], RegRuClient] = {}

    def client_for_zone(zone: dict[str, Any]) -> RegRuClient:
        acc = resolve_zone_account(
            zone=zone,
            config=config,
            default_env=default_env,
            default_env_path=default_env_path,
            project_dir=project_dir,
            cli_env_file=args.env_file,
        )
        key = account_cache_key(acc)
        if key not in clients:
            clients[key] = RegRuClient(
                acc["username"],
                acc["password"],
                ssl_cert=acc["ssl_cert"],
                ssl_key=acc["ssl_key"],
            )
            LOG.debug("Reg.ru client: account=%s env=%s", acc["username"], acc["env_label"])
        return clients[key]

    total_changes = 0
    try:
        for zone in zones:
            LOG.info("Zone %s (%s)", zone["domain"], ", ".join(zone["subdomains"]))
            client = client_for_zone(zone)
            total_changes += update_zone(
                client,
                zone,
                ipv4,
                ipv6,
                dry_run=args.dry_run,
            )
    except RegRuError as exc:
        LOG.error("%s", exc)
        return 1
    except socket.gaierror as exc:
        LOG.error("DNS resolution error: %s", exc)
        return 1

    if args.dry_run:
        LOG.info("Dry run complete (%d change(s) planned)", total_changes)
    elif total_changes:
        LOG.info("Updated %d record(s)", total_changes)
    else:
        LOG.info("No changes needed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
