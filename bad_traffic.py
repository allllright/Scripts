"""Convenience runner that generates error-heavy traffic against the FoodMe API."""

import asyncio

from traffic_core import TrafficConfig, TrafficGenerator, setup_logging


async def main() -> None:
    config = TrafficConfig(
        target="http://3.255.201.249:3000",
        traffic_type="chaos",
        rps=15,
        weights={
            "get_root": 5,
            "get_list": 15,
            "get_one": 20,
            "post_order": 40,
            "bogus": 20,
        },
        error_rates={
            "post_order": 0.5,
            "get_one": 0.3,
            "bogus": 1.0,
        },
        summary_interval=30,
    )

    setup_logging("INFO")
    generator = TrafficGenerator(config)
    # Run for five minutes by default
    await generator.run(duration=300)


if __name__ == "__main__":
    asyncio.run(main())
