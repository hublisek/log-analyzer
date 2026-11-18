"""Punkt wejścia: ``python -m loganalyzer ...``.

Strażnik ``if __name__ == "__main__"`` jest wymagany przez ``multiprocessing``
na Windows/macOS (metoda ``spawn`` importuje moduł główny w każdym procesie).
"""

import sys

from loganalyzer.cli import main

if __name__ == "__main__":
    sys.exit(main())
