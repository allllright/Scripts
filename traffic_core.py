"""Traffic generator core module.

This module provides a configurable asynchronous traffic generator for HTTP APIs.
It is designed for long running use, integrates error injection, and produces
periodic metrics summaries for observability and burn-rate testing scenarios.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import signal
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import aiohttp

try:
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - dependency optional at import time
    yaml = None

LOGGER = logging.getLogger("traffic_generator")

# Known restaurant identifiers used by the FoodMe demo API
RESTAURANT_IDS = [
    "esthers",
    "robatayaki",
    "tofuparadise",
    "bateaurouge",
    "khartoum",
    "sallys",
    "saucy",
    "czechpoint",
    "speisewagen",
    "beijing",
    "satay",
    "cancun",
    "curryup",
    "carthage",
    "burgerama",
    "littlepigs",
    "littleprague",
    "kohlhaus",
    "dragon",
    "babythai",
    "wholetamale",
    "bhangra",
    "taqueria",
    "pedros",
    "superwonton",
    "naansequitur",
    "sakura",
    "shandong",
    "currygalore",
    "north",
    "beans",
    "jeeves",
    "zardoz",
    "angular",
    "flavia",
    "luigis",
    "thick",
    "wheninrome",
    "pizza76",
]


def _normalize_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Return a copy of headers or an empty dict if None."""

    return dict(headers or {})


def build_valid_order() -> Dict[str, Any]:
    """Return a realistic valid order payload for the FoodMe API."""

    return {
        "items": [
            {"name": "Pizza", "qty": random.randint(1, 3)},
            {"name": "Salad", "qty": 1},
        ],
        "deliverTo": {"name": "Test User"},
        "restaurant": {"name": "Demo"},
    }


def build_invalid_order() -> Any:
    """Return intentionally malformed order payloads."""

    malformed_payloads: Iterable[Any] = (
        {"items": []},  # empty list - validation failure
        {"deliverTo": {}},  # missing nested fields
        "{ this is not valid json }",  # bogus text
        {"items": [{"qty": "not-an-int"}]},  # wrong types
        None,  # missing body entirely
    )
    return random.choice(tuple(malformed_payloads))


@dataclass
class TrafficConfig:
    """Configuration holder for the traffic generator."""

    target: str
    rps: float = 1.0
    traffic_type: str = "mixed"
    weights: Dict[str, float] = field(default_factory=dict)
    error_rates: Dict[str, float] = field(default_factory=dict)
    timeout_seconds: float = 5.0
    summary_interval: float = 30.0
    headers: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.rps <= 0:
            raise ValueError("rps must be a positive number")
        if not self.weights:
            raise ValueError("weights must contain at least one endpoint")

        total_weight = sum(self.weights.values())
        if total_weight <= 0:
            raise ValueError("weights must sum to a positive number")

        if self.summary_interval < 0:
            raise ValueError("summary_interval cannot be negative")

        self._choices = list(self.weights.keys())
        self._weight_probabilities = [w / total_weight for w in self.weights.values()]

        # Expand the target into a full base URL if only host:port provided
        if self.target.startswith("http://") or self.target.startswith("https://"):
            base = self.target
        else:
            base = f"http://{self.target}"
        self.base_url = base.rstrip("/")

        # Default headers that callers can override/extend
        merged_headers = {
            "User-Agent": f"foodme-{self.traffic_type}-load",
            "X-Traffic-Type": self.traffic_type,
        }
        merged_headers.update(self.headers)
        self.headers = merged_headers

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "TrafficConfig":
        """Build a config object from a plain dict."""

        return cls(**raw)

    def choose_endpoint(self) -> str:
        """Randomly select the next endpoint based on weights."""

        return random.choices(self._choices, weights=self._weight_probabilities, k=1)[0]

    def error_probability(self, endpoint: str) -> float:
        return float(self.error_rates.get(endpoint, 0.0))


class TrafficGenerator:
    """Asynchronous traffic generator implementing the request loop."""

    def __init__(self, config: TrafficConfig) -> None:
        self.config = config
        self.metrics: Counter[str] = Counter()
        self.exception_counts: Counter[str] = Counter()
        self.total_requests: int = 0
        self._stop_event = asyncio.Event()
        self._session: Optional[aiohttp.ClientSession] = None

    async def run(self, duration: Optional[float] = None) -> None:
        """Run the traffic generator until duration elapses or a signal stops it."""

        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            self._session = session
            loop = asyncio.get_running_loop()
            _install_signal_handlers(loop, self._stop_event)

            LOGGER.info(
                "Starting %s traffic at %.2f rps against %s",
                self.config.traffic_type,
                self.config.rps,
                self.config.base_url,
            )

            start = time.monotonic()
            next_summary = start + self.config.summary_interval if self.config.summary_interval else None
            interval = 1.0 / self.config.rps

            try:
                while not self._stop_event.is_set():
                    cycle_started = time.monotonic()
                    await self._one_cycle()
                    now = time.monotonic()

                    if duration and (now - start) >= duration:
                        LOGGER.info("Requested duration %.2fs reached – stopping.", duration)
                        break

                    if next_summary and now >= next_summary:
                        self.log_summary(now - start)
                        next_summary = now + self.config.summary_interval

                    sleep_for = interval - (time.monotonic() - cycle_started)
                    if sleep_for > 0:
                        await asyncio.sleep(sleep_for)
            finally:
                elapsed = time.monotonic() - start
                self.log_summary(elapsed, final=True)
                LOGGER.info("Traffic generator stopped after %.2fs", elapsed)

    def stop(self) -> None:
        self._stop_event.set()

    async def _one_cycle(self) -> None:
        """Perform a single request cycle."""

        assert self._session is not None, "Session must be initialized before running"

        endpoint = self.config.choose_endpoint()
        inject_error = random.random() < self.config.error_probability(endpoint)

        url, method, kwargs = self._build_request(endpoint, inject_error)
        status, exception_label = await self._send_request(method, url, **kwargs)

        if status is not None:
            self.metrics[str(status)] += 1
        if exception_label is not None:
            self.exception_counts[exception_label] += 1

        self.total_requests += 1
        LOGGER.debug(
            "endpoint=%s inject_error=%s status=%s exception=%s", endpoint, inject_error, status, exception_label
        )

    def _build_request(self, endpoint: str, inject_error: bool) -> Tuple[str, str, Dict[str, Any]]:
        """Build the request arguments for a given endpoint."""

        base_headers = _normalize_headers(self.config.headers)
        url = self.config.base_url

        if endpoint == "get_root":
            return f"{url}/", "GET", {"headers": base_headers}

        if endpoint == "get_list":
            return f"{url}/api/restaurant", "GET", {"headers": base_headers}

        if endpoint == "get_one":
            restaurant_id = random.choice(RESTAURANT_IDS) if not inject_error else "invalid_restaurant"
            return f"{url}/api/restaurant/{restaurant_id}", "GET", {"headers": base_headers}

        if endpoint == "post_order":
            payload = build_invalid_order() if inject_error else build_valid_order()
            headers = base_headers.copy()
            headers.setdefault("Content-Type", "application/json")

            if isinstance(payload, str):
                return (
                    f"{url}/api/order",
                    "POST",
                    {"data": payload, "headers": headers},
                )
            if payload is None:
                return (
                    f"{url}/api/order",
                    "POST",
                    {"headers": headers},
                )
            return (
                f"{url}/api/order",
                "POST",
                {"json": payload, "headers": headers},
            )

        if endpoint == "bogus":
            bogus_path = "/api/nope" if not inject_error else "/totally-invalid"
            return f"{url}{bogus_path}", "GET", {"headers": base_headers}

        raise ValueError(f"Unknown endpoint '{endpoint}'")

    async def _send_request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        json: Optional[Any] = None,
        data: Optional[Any] = None,
    ) -> Tuple[Optional[int], Optional[str]]:
        """Send the HTTP request and return status + error label (if any)."""

        assert self._session is not None
        try:
            async with self._session.request(method, url, headers=headers, json=json, data=data) as response:
                await response.read()  # ensure the connection can be reused
                return response.status, None
        except asyncio.TimeoutError:
            return None, "timeout"
        except aiohttp.ClientResponseError as exc:
            return exc.status, exc.__class__.__name__
        except aiohttp.ClientError as exc:
            return None, exc.__class__.__name__
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.exception("Unexpected error during request: %s", exc)
            return None, exc.__class__.__name__

    def log_summary(self, elapsed: float, *, final: bool = False) -> None:
        """Emit a summary of collected metrics so far."""

        total_success = sum(count for status, count in self.metrics.items() if status.isdigit() and status.startswith("2"))
        total_failures = sum(count for status, count in self.metrics.items()) - total_success
        total_errors = sum(self.exception_counts.values())

        parts = [
            f"total={self.total_requests}",
            f"2xx={total_success}",
            f"non2xx={total_failures}",
            f"errors={total_errors}",
        ]
        top_statuses = ", ".join(f"{status}:{count}" for status, count in self.metrics.most_common(5))
        if top_statuses:
            parts.append(f"status_breakdown=[{top_statuses}]")
        if self.exception_counts:
            top_exceptions = ", ".join(
                f"{name}:{count}" for name, count in self.exception_counts.most_common(3)
            )
            parts.append(f"errors=[{top_exceptions}]")

        message = "FINAL" if final else "SUMMARY"
        LOGGER.info("%s %.1fs %s", message, elapsed, " | ".join(parts))


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    """Install SIGINT/SIGTERM handlers to stop the generator gracefully."""

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # pragma: no cover - Windows / restricted envs
            LOGGER.debug("Signal handlers not supported on this platform")
            break


def load_config_file(path: Path) -> Dict[str, Any]:
    """Load a configuration file in YAML or JSON format."""

    suffix = path.suffix.lower()
    text = path.read_text()
    if suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML is required to load YAML configuration files")
        data = yaml.safe_load(text) or {}
        return dict(data)
    if suffix == ".json":
        data = json.loads(text or "{}")
        return dict(data)
    raise ValueError(f"Unsupported configuration file format: {suffix}")


def setup_logging(level: str = "INFO") -> None:
    """Configure basic logging output."""

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def run_with_config(config: TrafficConfig, duration: Optional[float]) -> None:
    """Helper to run the generator with asyncio.run."""

    async def _runner() -> None:
        generator = TrafficGenerator(config)
        await generator.run(duration=duration)

    asyncio.run(_runner())


def main(argv: Optional[Iterable[str]] = None) -> None:
    """CLI entry point for the traffic generator."""

    parser = argparse.ArgumentParser(description="Asynchronous HTTP traffic generator")
    parser.add_argument("--config", type=Path, required=True, help="Path to YAML/JSON config file")
    parser.add_argument("--duration", type=float, default=None, help="Optional duration (seconds) to run")
    parser.add_argument("--rps", type=float, default=None, help="Override requests per second")
    parser.add_argument(
        "--summary-interval",
        type=float,
        default=None,
        help="Override summary logging interval in seconds (0 to disable)",
    )
    parser.add_argument("--log-level", type=str, default="INFO", help="Logging level (DEBUG, INFO, ...)")

    args = parser.parse_args(list(argv) if argv is not None else None)

    config_dict = load_config_file(args.config)
    if args.rps is not None:
        config_dict["rps"] = args.rps
    if args.summary_interval is not None:
        config_dict["summary_interval"] = args.summary_interval

    config = TrafficConfig.from_dict(config_dict)
    setup_logging(args.log_level)
    run_with_config(config, args.duration)


if __name__ == "__main__":  # pragma: no cover - CLI usage
    main()
