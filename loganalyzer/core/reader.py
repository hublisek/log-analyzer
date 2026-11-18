"""Własny iterator czytający plik blokami (protokół ``__iter__`` / ``__next__``)."""

from __future__ import annotations

import logging
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self

logger = logging.getLogger(__name__)


class ChunkReader:
    """Iterator zwracający kolejne linie pliku, czytanego blokami po ``chunk_size`` bajtów.

    Plik nie jest wczytywany w całości do pamięci – w RAM jest naraz najwyżej
    jeden blok oraz niedokończona linia z poprzedniego bloku. Linia przecięta
    granicą bloku jest sklejana z początkiem następnego bloku.

    Iterator jest jednorazowy (jak każdy iterator): po wyczerpaniu zgłasza
    ``StopIteration`` i zamyka plik. Można go też używać jako menedżera kontekstu.

    Przykład::

        with ChunkReader("access.log") as reader:
            for line in reader:
                ...
    """

    def __init__(self, path: str | Path, chunk_size: int = 64 * 1024, encoding: str = "utf-8") -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size musi być dodatni")
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"Plik nie istnieje: {self.path}")
        self.chunk_size = chunk_size
        self.encoding = encoding
        self._file: BinaryIO | None = self.path.open("rb")
        self._pending: list[bytes] = []   # gotowe linie z bieżącego bloku (odwrócone)
        self._remainder = b""             # niedokończona linia z poprzedniego bloku
        self.chunks_read = 0
        self.lines_returned = 0

    # --- protokół iteratora -------------------------------------------------
    def __iter__(self) -> Self:
        return self

    def __next__(self) -> str:
        while not self._pending:
            if self._file is None:
                raise StopIteration
            chunk = self._file.read(self.chunk_size)
            if not chunk:
                # koniec pliku: oddajemy ostatnią linię bez znaku nowej linii
                self.close()
                if self._remainder:
                    last, self._remainder = self._remainder, b""
                    return self._decode(last)
                raise StopIteration
            self.chunks_read += 1
            parts = (self._remainder + chunk).split(b"\n")
            self._remainder = parts.pop()          # ostatni fragment może być niepełny
            self._pending = parts[::-1]            # odwracamy, by pop() był O(1)
        return self._decode(self._pending.pop())

    # --- pomocnicze ---------------------------------------------------------
    def _decode(self, raw: bytes) -> str:
        self.lines_returned += 1
        return raw.rstrip(b"\r").decode(self.encoding, errors="replace")

    def close(self) -> None:
        """Zamyka plik (bezpieczne przy wielokrotnym wywołaniu)."""
        if self._file is not None:
            self._file.close()
            self._file = None
            logger.debug("%s: przeczytano %d bloków", self.path.name, self.chunks_read)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self.path)!r}, chunk_size={self.chunk_size})"
