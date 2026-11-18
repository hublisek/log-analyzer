"""Interfejs wiersza poleceń (argparse).

Przykłady::

    python -m loganalyzer generate --files 8 --lines 100000
    python -m loganalyzer analyze --config analyzer.conf --mode processes --export-json out/report.json
    python -m loganalyzer search sample_data/*.log --status 404 --url-regex "^/api/"
    python -m loganalyzer benchmark
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path

from loganalyzer.config import AnalyzerConfig, ConfigError
from loganalyzer.core.analyzer import LogAnalyzer, Mode
from loganalyzer.core.pipeline import filter_entries, parse_lines, read_lines, skip_blank
from loganalyzer.data.generator import generate_dataset
from loganalyzer.report import export_csv, export_json, format_report

logger = logging.getLogger("loganalyzer")


def setup_logging(verbosity: int) -> None:
    level = logging.WARNING - 10 * verbosity          # -v -> INFO, -vv -> DEBUG
    logging.basicConfig(
        level=max(level, logging.DEBUG),
        format="%(asctime)s %(levelname)-7s %(threadName)-12s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_generate(args: argparse.Namespace) -> int:
    paths = generate_dataset(args.out, files=args.files, lines_per_file=args.lines, seed=args.seed)
    print(f"Wygenerowano {len(paths)} plików po {args.lines} linii w {args.out}/ (+ geoip.json)")
    return 0


def build_config(args: argparse.Namespace) -> AnalyzerConfig:
    if args.config:
        config = AnalyzerConfig.from_file(args.config)
    else:
        config = AnalyzerConfig.from_directory(args.input_dir, args.pattern)
        config.geoip_db = Path(args.input_dir) / "geoip.json"
    if args.workers:
        config.workers = args.workers
    return config


def cmd_analyze(args: argparse.Namespace) -> int:
    config = build_config(args)
    analyzer = LogAnalyzer(config)
    stats = analyzer.analyze(args.mode)
    elapsed = LogAnalyzer.analyze.last_elapsed  # type: ignore[attr-defined]
    top_ips = None if args.no_geoip else analyzer.resolve_top_ips(stats)
    print(format_report(stats, top_ips, top=config.top))
    print(f"Czas analizy ({args.mode}): {elapsed:.3f} s")

    # eksport kilku formatów naraz – zapisy do plików to I/O, więc wątki
    jobs = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        if args.export_json:
            jobs.append(pool.submit(export_json, stats, args.export_json, config.top))
        if args.export_csv:
            jobs.append(pool.submit(export_csv, stats, args.export_csv))
    for job in jobs:
        print(f"Zapisano: {job.result()}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    entries = parse_lines(skip_blank(read_lines(args.files)))
    found = filter_entries(entries, status=args.status, method=args.method, url_pattern=args.url_regex)
    count = 0
    for entry in islice(found, args.limit):
        count += 1
        print(f"{entry.timestamp:%Y-%m-%d %H:%M:%S}  {entry.ip:<16} {entry.method:<6} {entry.status}  {entry.url}")
    print(f"-- pokazano {count} wyników (limit {args.limit})")
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    from benchmarks.gil_benchmark import main as bench_main   # import leniwy – opcjonalny matplotlib
    return bench_main(["--data", args.input_dir, "--repeats", str(args.repeats)])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loganalyzer", description="Analizator logów serwera WWW")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="więcej logów (-v, -vv)")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="wygeneruj dane testowe")
    gen.add_argument("--out", default="sample_data")
    gen.add_argument("--files", type=int, default=8)
    gen.add_argument("--lines", type=int, default=100_000)
    gen.add_argument("--seed", type=int, default=42)
    gen.set_defaults(func=cmd_generate)

    ana = sub.add_parser("analyze", help="przeanalizuj pliki logów")
    ana.add_argument("--config", help="plik konfiguracyjny (klucz = wartość)")
    ana.add_argument("--input-dir", default="sample_data")
    ana.add_argument("--pattern", default="*.log")
    ana.add_argument("--mode", choices=[m.value for m in Mode], default=Mode.PROCESSES.value)
    ana.add_argument("--workers", type=int)
    ana.add_argument("--no-geoip", action="store_true", help="pomiń sprawdzanie adresów IP")
    ana.add_argument("--export-json")
    ana.add_argument("--export-csv")
    ana.set_defaults(func=cmd_analyze)

    srch = sub.add_parser("search", help="wyszukaj wpisy w logach")
    srch.add_argument("files", nargs="+", type=Path)
    srch.add_argument("--status", type=int)
    srch.add_argument("--method")
    srch.add_argument("--url-regex")
    srch.add_argument("--limit", type=int, default=20)
    srch.set_defaults(func=cmd_search)

    bench = sub.add_parser("benchmark", help="pomiar sekwencyjnie / wątki / procesy (GIL)")
    bench.add_argument("--input-dir", default="sample_data")
    bench.add_argument("--repeats", type=int, default=3)
    bench.set_defaults(func=cmd_benchmark)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    try:
        return args.func(args)
    except (ConfigError, OSError, ValueError, RuntimeError) as exc:   # OSError: brak pliku, brak uprawnień
        logger.debug("Szczegóły błędu", exc_info=True)
        print(f"Błąd: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Przerwano.", file=sys.stderr)
        return 130
