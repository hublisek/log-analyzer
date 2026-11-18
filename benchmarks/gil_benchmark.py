"""Pomiar porównawczy: sekwencyjnie / wątki / procesy (+ asyncio) – wpływ GIL.

Dwa rodzaje obciążenia:
    1. CPU-bound – analiza plików logów (regex + parsowanie dat w czystym Pythonie),
    2. I/O-bound – zapytania do bazy GeoIP z symulowanym opóźnieniem sieci.

Uruchomienie (z katalogu głównego projektu)::

    python -m benchmarks.gil_benchmark --data sample_data --repeats 3

Wyniki: tabela w konsoli, ``benchmarks/results.md``, ``benchmarks/results.json``
oraz wykres ``benchmarks/results.png`` (jeśli zainstalowano matplotlib).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from loganalyzer.config import AnalyzerConfig
from loganalyzer.core.analyzer import LogAnalyzer
from loganalyzer.services.geoip import GeoIPDatabase, IpLookupService

OUT_DIR = Path(__file__).resolve().parent


def measure(func: Callable[[], object], repeats: int) -> tuple[float, float]:
    """Uruchamia ``func`` ``repeats`` razy; zwraca (najlepszy czas, mediana)."""
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        func()
        times.append(time.perf_counter() - start)
    return min(times), statistics.median(times)


def gil_status() -> str:
    """Informacja, czy interpreter działa z GIL (Python 3.13+ ma ``sys._is_gil_enabled``)."""
    check = getattr(sys, "_is_gil_enabled", None)
    if check is None:
        return "włączony (Python < 3.13)"
    return "włączony" if check() else "WYŁĄCZONY (free-threaded)"


def _lookup_in_process(args: tuple[str, float]) -> str:
    """Pomocnicza funkcja dla procesów (musi być na poziomie modułu dla pickle)."""
    ip, delay = args
    return GeoIPDatabase(delay=delay).query(ip).country


def run(data_dir: Path, repeats: int, workers: int, io_workers: int, ip_count: int, delay: float) -> list[dict[str, object]]:
    config = AnalyzerConfig.from_directory(data_dir, workers=workers)
    if not config.input_files:
        raise SystemExit(f"Brak plików *.log w {data_dir} – uruchom najpierw: python -m loganalyzer generate")
    analyzer = LogAnalyzer(config)
    results: list[dict[str, object]] = []

    def record(workload: str, variant: str, best: float, median: float) -> None:
        results.append({"workload": workload, "variant": variant, "best_s": round(best, 4), "median_s": round(median, 4)})
        print(f"  {workload:<10} {variant:<22} best={best:7.3f} s   median={median:7.3f} s")

    print(f"CPU-bound: analiza {len(config.input_files)} plików, workers={workers}")
    for mode in ("sequential", "threads", "processes"):
        best, median = measure(lambda m=mode: analyzer.analyze(m), repeats)
        record("CPU", mode, best, median)

    db = GeoIPDatabase(data_dir / "geoip.json", delay=delay)
    service = IpLookupService(db)
    ips = [f"83.12.{i // 250}.{i % 250 + 1}" for i in range(ip_count)]
    print(f"I/O-bound: {ip_count} zapytań GeoIP po {delay * 1000:.0f} ms, workers={io_workers}")
    variants: dict[str, Callable[[], object]] = {
        "sequential": lambda: service.lookup_sequential(ips),
        "threads (Queue+Lock)": lambda: service.lookup_threads(ips, io_workers),
        "ThreadPoolExecutor": lambda: service.lookup_pool(ips, io_workers),
        "asyncio": lambda: service.lookup_async(ips, io_workers),
    }

    def with_processes() -> None:
        with ProcessPoolExecutor(max_workers=io_workers) as pool:
            list(pool.map(_lookup_in_process, [(ip, delay) for ip in ips], chunksize=8))

    variants["processes"] = with_processes
    for name, func in variants.items():
        best, median = measure(func, repeats)
        record("I/O", name, best, median)
    return results


def add_speedups(results: list[dict[str, object]]) -> None:
    """Dopisuje przyspieszenie względem wariantu sekwencyjnego w tej samej kategorii."""
    base = {r["workload"]: r["best_s"] for r in results if r["variant"] == "sequential"}
    for r in results:
        r["speedup"] = round(float(base[r["workload"]]) / float(r["best_s"]), 2)


def to_markdown(results: list[dict[str, object]], meta: dict[str, str]) -> str:
    header = "| Obciążenie | Wariant | Najlepszy czas [s] | Mediana [s] | Przyspieszenie |\n|---|---|---:|---:|---:|\n"
    rows = "".join(
        f"| {r['workload']} | {r['variant']} | {r['best_s']:.3f} | {r['median_s']:.3f} | {r['speedup']:.2f}× |\n"
        for r in results
    )
    info = "\n".join(f"- **{k}:** {v}" for k, v in meta.items())
    return f"{info}\n\n{header}{rows}"


def plot(results: list[dict[str, object]], path: Path) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("(matplotlib nie jest zainstalowany – pomijam wykres)")
        return None
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, workload, title in zip(axes, ("CPU", "I/O"), ("CPU-bound: analiza logów", "I/O-bound: zapytania GeoIP"), strict=True):
        rows = [r for r in results if r["workload"] == workload]
        names = [str(r["variant"]) for r in rows]
        values = [float(r["best_s"]) for r in rows]
        bars = ax.barh(names, values, color="#4C72B0")
        ax.bar_label(bars, labels=[f"{v:.2f} s" for v in values], padding=3, fontsize=9)
        ax.invert_yaxis()
        ax.set_title(title)
        ax.set_xlabel("czas [s] – mniej = lepiej")
        ax.margins(x=0.2)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="sample_data", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", type=int, default=AnalyzerConfig.default_workers())
    parser.add_argument("--io-workers", type=int, default=16, help="wątki/procesy dla zadania I/O")
    parser.add_argument("--ips", type=int, default=200, help="liczba zapytań GeoIP")
    parser.add_argument("--delay", type=float, default=0.01, help="opóźnienie jednego zapytania [s]")
    args = parser.parse_args(argv)

    meta = {
        "Python": f"{platform.python_implementation()} {platform.python_version()}",
        "GIL": gil_status(),
        "System": f"{platform.system()} {platform.release()}",
        "Rdzenie CPU": str(os.cpu_count()),
        "Workers (CPU / I/O)": f"{args.workers} / {args.io_workers}",
        "Powtórzenia": str(args.repeats),
    }
    print(" | ".join(f"{k}: {v}" for k, v in meta.items()))
    results = run(args.data, args.repeats, args.workers, args.io_workers, args.ips, args.delay)
    add_speedups(results)

    (OUT_DIR / "results.json").write_text(json.dumps({"meta": meta, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "results.md").write_text(to_markdown(results, meta), encoding="utf-8")
    chart = plot(results, OUT_DIR / "results.png")
    print(f"\nZapisano: {OUT_DIR / 'results.md'}, {OUT_DIR / 'results.json'}" + (f", {chart}" if chart else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
