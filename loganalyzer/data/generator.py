"""Generator danych testowych: fikcyjne logi serwera WWW + lokalna baza GeoIP.

Logi powstają z nieskończonego generatora ``log_line_stream``, z którego
``itertools.islice`` wycina potrzebną liczbę linii. Około 1% linii jest
celowo uszkodzonych, żeby sprawdzić obsługę błędów.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path

from loganalyzer.core.patterns import is_private_ip

METHODS = ["GET"] * 80 + ["POST"] * 15 + ["PUT", "DELETE", "HEAD", "PATCH", "OPTIONS"]
STATUSES = [200] * 70 + [304] * 8 + [301, 302] * 3 + [404] * 8 + [403, 401, 400] + [500] * 2 + [502, 503]
URLS = [
    "/", "/index.html", "/kontakt", "/oferta", "/oferta/uslugi", "/blog", "/blog/python-gil",
    "/blog/generatory", "/api/v1/users", "/api/v1/orders", "/api/v1/products?page=2",
    "/static/css/main.css", "/static/js/app.js", "/img/logo.png", "/favicon.ico",
    "/login", "/logout", "/admin", "/wp-login.php", "/robots.txt", "/sitemap.xml",
]
AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    "Googlebot/2.1 (+http://www.google.com/bot.html)",
    "curl/8.5.0",
    "python-requests/2.32.3",
]
CITIES = [
    ("PL", "Wieluń"), ("PL", "Łódź"), ("PL", "Wrocław"), ("PL", "Warszawa"), ("PL", "Kraków"),
    ("DE", "Berlin"), ("DE", "Frankfurt"), ("NL", "Amsterdam"), ("US", "Ashburn"),
    ("US", "Mountain View"), ("FR", "Paryż"), ("GB", "Londyn"), ("CZ", "Praga"),
]
MALFORMED = [
    "to nie jest linia logu",
    '999.1.1.1 - - [18/Nov/2026:10:00:00 +0100] "GET / HTTP/1.1" 200 12 "-" "x"',
    '10.0.0.1 - - [32/Foo/2026:10:00:00 +0100] "GET / HTTP/1.1" 200 12 "-" "x"',
    '10.0.0.1 - - [18/Nov/2026:10:00:00 +0100] "GET /niedomkniety',
]


def make_ip_pool(rng: random.Random, size: int = 400) -> list[str]:
    """Pula adresów: ~90% publicznych (z bazy GeoIP), ~10% prywatnych."""
    prefixes = [f"{a}.{b}" for a, b in ((83, 12), (89, 64), (31, 13), (185, 25), (5, 173),
                                          (66, 249), (142, 250), (52, 95), (77, 88), (195, 150))]
    pool = [f"{rng.choice(prefixes)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}" for _ in range(size)]
    pool += [f"192.168.1.{rng.randint(2, 254)}" for _ in range(size // 10)]
    return pool


def log_line_stream(seed: int = 42, start: datetime | None = None) -> Iterator[str]:
    """Nieskończony generator linii logu w formacie "combined"."""
    rng = random.Random(seed)
    ips = make_ip_pool(rng)
    weights = [1 / (i + 1) for i in range(len(ips))]      # rozkład Zipfa – są „top” adresy
    moment = start or datetime(2026, 11, 1, tzinfo=timezone(timedelta(hours=1)))
    while True:
        moment += timedelta(seconds=rng.expovariate(1 / 2))
        if rng.random() < 0.01:
            yield rng.choice(MALFORMED)
            continue
        ip = rng.choices(ips, weights)[0]
        ts = moment.strftime("%d/%b/%Y:%H:%M:%S %z")
        status = rng.choice(STATUSES)
        size = "-" if status == 304 else str(rng.randint(120, 250_000))
        yield (
            f'{ip} - - [{ts}] "{rng.choice(METHODS)} {rng.choice(URLS)} HTTP/1.1" '
            f'{status} {size} "-" "{rng.choice(AGENTS)}"'
        )


def write_geoip_db(path: Path, seed: int = 42) -> Path:
    """Zapisuje bazę GeoIP (JSON) pasującą do puli adresów.

    Adresy prywatne są pomijane – serwis GeoIP rozpoznaje je sam (jako "LAN"),
    więc wpis w bazie byłby martwy.
    """
    rng = random.Random(seed)
    prefixes = {ip.rsplit(".", 2)[0] for ip in make_ip_pool(rng) if not is_private_ip(ip)}
    data = {p: dict(zip(("country", "city"), rng.choice(CITIES), strict=True)) for p in sorted(prefixes)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def generate_dataset(out_dir: str | Path, files: int = 8, lines_per_file: int = 100_000, seed: int = 42) -> list[Path]:
    """Tworzy ``files`` plików logów po ``lines_per_file`` linii oraz plik ``geoip.json``."""
    if files < 1 or lines_per_file < 1:
        raise ValueError("files i lines_per_file muszą być dodatnie")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(files):
        path = out / f"access_{i + 1:02d}.log"
        stream = log_line_stream(seed=seed + i)
        with path.open("w", encoding="utf-8", newline="\n") as f:
            f.writelines(line + "\n" for line in islice(stream, lines_per_file))
        paths.append(path)
    write_geoip_db(out / "geoip.json", seed=seed)
    return paths
