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