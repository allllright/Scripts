I like this idea a *lot* — splitting **good** and **bad** traffic is actually closer to how you’d design test load in a real SRE setup.

Let me walk through how I’d approach it and what the design would look like.

---

## 1. Opinion: 2 scripts is a good idea

**Why it’s nice:**

1. **Clear separation of intent**

   * `good_traffic` = “baseline user behaviour”, mostly 2xx.
   * `bad_traffic` = “fault injection”, intentionally raises error rate.
   * In New Relic you can quickly answer: “Is the app healthy when only good traffic runs?”

2. **Clean observability**

   * You can tag them differently:

     * header `X-Traffic-Type: good` vs `X-Traffic-Type: bad`
     * different `User-Agent`s: `foodme-good-load` vs `foodme-bad-load`
   * Easy to filter charts / NRQL by attribute and compare.

3. **Operational control**

   * Keep `good_traffic` as a **permanent systemd service** (always on).
   * Run `bad_traffic` only when you want:

     * manual: `python bad_traffic.py --duration 600`
     * scheduled: cron or a one-off systemd timer.

4. **Safer experiments**

   * You can first validate SLOs and burn-rate alerts with only good load.
   * Then trigger `bad_traffic` to see:

     * does alerting fire at the right time?
     * does error budget burn as expected?
     * does on-call get paged?

So: yes, I’d go with **two scripts**, but with a **shared core** so you don’t duplicate logic.

---

## 2. High-level design

### 2.1 Components

I’d structure it like this:

* `traffic_core.py`
  Shared async engine:

  * HTTP client (aiohttp)
  * common functions for:

    * choosing endpoint
    * building valid / invalid requests
    * counters + logging
  * `run_traffic(config)` that takes:

    * weights for endpoints
    * error injection percentages
    * rps, concurrency, duration (optional)
    * traffic_type label (“good” / “bad”)

* `good_traffic.py`
  Thin wrapper that calls `run_traffic` with:

  * high weight for valid endpoints
  * very low or zero error injection
  * long / infinite duration
    This one you’ll keep running all the time (systemd).

* `bad_traffic.py`
  Thin wrapper that calls `run_traffic` with:

  * high weight for “bad” behaviours (invalid path, malformed JSON, wrong methods)
  * higher RPS but limited duration (e.g. 5–15 minutes)
    You run this on-demand to simulate incidents.

* `config_good.json` / `config_bad.json`
  Optional JSON config to fine-tune weights + error rates without changing code.

### 2.2 Behaviour of each

**Good traffic script**

* Endpoints:

  * `GET /`
  * `GET /api/restaurants`
  * `GET /api/restaurant/:id`
  * `POST /api/order` (valid JSON, correct headers)
* Error behaviour:

  * Ideally **0% intentional errors**.
  * You might keep a tiny (e.g. 1–2%) chance of “user mistakes” if you want.
* Rate:

  * Moderate and stable (e.g. 2–5 rps).
* Headers:

  * `User-Agent: foodme-good-load`
  * `X-Traffic-Type: good`

**Bad traffic script**

* Endpoints:

  * Same as above, but **plus**:

    * `/api/does-not-exist` (404)
    * `DELETE /api/restaurants` (405/400)
  * `POST /api/order` with:

    * invalid JSON
    * missing fields
    * wrong `Content-Type`
* Error behaviour:

  * Majority of requests intentionally faulty:

    * e.g. 30–60% of POSTs are bad
    * 20% of all requests hit bogus endpoints
* Rate and duration:

  * Higher RPS (e.g. 10–20 rps) but:

    * limited runtime (`--duration 300` seconds)
    * or run as a one-off.
* Headers:

  * `User-Agent: foodme-bad-load`
  * `X-Traffic-Type: bad`

---

## 3. Rough code sketch (structure, not full wall of code)

### 3.1 Shared core (`traffic_core.py`)

```python
# traffic_core.py
import asyncio, aiohttp, random, time
from collections import Counter

class TrafficConfig:
    def __init__(self, target, rps, concurrency, weights, error_cfg, traffic_type):
        self.target = target.rstrip("/")
        self.rps = rps
        self.interval = 1.0 / rps if rps > 0 else 1.0
        self.concurrency = concurrency
        self.weights = weights
        self.error_cfg = error_cfg
        self.traffic_type = traffic_type

async def run_traffic(cfg: TrafficConfig, duration: float | None = None):
    timeout = aiohttp.ClientTimeout(total=5)
    session = aiohttp.ClientSession(timeout=timeout)
    metrics = Counter()
    stop_time = time.time() + duration if duration else None
    sem = asyncio.Semaphore(cfg.concurrency)

    async def worker():
        nonlocal metrics
        while True:
            if stop_time and time.time() >= stop_time:
                break
            async with sem:
                await make_one_request(session, cfg, metrics)
            await asyncio.sleep(cfg.interval)

    workers = [asyncio.create_task(worker()) for _ in range(cfg.concurrency)]
    try:
        await asyncio.gather(*workers)
    finally:
        await session.close()
        print("Metrics:", metrics)

async def make_one_request(session, cfg: TrafficConfig, metrics: Counter):
    # choose endpoint based on cfg.weights dict
    # build URL, method, payload; respect cfg.error_cfg for bad traffic
    # add headers like X-Traffic-Type; send request; update metrics by status code
    ...
```

### 3.2 Good script (`good_traffic.py`)

```python
# good_traffic.py
import asyncio
from traffic_core import TrafficConfig, run_traffic

async def main():
    cfg = TrafficConfig(
        target="3.255.201.249:3000",
        rps=3,
        concurrency=10,
        weights={
            "get_root": 5,
            "get_restaurants": 40,
            "get_restaurant_id": 35,
            "post_order": 20,
            "bogus": 0,  # no deliberate bad paths
        },
        error_cfg={
            "post_invalid_json_pct": 0,
            "post_missing_fields_pct": 0,
            "post_wrong_content_type_pct": 0,
        },
        traffic_type="good",
    )
    # duration=None => run forever until Ctrl-C or systemd stop
    await run_traffic(cfg, duration=None)

if __name__ == "__main__":
    asyncio.run(main())
```

### 3.3 Bad script (`bad_traffic.py`)

```python
# bad_traffic.py
import asyncio
from traffic_core import TrafficConfig, run_traffic

async def main():
    cfg = TrafficConfig(
        target="3.255.201.249:3000",
        rps=15,
        concurrency=50,
        weights={
            "get_root": 5,
            "get_restaurants": 15,
            "get_restaurant_id": 15,
            "post_order": 40,
            "bogus": 25,  # lots of bogus paths/methods
        },
        error_cfg={
            "post_invalid_json_pct": 40,
            "post_missing_fields_pct": 40,
            "post_wrong_content_type_pct": 30,
        },
        traffic_type="bad",
    )
    # e.g. run bad load for 5 minutes
    await run_traffic(cfg, duration=300)

if __name__ == "__main__":
    asyncio.run(main())
```

(Inside `make_one_request` you’d implement the actual URL building & error injection — basically the logic I gave you earlier, but parameterised.)

---

## 4. How you’d operate this

1. **Deploy scripts** on a dedicated “load generator” VM (not on the app host).
2. **Good traffic:**

   * Wrap `good_traffic.py` in a systemd service.
   * It runs 24/7 at low/moderate load.
3. **Bad traffic:**

   * Either:

     * Run manually (`python bad_traffic.py`) when you want to test alerts.
     * Or create a systemd service + timer that you enable only during experiments.
4. **Observability:**

   * Use `X-Traffic-Type` or `User-Agent` to filter in New Relic:

     * Example NRQL:
       `SELECT count(*) FROM Transaction WHERE request.headers.xTrafficType = 'bad' FACET httpResponseCode`

---

## 5. Summary

* Splitting into **good** and **bad** traffic scripts is a solid design:

  * Clear semantics, safer experiments, cleaner observability.
* Use a **shared Python core** (aiohttp + asyncio) so:

  * No duplicated logic.
  * Different configs produce different behaviours.
* Operationally:

  * `good_traffic.py` = always-on background load.
  * `bad_traffic.py` = controlled chaos trigger to test error budgets and alerts.

---

### Two questions for you

1. Would you prefer the **core + two small wrappers** structure, or do you want **two completely independent scripts** (simpler mentally but more duplication)?
2. How “realistic” do you want the good traffic to be — is basic random endpoint selection enough, or do you eventually want realistic **user journeys** (e.g. “list restaurants → pick one → place order” as a sequence)?
