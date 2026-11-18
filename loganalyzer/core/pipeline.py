"""Potok (pipeline) generatorów przetwarzających linie logów.

Każdy etap jest generatorem lub wyrażeniem generującym, więc dane przepływają
element po elemencie – plik o rozmiarze kilku GB nie trafia w całości do pamięci::

    read_lines(paths) -> skip_blank(...) -> parse_lines(...) -> filter_entries(...)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loganalyzer.core.patterns import LOG_LINE_RE, is_valid_ip, parse_timestamp
from loganalyzer.core.reader import ChunkReader

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class LogEntry:
    """Jedna poprawnie sparsowana linia logu."""

    ip: str
    timestamp: datetime
    method: str
    url: str
    status: int
    size: int
    agent: str


class MalformedLineError(ValueError):
    """Linia nie pasuje do formatu logu albo zawiera niepoprawne dane."""


def read_lines(paths: Iterable[str | Path], chunk_size: int = 64 * 1024) -> Iterator[str]:
    """Generator: zwraca kolejno linie ze wszystkich podanych plików.

    Deleguje odczyt do iteratora ``ChunkReader`` za pomocą ``yield from``.
    """
    for path in paths:
        with ChunkReader(path, chunk_size=chunk_size) as reader:
            yield from reader


def skip_blank(lines: Iterable[str]) -> Iterator[str]:
    """Pomija puste linie (wyrażenie generujące)."""
    return (line for line in lines if line.strip())


def parse_line(line: str) -> LogEntry:
    """Parsuje pojedynczą linię logu.

    Raises:
        MalformedLineError: gdy linia ma zły format, niepoprawne IP lub datę.
    """
    match = LOG_LINE_RE.match(line)
    if match is None:
        raise MalformedLineError(f"Nieznany format linii: {line[:60]!r}")
    ip = match["ip"]
    if not is_valid_ip(ip):
        raise MalformedLineError(f"Niepoprawny adres IP: {ip!r}")
    try:
        timestamp = parse_timestamp(match["timestamp"])
    except ValueError as exc:
        raise MalformedLineError(str(exc)) from exc
    size = match["size"]
    return LogEntry(
        ip=ip,
        timestamp=timestamp,
        method=match["method"],
        url=match["url"],
        status=int(match["status"]),
        size=0 if size == "-" else int(size),
        agent=match["agent"] or "-",
    )


def parse_lines(
    lines: Iterable[str],
    on_error: Callable[[str, MalformedLineError], None] | None = None,
) -> Iterator[LogEntry]:
    """Generator: zamienia linie tekstu na obiekty ``LogEntry``.

    Niepoprawne linie są pomijane; jeśli podano ``on_error``, jest ona wywoływana
    dla każdej z nich (np. w celu zliczenia błędów).
    """
    for line in lines:
        try:
            yield parse_line(line)
        except MalformedLineError as exc:
            if on_error is not None:
                on_error(line, exc)
            else:
                logger.debug("Pominięto linię: %s", exc)


def filter_entries(
    entries: Iterable[LogEntry],
    *,
    status: int | None = None,
    method: str | None = None,
    url_pattern: str | None = None,
) -> Iterator[LogEntry]:
    """Filtruje wpisy wg kodu HTTP, metody oraz wzorca regex dopasowanego do URL."""
    url_re = re.compile(url_pattern) if url_pattern else None
    return (
        entry
        for entry in entries
        if (status is None or entry.status == status)
        and (method is None or entry.method == method.upper())
        and (url_re is None or url_re.search(entry.url))
    )


def unique_ips(entries: Iterable[LogEntry]) -> Iterator[str]:
    """Generator zwracający adresy IP w kolejności pierwszego wystąpienia (bez powtórzeń)."""
    seen: set[str] = set()
    for entry in entries:
        if entry.ip not in seen:
            seen.add(entry.ip)
            yield entry.ip
