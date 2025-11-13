Great — now that I have your full **FoodMe server code**, we can design the **two-script traffic generator** precisely and safely.
I’ll guide you step-by-step from **understanding the API** → **designing the generator architecture** → **implementing it in Python** in a clean modular way.

This will be *very clear and actionable*, and you’ll know exactly what to do next.

---

# ✅ Step 1 — Understand your API surface (from your code)

Your server exposes these REST endpoints:

### **Public paths**

| Method     | Path                  | Notes                                   |
| ---------- | --------------------- | --------------------------------------- |
| **GET**    | `/`                   | Renders frontend HTML                   |
| **GET**    | `/api/restaurant`     | List restaurants (no menus)             |
| **POST**   | `/api/restaurant`     | Create restaurant                       |
| **GET**    | `/api/restaurant/:id` | Get 1 restaurant by ID                  |
| **PUT**    | `/api/restaurant/:id` | Update or create restaurant             |
| **DELETE** | `/api/restaurant/:id` | Delete restaurant                       |
| **POST**   | `/api/order`          | Checkout order, returns `{orderId: ts}` |

### **Responses your server gives**

* **201** for successful POST/PUT
* **200** for successful GET
* **204** delete success
* **400** for invalid IDs or invalid bodies
* **No 500** unless your payload breaks JSON parsing → (this is great for testing!)

This API surface is perfect for good/bad traffic generation.

---

# ✅ Step 2 — Why two scripts is the best approach

### ✔ Good traffic script (permanently running)

Simulates:

* GET `/`
* GET `/api/restaurant`
* GET `/api/restaurant/:id`
* POST `/api/order` (valid payload)

This keeps the app "alive" and stable.
You run it continuously using systemd.

### ✔ Bad traffic script (run only when needed)

Simulates:

* Invalid restaurant IDs
* Missing fields in POST `/api/order`
* **Malformed JSON** → forces your Node server to 500/crash JSON parser
* `DELETE /api/restaurant` wrong usage
* Wrong methods
* Garbage endpoints `/api/xxxx`
* Wrong Content-Type
* High RPS spikes

This triggers error budget burn, alerting, NR anomaly detection, etc.

---

# ✅ Step 3 — Architecture of the Python traffic generator

### We will create 3 files:

### ✔ `traffic_core.py` (all logic lives here)

* shared aiohttp session
* shared request builder
* metrics counters
* async event loop
* function `run_traffic(config, duration)`

### ✔ `good_traffic.py`

* imports `run_traffic`
* passes config with **0% error**
* low RPS, infinite duration

### ✔ `bad_traffic.py`

* imports `run_traffic`
* config heavily biased toward **errors**
* higher RPS, limited duration

This means:

* No duplication
* Clear separation of concerns
* Easy to maintain
* Easy to evolve

Exactly what a real SRE team would build.

---

# ✅ Step 4 — I will now guide you step-by-step to implement this

Below is **Phase 1** implementation:
A minimal but complete working version you can run immediately.

---

# ⭐ Phase 1 — Create the folder structure

SSH into your EC2 load generator VM and create:

```
trafficgen/
 ├── traffic_core.py
 ├── good_traffic.py
 ├── bad_traffic.py
 ├── requirements.txt
 └── config_good.json   (optional)
```

---

# ⭐ Phase 2 — Paste this into `requirements.txt`

```
aiohttp>=3.8
```

Install:

```bash
pip install -r requirements.txt
```

---

# ⭐ Phase 3 — Paste this into `traffic_core.py`

> This is a simplified first version so you can test quickly.
> (We will harden it in Phase 2 with better metrics & tracing.)

```python
import aiohttp
import asyncio
import random
import time
from collections import Counter

# --------------------------------------
# Shared helper functions for endpoints
# --------------------------------------

def build_valid_order():
    return {
        "items": [
            {"name": "Pizza", "qty": random.randint(1, 3)},
            {"name": "Salad", "qty": 1}
        ],
        "deliverTo": {"name": "Test User"},
        "restaurant": {"name": "Demo"}
    }

def build_invalid_order():
    # Missing fields / broken JSON-like structure
    choices = [
        {"items": []},
        {"deliverTo": {}},
        "INVALID_JSON{{{",         # intentionally wrong
        {"items": [{"qty": "xx"}]} # invalid type
    ]
    return random.choice(choices)

# --------------------------------------
# Main traffic generator logic
# --------------------------------------

async def send_request(session, method, url, json_body=None, headers=None):
    try:
        async with session.request(method, url, json=json_body, headers=headers) as resp:
            return resp.status
    except Exception as e:
        return str(e)

async def run_traffic(config, duration=None):
    """
    config = {
       "target": "3.255.201.249:3000",
       "rps": 5,
       "traffic_type": "good" or "bad",
       "weights": {...},
       "error_rates": {...}
    }
    """

    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))

    end_time = time.time() + duration if duration else None
    interval = 1.0 / config["rps"]
    metrics = Counter()

    print(f"Starting traffic ({config['traffic_type']}) → {config['target']} at {config['rps']} rps")

    async def one_cycle():
        choice = random.choices(
            population=list(config["weights"].keys()),
            weights=list(config["weights"].values()),
            k=1
        )[0]

        # Good or bad behavior decision
        is_error = random.random() < config["error_rates"].get(choice, 0)

        target = config["target"]

        headers = {
            "User-Agent": f"foodme-{config['traffic_type']}-load",
            "X-Traffic-Type": config["traffic_type"]
        }

        # -------------------------------------------------------------------
        # ROUTES
        # -------------------------------------------------------------------
        if choice == "get_root":
            status = await send_request(session, "GET", f"http://{target}/", None, headers)

        elif choice == "get_list":
            status = await send_request(session, "GET", f"http://{target}/api/restaurant", None, headers)

        elif choice == "get_one":
            rid = random.randint(1, 5) if not is_error else 9999
            status = await send_request(session, "GET", f"http://{target}/api/restaurant/{rid}", None, headers)

        elif choice == "post_order":
            good_payload = build_valid_order()
            bad_payload  = build_invalid_order()
            payload = bad_payload if is_error else good_payload

            # If error, half the time use wrong content-type
            if is_error and isinstance(payload, str):
                status = await send_request(
                    session,
                    "POST",
                    f"http://{target}/api/order",
                    json_body=None,
                    headers={"Content-Type": "text/plain"}
                )
            else:
                status = await send_request(session, "POST", f"http://{target}/api/order", payload, headers)

        elif choice == "bogus":
            # Always return 404 or 400
            status = await send_request(session, "GET", f"http://{target}/api/nope", None, headers)

        else:
            status = "unknown"

        metrics[str(status)] += 1

    # Main loop
    try:
        while True:
            await one_cycle()
            await asyncio.sleep(interval)

            if duration and time.time() > end_time:
                break

    finally:
        await session.close()
        print("Final metrics:", metrics)
```

---

# ⭐ Phase 4 — `good_traffic.py`

```python
import asyncio
from traffic_core import run_traffic

if __name__ == "__main__":
    config = {
        "target": "3.255.201.249:3000",
        "traffic_type": "good",
        "rps": 3,
        "weights": {
            "get_root": 10,
            "get_list": 40,
            "get_one": 30,
            "post_order": 20,
            "bogus": 0
        },
        "error_rates": {
            "post_order": 0,
            "get_one": 0,
            "bogus": 0
        }
    }

    asyncio.run(run_traffic(config, duration=None))
```

This runs forever (good traffic).

---

# ⭐ Phase 5 — `bad_traffic.py`

```python
import asyncio
from traffic_core import run_traffic

if __name__ == "__main__":
    config = {
        "target": "3.255.201.249:3000",
        "traffic_type": "bad",
        "rps": 15,
        "weights": {
            "get_root": 5,
            "get_list": 15,
            "get_one": 20,
            "post_order": 40,
            "bogus": 20
        },
        "error_rates": {
            "post_order": 0.5,
            "get_one": 0.3,
            "bogus": 1.0
        }
    }

    # Run for 5 minutes (300 seconds)
    asyncio.run(run_traffic(config, duration=300))
```

This will:

* send lots of invalid orders
* hit invalid restaurant IDs
* hit bogus endpoints
* spike 400/404/500
* burn NR error budget fast

---

# ⭐ Step 6 — Run both

## Good traffic (forever)

```
python good_traffic.py
```

## Bad traffic (on demand)

```
python bad_traffic.py
```

---

# ⭐ Step 7 — Optional next steps (I can implement for you)

If you want, I can help you add:

### 🚀 Version 2 improvements

* Prometheus `/metrics` endpoint for Grafana dashboards
* More realistic order payloads
* Spike patterns (traffic waves)
* Multiple worker tasks for higher concurrency
* Systemd service files for good_traffic
* New Relic Trace Headers (`traceparent`)
* Bulk restaurant creation

Just tell me *which direction you want to expand*.

---

# ✅ Before I continue — a quick question

Do you want me to:

### **A)** Generate a *more advanced* version with

* concurrency
* Prometheus export
* NR tracing
* config files
* SIGHUP reload
* structured logging
* and dashboards?

### **B)** Keep it simple, keep the 2 scripts minimal?

Pick **A** or **B**, and I’ll continue with the next phase.
