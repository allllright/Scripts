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
            restaurant_id_list = ["esthers", "robatayaki", "tofuparadise", "bateaurouge", "khartoum",
    "sallys", "saucy", "czechpoint", "speisewagen", "beijing",
    "satay", "cancun", "curryup", "carthage", "burgerama",
    "littlepigs", "littleprague", "kohlhaus", "dragon", "babythai",
    "wholetamale", "bhangra", "taqueria", "pedros", "superwonton",
    "naansequitur", "sakura", "shandong", "currygalore", "north",
    "beans", "jeeves", "zardoz", "angular", "flavia",
    "luigis", "thick", "wheninrome", "pizza76"]
            rid = restaurant_id_list[random.randint(0, len(restaurant_id_list)-1)] if not is_error else "invalid_restaurant"
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