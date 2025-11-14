"""Convenience runner that generates healthy traffic against the FoodMe API."""

import asyncio

from traffic_core import TrafficConfig, TrafficGenerator, setup_logging


async def main() -> None:
    config = TrafficConfig(
        target="http://3.255.201.249:3000",
        traffic_type="good",
        rps=3,
        weights={
            "get_root": 10,
            "get_list": 40,
            "get_one": 30,
            "post_order": 20,
            "bogus": 0,
        },
        error_rates={
            "post_order": 0.0,
            "get_one": 0.0,
            "bogus": 0.0,
        },
        summary_interval=60,
    )

    setup_logging("INFO")
    generator = TrafficGenerator(config)
    await generator.run(duration=None)


if __name__ == "__main__":
    asyncio.run(main())
