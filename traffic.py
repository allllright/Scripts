#!/usr/bin/env python3
"""
traffic_generator.py
Async traffic generator for FoodMe-like endpoints with adjustable error injection.

Usage:
  pip install -r requirements.txt
  python3 traffic_generator.py --target 3.255.201.249:3000 --rps 5

Config reload:
  - The script watches 'config.json' for runtime settings (weights, error rates).
  - Send SIGHUP to the process to force reload of config.json immediately.

Daemonize: use systemd unit provided below.
"""
import asyncio
import aiohttp
import argparse
import json
import logging
import os
import random
import signal
import sys
import time
from collections import Counter, defaultdict
from typing import Dict, Any

# ---------------------------
# Defaults and helper values
# ---------------------------
DEFAULT_CONFIG_PATH = "config.json"
DEFAULTS = {
    "weights": {
        "get_root": 10,
        "get_restaurants": 40,
        "get_restaurant_id": 25,
        "post_order": 20,
        "bogus": 5
    },
    # post error injection percents (0-100)
    "post_invalid_json_pct": 20,
    "post_missing_fields_pct": 30,
    "post_wrong_content_type_pct": 10,
    # misc
    "max_restaurant_id": 5,
    "user_agent": "traffic-generator/1.0",
    "timeout_seconds": 5
}

# ---------------------------
# Logging & metrics
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("traffic")

metrics = {
    "total_requests": 0,
    "status_counter": Counter(),
    "by_endpoint": Counter(),
    "errors": Counter(),
}
_start_time = time.time()

# ---------------------------
# Config handling
# ---------------------------
def load_config(path: str) -> Dict[str, Any]:
    cfg = {}
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                cfg = json.load(f)
                log.info("Loaded config from %s", path)
        except Exception as e:
            log.error("Failed to parse config file %s: %s", path, e)
    else:
        log.info("Config file %s not found; using defaults", path)
    # merge defaults
    merged = DEFAULTS.copy()
    merged.update(cfg)
    # deep merge weights if present
    merged_weights = DEFAULTS["weights"].copy()
    merged_weights.update(cfg.get("weights", {}))
    merged["weights"] = merged_weights
    return merged

# ---------------------------
# Build request selection
# ---------------------------
def choose_request_type(weights: Dict[str, int]) -> str:
    # weights is a dict of name -> weight
    items = list(weights.items())
    names = [k for k, _ in items]
    w = [v for _, v in items]
    # if all zero, fallback
    if sum(w) == 0:
        return "get_restaurants"
    choice = random.choices(names, weights=w, k=1)[0]
    return choice

def rand_between(low:int, high:int)->int:
    return random.randint(low, high)

def gen_order_payload(max_restaurant_id:int, invalid_json:bool, missing_fields:bool):
    customers = ["Alice","Bob","Carlos","Diana","Eve","Frank","Grace"]
    items_pool = ["Burger","Sushi","Salad","Pizza","Pasta","Taco","Fries"]
    cust = random.choice(customers)
    nitems = random.randint(1,3)
    items = []
    for _ in range(nitems):
        items.append({"name": random.choice(items_pool), "qty": random.randint(1,3)})
    if invalid_json:
        # produce a string that is invalid JSON
        body = '{"customer": "%s", "items": %s' % (cust, json.dumps(items))
        return body, "text/plain"  # wrong content-type often accompanies broken body
    if missing_fields:
        # randomly drop customer or items
        if random.choice([True, False]):
            return json.dumps({"customer": cust}), "application/json"
        else:
            return json.dumps({"items": items}), "application/json"
    return json.dumps({"customer": cust, "items": items}), "application/json"

# ---------------------------
# Request logic
# ---------------------------

class TrafficGenerator:
    def __init__(self, target: str, config_path: str, rps: float, concurrency: int):
        self.target = target.rstrip("/")
        self.config_path = config_path
        self.rps = rps
        self.interval = 1.0 / rps if rps > 0 else 1.0
        self.concurrency = concurrency
        self.config = load_config(config_path)
        self.session = None
        self._stop = asyncio.Event()
        self._reload = asyncio.Event()
        self.work_sem = asyncio.Semaphore(concurrency)
        self.lock = asyncio.Lock()

    async def start(self):
        timeout = aiohttp.ClientTimeout(total=self.config.get("timeout_seconds", 5))
        self.session = aiohttp.ClientSession(timeout=timeout)
        # setup signal handlers for SIGHUP to reload
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGHUP, self.request_reload)
        loop.add_signal_handler(signal.SIGTERM, self.stop)
        loop.add_signal_handler(signal.SIGINT, self.stop)
        # background tasks
        tasks = [
            asyncio.create_task(self._generator_loop()),
            asyncio.create_task(self._metrics_loop()),
            asyncio.create_task(self._reload_watcher()),
        ]
        await self._stop.wait()
        for t in tasks:
            t.cancel()
        await self.session.close()
        log.info("TrafficGenerator stopped")

    def request_reload(self):
        self._reload.set()

    def stop(self):
        log.info("Stop requested")
        self._stop.set()

    async def _reload_watcher(self):
        while not self._stop.is_set():
            await self._reload.wait()
            log.info("Reloading config")
            async with self.lock:
                self.config = load_config(self.config_path)
                self.interval = 1.0 / self.rps if self.rps > 0 else 1.0
            self._reload.clear()

    async def _metrics_loop(self):
        # prints periodic summary
        while not self._stop.is_set():
            await asyncio.sleep(10)
            self.print_summary()

    def print_summary(self):
        elapsed = time.time() - _start_time
        total = metrics["total_requests"]
        rates = total / elapsed if elapsed > 0 else 0
        log.info("SUMMARY: elapsed=%.0fs total=%d avg_rps=%.2f", elapsed, total, rates)
        log.info("Status counter: %s", dict(metrics["status_counter"]))
        log.info("By endpoint: %s", dict(metrics["by_endpoint"]))
        # optional: zero out counters if you prefer periodic windows

    async def _generator_loop(self):
        # maintain steady rate using interval; allow bursts by concurrency semaphore
        while not self._stop.is_set():
            async with self.work_sem:
                asyncio.create_task(self._make_one_request())
            await asyncio.sleep(self.interval)

    async def _make_one_request(self):
        pick = choose_request_type(self.config["weights"])
        try:
            headers = {
                "User-Agent": self.config.get("user_agent", DEFAULTS["user_agent"]),
                "Accept": "application/json"
            }
            url = ""
            method = "GET"
            data = None
            content_type = None
            # decide endpoint
            if pick == "get_root":
                url = f"http://{self.target}/"
                method = "GET"
            elif pick == "get_restaurants":
                url = f"http://{self.target}/api/restaurants"
                method = "GET"
            elif pick == "get_restaurant_id":
                rid = rand_between(1, int(self.config.get("max_restaurant_id", 5)))
                url = f"http://{self.target}/api/restaurant/{rid}"
                method = "GET"
            elif pick == "post_order":
                method = "POST"
                # determine error injection for POST
                r1 = random.randint(0,99)
                invalid_json = r1 < int(self.config.get("post_invalid_json_pct", 20))
                r2 = random.randint(0,99)
                missing_fields = (not invalid_json) and (r2 < int(self.config.get("post_missing_fields_pct", 30)))
                body, content_type = gen_order_payload(self.config.get("max_restaurant_id", 5),
                                                      invalid_json, missing_fields)
                # sometimes use wrong content-type
                if random.randint(0,99) < int(self.config.get("post_wrong_content_type_pct", 10)):
                    content_type = "text/plain"
                url = f"http://{self.target}/api/order"
                data = body
            else:
                # bogus: cause 404 or method error
                if random.choice([True, False]):
                    url = f"http://{self.target}/api/does-not-exist"
                    method = "GET"
                else:
                    url = f"http://{self.target}/api/restaurants"
                    method = "DELETE"

            # perform request
            start = time.time()
            async with self.session.request(method, url, headers=headers,
                                            data=data if data is not None else None) as resp:
                status = resp.status
                elapsed = time.time() - start
                metrics["total_requests"] += 1
                metrics["status_counter"][status] += 1
                metrics["by_endpoint"][pick] += 1
                # optionally read text for debugging small responses (avoid heavy reads)
                # text = await resp.text()
                log.debug("%s %s -> %d %.3fs", method, url, status, elapsed)
        except Exception as e:
            metrics["errors"][str(e.__class__.__name__)] += 1
            log.warning("Request error (%s): %s", pick, e)

# ---------------------------
# CLI and run
# ---------------------------
def build_argparser():
    p = argparse.ArgumentParser(description="Async traffic generator with error injection")
    p.add_argument("--target", "-t", required=True, help="host:port (e.g. 3.255.201.249:3000)")
    p.add_argument("--rps", type=float, default=2.0, help="requests per second (per process)")
    p.add_argument("--concurrency", type=int, default=20, help="concurrent in-flight requests")
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="path to config.json")
    return p

def main():
    args = build_argparser().parse_args()
    cfg = load_config(args.config)
    log.info("Starting traffic generator targeting %s rps=%.2f concurrency=%d", args.target, args.rps, args.concurrency)
    gen = TrafficGenerator(target=args.target, config_path=args.config, rps=args.rps, concurrency=args.concurrency)
    try:
        asyncio.run(gen.start())
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt received; exiting")

if __name__ == "__main__":
    main()
