"""Własne dekoratory projektu.

Zawiera:
    * measure_time         – dekorator funkcji bez parametrów (pomiar czasu),
    * retry(...)           – dekorator funkcji z parametrami (ponawianie operacji),
    * log_call(level=...)  – dekorator funkcji z parametrem (logowanie wywołań),
    * requires_config      – dekorator METODY korzystający z ``self``.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

logger = logging.getLogger(__name__)


def measure_time(func: Callable[P, R]) -> Callable[P, R]:
    """Mierzy czas wykonania funkcji i zapisuje go w logu (poziom INFO).

    Ostatni zmierzony czas jest dostępny jako atrybut ``wrapper.last_elapsed``,
    co wykorzystuje m.in. benchmark.
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - start
            wrapper.last_elapsed = elapsed  # type: ignore[attr-defined]
            logger.info("[%s] wykonano w %.4f s", func.__name__, elapsed)

    wrapper.last_elapsed = 0.0  # type: ignore[attr-defined]
    return wrapper


def retry(
    attempts: int = 3,
    delay: float = 0.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Fabryka dekoratorów ponawiających wywołanie po wystąpieniu wyjątku.

    Args:
        attempts: maksymalna liczba prób (>= 1).
        delay: opóźnienie w sekundach pomiędzy próbami.
        exceptions: krotka wyjątków, przy których ponawiamy próbę.

    Raises:
        ValueError: gdy ``attempts`` < 1.
        RuntimeError: gdy wszystkie próby zakończą się błędem
            (oryginalny wyjątek dostępny w ``__cause__``).
    """
    if attempts < 1:
        raise ValueError("attempts musi być >= 1")

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            last_error: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_error = exc
                    logger.warning(
                        "%s: próba %d/%d nieudana: %s",
                        func.__name__, attempt, attempts, exc,
                    )
                    if delay and attempt < attempts:
                        time.sleep(delay)
            raise RuntimeError(
                f"{func.__name__}: wyczerpano limit {attempts} prób"
            ) from last_error

        return wrapper

    return decorator


def log_call(level: int = logging.DEBUG) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Fabryka dekoratorów logujących wywołanie funkcji i jej wynik."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            logger.log(level, "-> %s args=%r kwargs=%r", func.__name__, args, kwargs)
            result = func(*args, **kwargs)
            logger.log(level, "<- %s zwrócił %s", func.__name__, type(result).__name__)
            return result

        return wrapper

    return decorator


def requires_config(method: Callable[..., R]) -> Callable[..., R]:
    """Dekorator metody: blokuje wywołanie, jeśli obiekt nie ma poprawnej konfiguracji.

    Korzysta ze stanu obiektu (``self.config``), więc ``self`` jest przyjmowany
    jawnie jako pierwszy argument wrappera.
    """

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> R:
        config = getattr(self, "config", None)
        if config is None:
            raise RuntimeError(
                f"{type(self).__name__}.{method.__name__}: brak konfiguracji"
            )
        if not config.input_files:
            raise RuntimeError(
                f"{type(self).__name__}.{method.__name__}: brak plików wejściowych"
            )
        return method(self, *args, **kwargs)

    return wrapper
