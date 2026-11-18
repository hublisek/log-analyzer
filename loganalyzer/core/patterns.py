"""Wyrażenia regularne używane w projekcie.

Wszystkie wzorce są kompilowane raz, przy imporcie modułu (``re.compile``),
dzięki czemu nie są kompilowane ponownie dla każdej linii logu.

Wzorce:
    * LOG_LINE_RE   – ekstrakcja pól z linii w formacie Apache/Nginx "combined",
    * IP_RE         – walidacja adresu IPv4 (każdy oktet 0–255),
    * DATE_RE       – rozbiór znacznika czasu ``18/Nov/2026:20:22:01 +0100``,
    * CONFIG_LINE_RE – rozbiór linii pliku konfiguracyjnego ``klucz = wartość``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# Przykład linii:
# 83.12.4.200 - - [18/Nov/2026:20:22:01 +0100] "GET /index.html HTTP/1.1" 200 5123 "-" "Mozilla/5.0"
LOG_LINE_RE = re.compile(
    r"""
    ^(?P<ip>\S+)\s+                     # adres klienta
    (?P<ident>\S+)\s+                   # identd (zwykle "-")
    (?P<user>\S+)\s+                    # użytkownik (zwykle "-")
    \[(?P<timestamp>[^\]]+)\]\s+        # [data czas strefa]
    "(?P<method>[A-Z]+)\s+              # metoda HTTP
    (?P<url>\S+)\s+                     # ścieżka
    (?P<protocol>HTTP/\d(?:\.\d)?)"\s+  # protokół
    (?P<status>\d{3})\s+                # kod odpowiedzi
    (?P<size>\d+|-)                     # rozmiar odpowiedzi w bajtach
    (?:\s+"(?P<referrer>[^"]*)"         # opcjonalnie: referrer
    \s+"(?P<agent>[^"]*)")?             # opcjonalnie: user-agent
    \s*$
    """,
    re.VERBOSE,
)

_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
IP_RE = re.compile(rf"^(?P<a>{_OCTET})\.(?P<b>{_OCTET})\.(?P<c>{_OCTET})\.(?P<d>{_OCTET})$")

DATE_RE = re.compile(
    r"^(?P<day>\d{2})/(?P<month>[A-Z][a-z]{2})/(?P<year>\d{4})"
    r":(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"\s(?P<tz_sign>[+-])(?P<tz_hours>\d{2})(?P<tz_minutes>\d{2})$"
)

CONFIG_LINE_RE = re.compile(
    r"^\s*(?P<key>[a-z_][a-z0-9_]*)\s*=\s*(?P<value>.*?)\s*(?:#.*)?$"
)

MONTHS: dict[str, int] = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}


def is_valid_ip(ip: str) -> bool:
    """Zwraca ``True``, jeśli ``ip`` jest poprawnym adresem IPv4."""
    return IP_RE.fullmatch(ip) is not None


def is_private_ip(ip: str) -> bool:
    """Sprawdza, czy adres należy do puli prywatnej (10/8, 172.16/12, 192.168/16)."""
    match = IP_RE.fullmatch(ip)
    if match is None:
        return False
    a, b = int(match["a"]), int(match["b"])
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def parse_timestamp(text: str) -> datetime:
    """Zamienia znacznik czasu z logu na obiekt ``datetime`` ze strefą czasową.

    Raises:
        ValueError: gdy tekst nie pasuje do formatu lub zawiera nieznany miesiąc.
    """
    match = DATE_RE.fullmatch(text)
    if match is None:
        raise ValueError(f"Niepoprawny znacznik czasu: {text!r}")
    month = MONTHS.get(match["month"])
    if month is None:
        raise ValueError(f"Nieznany miesiąc: {match['month']!r}")
    offset = timedelta(hours=int(match["tz_hours"]), minutes=int(match["tz_minutes"]))
    if match["tz_sign"] == "-":
        offset = -offset
    return datetime(
        int(match["year"]), month, int(match["day"]),
        int(match["hour"]), int(match["minute"]), int(match["second"]),
        tzinfo=timezone(offset),
    )
