"""Prezentacja i eksport wyników analizy."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from loganalyzer.core.stats import LogStats
from loganalyzer.services.geoip import GeoInfo


def human_bytes(n: float) -> str:
    """Zamienia liczbę bajtów na czytelny zapis (KiB, MiB, ...)."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def format_report(stats: LogStats, top_ips: list[tuple[str, int, GeoInfo]] | None = None, top: int = 10) -> str:
    """Zwraca raport tekstowy do wyświetlenia w konsoli."""
    lines = [
        "=" * 60,
        "RAPORT ANALIZY LOGÓW",
        "=" * 60,
        f"Plików:               {len(stats.files)}",
        f"Linii łącznie:        {stats.total_lines:,}".replace(",", " "),
        f"Poprawnych:           {stats.parsed_lines:,}".replace(",", " "),
        f"Uszkodzonych:         {stats.malformed_lines:,}".replace(",", " "),
        f"Unikalnych IP:        {stats.unique_ips}",
        f"Wysłano danych:       {human_bytes(stats.bytes_sent)}",
        f"Odsetek błędów 4xx/5xx: {stats.error_rate:.2%}",
        "",
        "Kody HTTP:",
        *(f"  {code}: {n}" for code, n in sorted(stats.status_counts.items())),
        "",
        f"Top {top} URL:",
        *(f"  {n:>8}  {url}" for url, n in stats.url_counts.most_common(top)),
    ]
    if top_ips:
        lines += ["", f"Top {len(top_ips)} adresów IP (GeoIP):"]
        lines += [f"  {n:>8}  {ip:<16} {info.country:<4} {info.city}" for ip, n, info in top_ips]
    lines.append("=" * 60)
    return "\n".join(lines)


def export_json(stats: LogStats, path: str | Path, top: int = 10) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats.to_dict(top), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_csv(stats: LogStats, path: str | Path) -> Path:
    """Eksportuje liczniki adresów IP do CSV (ip, liczba_żądań)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ip", "requests"])
        writer.writerows(stats.ip_counts.most_common())
    return path
