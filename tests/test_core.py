"""Testy jednostkowe (pytest)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from loganalyzer.config import AnalyzerConfig, ConfigError
from loganalyzer.core.analyzer import LogAnalyzer
from loganalyzer.core.patterns import LOG_LINE_RE, is_private_ip, is_valid_ip, parse_timestamp
from loganalyzer.core.pipeline import (
    MalformedLineError, filter_entries, parse_line, parse_lines, read_lines, skip_blank, unique_ips,
)
from loganalyzer.core.reader import ChunkReader
from loganalyzer.core.stats import LogStats, analyze_file
from loganalyzer.data.generator import generate_dataset
from loganalyzer.services.geoip import GeoIPDatabase, IpLookupService
from loganalyzer.utils.decorators import measure_time, requires_config, retry

LINE = '83.12.4.200 - - [18/Nov/2026:20:22:01 +0100] "GET /index.html?x=1 HTTP/1.1" 404 5123 "-" "curl/8.5.0"'


# --- regex ------------------------------------------------------------------
def test_log_line_named_groups() -> None:
    m = LOG_LINE_RE.match(LINE)
    assert m is not None
    assert m["ip"] == "83.12.4.200"
    assert m["method"] == "GET"
    assert m["status"] == "404"
    assert m["agent"] == "curl/8.5.0"


@pytest.mark.parametrize("ip, ok", [("0.0.0.0", True), ("255.255.255.255", True),
                                     ("256.1.1.1", False), ("1.2.3", False), ("01a.2.3.4", False)])
def test_ip_validation(ip: str, ok: bool) -> None:
    assert is_valid_ip(ip) is ok


def test_private_ip() -> None:
    assert is_private_ip("192.168.1.5") and is_private_ip("172.20.0.1") and is_private_ip("10.1.2.3")
    assert not is_private_ip("172.32.0.1")


def test_parse_timestamp() -> None:
    dt = parse_timestamp("18/Nov/2026:20:22:01 +0100")
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 11, 18, 20)
    assert dt.utcoffset().total_seconds() == 3600
    with pytest.raises(ValueError):
        parse_timestamp("18/Foo/2026:20:22:01 +0100")


# --- iterator ---------------------------------------------------------------
@pytest.mark.parametrize("chunk_size", [1, 7, 64, 4096])
def test_chunk_reader_handles_boundaries(tmp_path: Path, chunk_size: int) -> None:
    path = tmp_path / "a.log"
    lines = [f"linia {i} " + "x" * i for i in range(50)]
    path.write_text("\n".join(lines), encoding="utf-8")     # brak \n na końcu
    with ChunkReader(path, chunk_size=chunk_size) as reader:
        assert list(reader) == lines
    with pytest.raises(StopIteration):
        next(reader)                                          # iterator jest jednorazowy


def test_chunk_reader_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ChunkReader(tmp_path / "brak.log")
    (tmp_path / "x.log").write_text("a")
    with pytest.raises(ValueError):
        ChunkReader(tmp_path / "x.log", chunk_size=0)


# --- generatory -------------------------------------------------------------
def test_pipeline(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text(f"{LINE}\n\nśmieci\n{LINE.replace('404', '200')}\n", encoding="utf-8")
    errors: list[str] = []
    entries = list(parse_lines(skip_blank(read_lines([path])), on_error=lambda line, _e: errors.append(line)))
    assert len(entries) == 2 and errors == ["śmieci"]
    assert [e.status for e in filter_entries(entries, status=404)] == [404]
    assert len(list(filter_entries(entries, url_pattern=r"^/index"))) == 2
    assert list(unique_ips(entries)) == ["83.12.4.200"]


def test_parse_line_rejects_bad_ip() -> None:
    with pytest.raises(MalformedLineError):
        parse_line(LINE.replace("83.12.4.200", "999.1.1.1"))


# --- dekoratory -------------------------------------------------------------
def test_retry_succeeds_after_failures() -> None:
    calls = {"n": 0}

    @retry(attempts=3, exceptions=(ConnectionError,))
    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("x")
        return "ok"

    assert flaky() == "ok" and calls["n"] == 3
    assert flaky.__name__ == "flaky"                          # functools.wraps


def test_retry_gives_up() -> None:
    @retry(attempts=2, exceptions=(ConnectionError,))
    def always_fails() -> None:
        raise ConnectionError("x")

    with pytest.raises(RuntimeError) as info:
        always_fails()
    assert isinstance(info.value.__cause__, ConnectionError)


def test_measure_time_logs(caplog: pytest.LogCaptureFixture) -> None:
    @measure_time
    def work() -> int:
        return 42

    with caplog.at_level(logging.INFO):
        assert work() == 42
    assert "[work] wykonano" in caplog.text
    assert work.last_elapsed >= 0                             # type: ignore[attr-defined]


def test_requires_config() -> None:
    class Dummy:
        config = None

        @requires_config
        def run(self) -> str:
            return "ok"

    with pytest.raises(RuntimeError, match="brak konfiguracji"):
        Dummy().run()
    d = Dummy()
    d.config = AnalyzerConfig(input_files=[Path("x.log")])
    assert d.run() == "ok"


# --- konfiguracja (@property, @classmethod) ---------------------------------
def test_config_validation() -> None:
    cfg = AnalyzerConfig()
    with pytest.raises(ConfigError):
        cfg.workers = 0
    with pytest.raises(ConfigError):
        cfg.chunk_size = 10
    assert 1 <= AnalyzerConfig.default_workers() <= 8


@pytest.mark.parametrize("attr, value", [("workers", True), ("top", 0), ("top", 5000),
                                         ("lookup_delay", -1), ("lookup_delay", float("nan")),
                                         ("lookup_delay", "x")])
def test_config_rejects_bad_values(attr: str, value: object) -> None:
    with pytest.raises(ConfigError):
        setattr(AnalyzerConfig(), attr, value)


def test_config_from_file(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "a.log").write_text(LINE)
    conf = tmp_path / "a.conf"
    conf.write_text("# test\ninput_dir = logs\nworkers = 3  # komentarz\n", encoding="utf-8")
    cfg = AnalyzerConfig.from_file(conf)
    assert cfg.workers == 3 and len(cfg.input_files) == 1
    conf.write_text("to nie jest klucz\n")
    with pytest.raises(ConfigError):
        AnalyzerConfig.from_file(conf)


# --- statystyki, wątki, procesy ---------------------------------------------
@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    return generate_dataset(tmp_path_factory.mktemp("data"), files=3, lines_per_file=2_000)


def test_all_modes_give_same_result(dataset: list[Path]) -> None:
    analyzer = LogAnalyzer(AnalyzerConfig(input_files=dataset, workers=2))
    results = [analyzer.analyze(mode).to_dict() for mode in ("sequential", "threads", "processes")]
    assert results[0] == results[1] == results[2]
    assert results[0]["total_lines"] == 6_000
    assert 0 < results[0]["malformed_lines"] < 200


def test_stats_merge(dataset: list[Path]) -> None:
    parts = [analyze_file(p) for p in dataset]
    total = LogStats.combine(parts)
    assert total.parsed_lines == sum(p.parsed_lines for p in parts)
    assert 0 <= total.error_rate <= 1


def test_ip_lookup_strategies_agree(dataset: list[Path]) -> None:
    db = GeoIPDatabase(dataset[0].parent / "geoip.json", delay=0.001)
    service = IpLookupService(db)
    ips = ["83.12.1.1", "192.168.1.10", "1.1.1.1", "89.64.2.2"]
    expected = service.lookup_sequential(ips)
    assert service.lookup_threads(ips, workers=3) == expected
    assert service.lookup_pool(ips, workers=3) == expected
    assert service.lookup_async(ips, concurrency=3) == expected
    assert expected["192.168.1.10"].country == "LAN"
    assert expected["1.1.1.1"].country == "??"


def test_ip_lookup_with_transient_failures() -> None:
    db = GeoIPDatabase(delay=0.0, fail_rate=0.3)              # @retry ponawia nieudane zapytania
    service = IpLookupService(db)
    ips = [f"10.0.0.{i}" for i in range(1, 30)]
    # wszystkie strategie pomijają adresy, dla których wyczerpano 4 próby, zamiast przerywać całość
    for results in (service.lookup_threads(ips, workers=4), service.lookup_pool(ips, workers=4),
                    service.lookup_async(ips, concurrency=4)):
        assert len(results) >= 20 and set(results) <= set(ips)


def test_ip_lookup_skips_invalid_ip() -> None:
    service = IpLookupService(GeoIPDatabase(delay=0.0))
    ips = ["10.0.0.1", "999.0.0.1"]
    for results in (service.lookup_threads(ips, workers=2), service.lookup_pool(ips, workers=2),
                    service.lookup_async(ips, concurrency=2)):
        assert list(results) == ["10.0.0.1"]
