"""Główna klasa analizatora – łączy parsowanie (procesy) i GeoIP (wątki)."""

from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from enum import StrEnum
from itertools import repeat

from loganalyzer.config import AnalyzerConfig
from loganalyzer.core.stats import LogStats, analyze_file
from loganalyzer.services.geoip import GeoInfo, GeoIPDatabase, IpLookupService
from loganalyzer.utils.decorators import log_call, measure_time, requires_config

logger = logging.getLogger(__name__)


class Mode(StrEnum):
    """Strategia przetwarzania plików."""

    SEQUENTIAL = "sequential"
    THREADS = "threads"
    PROCESSES = "processes"


class LogAnalyzer:
    """Analizuje wiele plików logów i wzbogaca najaktywniejsze adresy o dane GeoIP."""

    def __init__(self, config: AnalyzerConfig) -> None:
        self.config = config

    @measure_time          # zewnętrzny – mierzy czas całości i zapamiętuje last_elapsed
    @requires_config       # wewnętrzny – sprawdza konfigurację przed analizą
    def analyze(self, mode: Mode | str = Mode.PROCESSES) -> LogStats:
        """Liczy statystyki dla wszystkich plików z konfiguracji.

        * ``sequential`` – pliki jeden po drugim,
        * ``threads``    – ``ThreadPoolExecutor`` (przez GIL brak zysku dla CPU-bound),
        * ``processes``  – ``ProcessPoolExecutor`` (prawdziwa równoległość).
        """
        mode = Mode(mode)
        files = [str(p) for p in self.config.input_files]
        chunk = self.config.chunk_size
        workers = min(self.config.workers, len(files))
        logger.info("Analiza %d plików, tryb=%s, workers=%d", len(files), mode, workers)

        if mode is Mode.SEQUENTIAL:
            parts = [analyze_file(f, chunk) for f in files]
        elif mode is Mode.THREADS:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                parts = list(pool.map(analyze_file, files, repeat(chunk)))
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                parts = list(pool.map(analyze_file, files, repeat(chunk)))
        return LogStats.combine(parts)

    @requires_config
    @log_call(level=logging.DEBUG)
    def resolve_top_ips(self, stats: LogStats, limit: int | None = None) -> list[tuple[str, int, GeoInfo]]:
        """Sprawdza w bazie GeoIP najczęściej występujące adresy (wątki)."""
        limit = limit or self.config.top
        top = stats.ip_counts.most_common(limit)
        db = GeoIPDatabase(self.config.geoip_db, delay=self.config.lookup_delay)
        service = IpLookupService(db)
        infos = service.lookup_threads((ip for ip, _ in top), workers=self.config.workers)
        return [(ip, count, infos[ip]) for ip, count in top if ip in infos]
