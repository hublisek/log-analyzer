"""Konfiguracja analizatora (dekoratory metod: @property z setterem, @classmethod, @staticmethod)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from loganalyzer.core.patterns import CONFIG_LINE_RE

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Błąd w pliku konfiguracyjnym lub niepoprawna wartość ustawienia."""


class AnalyzerConfig:
    """Ustawienia analizatora z walidacją przy przypisaniu.

    Przykładowy plik konfiguracyjny (``analyzer.conf``)::

        # komentarz
        input_dir   = sample_data
        pattern     = *.log
        workers     = 4
        chunk_size  = 65536
        geoip_db    = sample_data/geoip.json
        lookup_delay = 0.02
        top         = 10
    """

    def __init__(
        self,
        input_files: list[Path] | None = None,
        workers: int | None = None,
        chunk_size: int = 64 * 1024,
        geoip_db: Path | None = None,
        lookup_delay: float = 0.02,
        top: int = 10,
    ) -> None:
        self.input_files = list(input_files or [])
        self.workers = workers if workers is not None else self.default_workers()
        self.chunk_size = chunk_size
        self.geoip_db = geoip_db
        self.lookup_delay = lookup_delay
        self.top = top

    # --- właściwości z walidacją --------------------------------------------
    @property
    def workers(self) -> int:
        """Liczba wątków/procesów roboczych (1–64)."""
        return self._workers

    @workers.setter
    def workers(self, value: int) -> None:
        # bool jest podklasą int, więc True/False trzeba odrzucić jawnie
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 64:
            raise ConfigError(f"workers musi być liczbą całkowitą 1–64, podano {value!r}")
        self._workers = value

    @property
    def chunk_size(self) -> int:
        """Rozmiar bloku odczytu w bajtach (1 KiB – 64 MiB)."""
        return self._chunk_size

    @chunk_size.setter
    def chunk_size(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 64 * 1024 * 1024:
            raise ConfigError(f"chunk_size poza zakresem 1 KiB – 64 MiB: {value!r}")
        self._chunk_size = value

    @property
    def lookup_delay(self) -> float:
        """Symulowane opóźnienie zapytania do bazy GeoIP (sekundy)."""
        return self._lookup_delay

    @lookup_delay.setter
    def lookup_delay(self, value: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"lookup_delay musi być liczbą, podano {value!r}")
        if not value >= 0:  # łapie też NaN
            raise ConfigError(f"lookup_delay nie może być ujemne, podano {value!r}")
        self._lookup_delay = float(value)

    @property
    def top(self) -> int:
        """Liczba pozycji w rankingach (top IP / URL), 1–1000."""
        return self._top

    @top.setter
    def top(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
            raise ConfigError(f"top musi być liczbą całkowitą 1–1000, podano {value!r}")
        self._top = value

    # --- metody klasy / statyczne -------------------------------------------
    @staticmethod
    def default_workers() -> int:
        """Domyślna liczba workerów = liczba rdzeni CPU (maks. 8)."""
        return min(os.cpu_count() or 1, 8)

    @classmethod
    def from_directory(cls, directory: str | Path, pattern: str = "*.log", **kwargs: object) -> AnalyzerConfig:
        """Alternatywny konstruktor: wszystkie pliki pasujące do ``pattern`` w katalogu."""
        directory = Path(directory)
        if not directory.is_dir():
            raise ConfigError(f"Katalog nie istnieje: {directory}")
        files = sorted(directory.glob(pattern))
        return cls(input_files=files, **kwargs)  # type: ignore[arg-type]

    @classmethod
    def from_file(cls, path: str | Path) -> AnalyzerConfig:
        """Alternatywny konstruktor: wczytuje plik ``klucz = wartość`` (parsowany regexem)."""
        path = Path(path)
        raw: dict[str, str] = {}
        with path.open(encoding="utf-8") as f:
            for number, line in enumerate(f, start=1):
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                match = CONFIG_LINE_RE.match(line)
                if match is None:
                    raise ConfigError(f"{path}:{number}: niepoprawna linia: {line.strip()!r}")
                raw[match["key"]] = match["value"]

        base = path.parent
        try:
            input_dir = base / raw.pop("input_dir", ".")
            pattern = raw.pop("pattern", "*.log")
            kwargs: dict[str, object] = {}
            if "workers" in raw:
                kwargs["workers"] = int(raw.pop("workers"))
            if "chunk_size" in raw:
                kwargs["chunk_size"] = int(raw.pop("chunk_size"))
            if "geoip_db" in raw:
                kwargs["geoip_db"] = base / raw.pop("geoip_db")
            if "lookup_delay" in raw:
                kwargs["lookup_delay"] = float(raw.pop("lookup_delay"))
            if "top" in raw:
                kwargs["top"] = int(raw.pop("top"))
        except ValueError as exc:
            raise ConfigError(f"{path}: niepoprawna wartość liczbowa: {exc}") from exc
        for unknown in raw:
            logger.warning("%s: nieznany klucz konfiguracji %r – pominięto", path, unknown)
        return cls.from_directory(input_dir, pattern, **kwargs)

    def __repr__(self) -> str:
        return (
            f"AnalyzerConfig(files={len(self.input_files)}, workers={self.workers}, "
            f"chunk_size={self.chunk_size}, geoip_db={self.geoip_db})"
        )
