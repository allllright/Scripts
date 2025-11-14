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