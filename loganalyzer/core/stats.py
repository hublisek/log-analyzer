"""Statystyki liczone dla plików logów.

Funkcja ``analyze_file`` jest zadaniem CPU-bound (regex + parsowanie dat dla
każdej linii) i jest zdefiniowana na poziomie modułu, żeby dało się ją
przekazać do ``ProcessPoolExecutor`` (musi być serializowalna przez ``pickle``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from loganalyzer.core.pipeline import MalformedLineError, parse_lines, read_lines, skip_blank


@dataclass
class LogStats:
    """Zagregowane statystyki jednego lub wielu plików logów."""

    files: list[str] = field(default_factory=list)
    total_lines: int = 0
    parsed_lines: int = 0
    malformed_lines: int = 0
    bytes_sent: int = 0
    status_counts: Counter[int] = field(default_factory=Counter)
    method_counts: Counter[str] = field(default_factory=Counter)
    ip_counts: Counter[str] = field(default_factory=Counter)
    url_counts: Counter[str] = field(default_factory=Counter)
    hour_counts: Counter[int] = field(default_factory=Counter)

    # --- właściwości wyliczane ----------------------------------------------
    @property
    def error_rate(self) -> float:
        """Odsetek odpowiedzi z kodem 4xx/5xx."""
        if not self.parsed_lines:
            return 0.0
        errors = sum(n for code, n in self.status_counts.items() if code >= 400)
        return errors / self.parsed_lines

    @property
    def unique_ips(self) -> int:
        return len(self.ip_counts)

    # --- łączenie wyników z wielu procesów/plików ---------------------------
    def merge(self, other: LogStats) -> Self:
        """Dodaje statystyki ``other`` do bieżącego obiektu (in place) i zwraca ``self``."""
        self.files.extend(other.files)
        self.total_lines += other.total_lines
        self.parsed_lines += other.parsed_lines
        self.malformed_lines += other.malformed_lines
        self.bytes_sent += other.bytes_sent
        self.status_counts.update(other.status_counts)
        self.method_counts.update(other.method_counts)
        self.ip_counts.update(other.ip_counts)
        self.url_counts.update(other.url_counts)
        self.hour_counts.update(other.hour_counts)
        return self

    @classmethod
    def combine(cls, parts: list[LogStats]) -> LogStats:
        """Łączy listę wyników cząstkowych w jeden obiekt."""
        total = cls()
        for part in parts:
            total.merge(part)
        return total

    def to_dict(self, top: int = 10) -> dict[str, object]:
        """Reprezentacja do eksportu (JSON)."""
        return {
            "files": self.files,
            "total_lines": self.total_lines,
            "parsed_lines": self.parsed_lines,
            "malformed_lines": self.malformed_lines,
            "bytes_sent": self.bytes_sent,
            "unique_ips": self.unique_ips,
            "error_rate": round(self.error_rate, 4),
            "status_counts": dict(sorted(self.status_counts.items())),
            "method_counts": dict(self.method_counts.most_common()),
            "top_ips": self.ip_counts.most_common(top),
            "top_urls": self.url_counts.most_common(top),
            "hour_counts": dict(sorted(self.hour_counts.items())),
        }


def analyze_file(path: str | Path, chunk_size: int = 64 * 1024) -> LogStats:
    """Liczy statystyki dla pojedynczego pliku (zadanie CPU-bound)."""
    stats = LogStats(files=[str(path)])

    def count_error(_line: str, _exc: MalformedLineError) -> None:
        stats.malformed_lines += 1

    lines = skip_blank(read_lines([path], chunk_size=chunk_size))
    for entry in parse_lines(lines, on_error=count_error):
        stats.parsed_lines += 1
        stats.bytes_sent += entry.size
        stats.status_counts[entry.status] += 1
        stats.method_counts[entry.method] += 1
        stats.ip_counts[entry.ip] += 1
        stats.url_counts[entry.url.split("?", 1)[0]] += 1
        stats.hour_counts[entry.timestamp.hour] += 1
    stats.total_lines = stats.parsed_lines + stats.malformed_lines
    return stats
