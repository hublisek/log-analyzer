"""Lokalna „usługa GeoIP” – przykład zadania I/O-bound przetwarzanego wątkami.

Zamiast prawdziwego API (limity, brak sieci na obronie) używamy lokalnej bazy
JSON i symulujemy opóźnienie sieci przez ``time.sleep``. Podczas ``sleep``
(tak jak podczas prawdziwego oczekiwania na sieć/dysk) wątek zwalnia GIL,
więc wątki dają tu realne przyspieszenie.

Zawiera cztery strategie: sekwencyjną, ręczne wątki + ``Queue`` + ``Lock``,
``ThreadPoolExecutor`` oraz ``asyncio`` (element dodatkowy – porównanie).
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import random
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from loganalyzer.core.patterns import IP_RE, is_private_ip
from loganalyzer.utils.decorators import retry

logger = logging.getLogger(__name__)

UNKNOWN = {"country": "??", "city": "nieznane"}
LAN = {"country": "LAN", "city": "sieć lokalna"}

RETRY_ATTEMPTS = 4      # wspólne dla wersji synchronicznej (@retry) i asynchronicznej
RETRY_DELAY = 0.01


class TransientLookupError(ConnectionError):
    """Chwilowy błąd „połączenia” z bazą (symulowany)."""


@dataclass(frozen=True, slots=True)
class GeoInfo:
    ip: str
    country: str
    city: str


class GeoIPDatabase:
    """Baza adresów: klucz = dwa pierwsze oktety (np. ``"83.12"``)."""

    def __init__(self, path: str | Path | None = None, delay: float = 0.02, fail_rate: float = 0.0) -> None:
        if not 0.0 <= fail_rate < 1.0:
            raise ValueError("fail_rate musi być z przedziału [0, 1)")
        self.delay = delay
        self.fail_rate = fail_rate
        self._data: dict[str, dict[str, str]] = {}
        if path is not None:
            path = Path(path)
            try:
                with path.open(encoding="utf-8") as f:
                    self._data = json.load(f)
            except FileNotFoundError:
                logger.warning("Brak bazy GeoIP %s – wszystkie adresy będą nieznane", path)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Uszkodzony plik bazy GeoIP {path}: {exc}") from exc

    @retry(attempts=RETRY_ATTEMPTS, delay=RETRY_DELAY, exceptions=(TransientLookupError,))
    def query(self, ip: str) -> GeoInfo:
        """Zwraca informacje o adresie (z symulowanym opóźnieniem i błędami)."""
        time.sleep(self.delay)                      # „czekamy na sieć” – GIL zwolniony
        if self.fail_rate and random.random() < self.fail_rate:
            raise TransientLookupError(f"timeout dla {ip}")
        return self._resolve(ip)

    async def query_async(self, ip: str) -> GeoInfo:
        """Wersja asynchroniczna zapytania (dla porównania z wątkami).

        Dekorator ``@retry`` jest synchroniczny (używa ``time.sleep``, który
        zablokowałby pętlę zdarzeń), dlatego ponawianie jest tu zapisane jawnie,
        z tymi samymi parametrami i ``await asyncio.sleep`` między próbami.
        """
        last_error: TransientLookupError | None = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            await asyncio.sleep(self.delay)
            if not (self.fail_rate and random.random() < self.fail_rate):
                return self._resolve(ip)
            last_error = TransientLookupError(f"timeout dla {ip}")
            logger.warning("query_async: próba %d/%d nieudana: %s", attempt, RETRY_ATTEMPTS, last_error)
            if attempt < RETRY_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY)
        raise RuntimeError(f"query_async: wyczerpano limit {RETRY_ATTEMPTS} prób") from last_error

    def _resolve(self, ip: str) -> GeoInfo:
        match = IP_RE.fullmatch(ip)
        if match is None:
            raise ValueError(f"Niepoprawny adres IP: {ip!r}")
        if is_private_ip(ip):
            info = LAN
        else:
            info = self._data.get(f"{match['a']}.{match['b']}", UNKNOWN)
        return GeoInfo(ip=ip, country=info["country"], city=info["city"])


class IpLookupService:
    """Rozwiązuje listę adresów IP różnymi strategiami współbieżności.

    Wszystkie strategie obsługują błędy tak samo: adres, którego nie udało się
    sprawdzić, jest logowany i pomijany w wyniku (reszta jest zwracana).
    """

    def __init__(self, db: GeoIPDatabase) -> None:
        self.db = db

    def lookup_sequential(self, ips: Iterable[str]) -> dict[str, GeoInfo]:
        """Jedno zapytanie po drugim (punkt odniesienia)."""
        return {ip: self.db.query(ip) for ip in ips}

    def lookup_threads(self, ips: Iterable[str], workers: int = 8) -> dict[str, GeoInfo]:
        """Ręcznie tworzone wątki pobierające zadania z ``queue.Queue``.

        Wyniki trafiają do wspólnego słownika chronionego ``threading.Lock``.
        Wątki kończą pracę po odebraniu „pigułki” ``None`` (sentinel).
        """
        tasks: queue.Queue[str | None] = queue.Queue()
        results: dict[str, GeoInfo] = {}
        errors: list[tuple[str, Exception]] = []
        lock = threading.Lock()

        def worker() -> None:
            while True:
                ip = tasks.get()
                try:
                    if ip is None:
                        return
                    info = self.db.query(ip)
                    with lock:                       # sekcja krytyczna
                        results[ip] = info
                except Exception as exc:            # noqa: BLE001 – zbieramy błędy wątków
                    with lock:
                        errors.append((str(ip), exc))
                finally:
                    tasks.task_done()

        threads = [threading.Thread(target=worker, name=f"geoip-{i}", daemon=True) for i in range(workers)]
        for t in threads:
            t.start()
        try:
            for ip in ips:
                tasks.put(ip)
        finally:
            for _ in threads:                       # pigułki także przy błędzie w ``ips``
                tasks.put(None)
        tasks.join()
        for t in threads:
            t.join()

        for ip, exc in errors:
            _log_failure(ip, exc)
        return results

    def lookup_pool(self, ips: Iterable[str], workers: int = 8) -> dict[str, GeoInfo]:
        """To samo z użyciem ``ThreadPoolExecutor`` (mniej kodu, ta sama idea)."""
        results: dict[str, GeoInfo] = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="geoip-pool") as pool:
            futures = [(ip, pool.submit(self.db.query, ip)) for ip in ips]
            for ip, future in futures:              # kolejność taka jak na wejściu
                try:
                    results[ip] = future.result()
                except Exception as exc:            # noqa: BLE001 – jak w lookup_threads
                    _log_failure(ip, exc)
        return results

    def lookup_async(self, ips: Iterable[str], concurrency: int = 8) -> dict[str, GeoInfo]:
        """Wersja ``asyncio`` – jeden wątek, wiele korutyn, limit przez ``Semaphore``."""

        async def run() -> dict[str, GeoInfo]:
            semaphore = asyncio.Semaphore(concurrency)

            async def one(ip: str) -> GeoInfo:
                async with semaphore:
                    return await self.db.query_async(ip)

            ip_list = list(ips)
            # return_exceptions=True: jeden błąd nie przerywa pozostałych korutyn
            infos = await asyncio.gather(*(one(ip) for ip in ip_list), return_exceptions=True)
            results: dict[str, GeoInfo] = {}
            for ip, info in zip(ip_list, infos, strict=True):
                if isinstance(info, Exception):
                    _log_failure(ip, info)
                elif isinstance(info, BaseException):   # np. CancelledError – nie tłumimy
                    raise info
                else:
                    results[ip] = info
            return results

        return asyncio.run(run())


def _log_failure(ip: str, exc: BaseException) -> None:
    logger.error("Nie udało się sprawdzić %s: %s", ip, exc)
