# FoodMe Traffic Generator

This repository provides an asynchronous HTTP traffic generator that can be used to
stress test the [FoodMe sample application](https://github.com/BetterStackHQ/foodme)
or any similar REST API. The generator can continuously issue a mix of successful
and intentionally faulty requests to help you validate observability dashboards,
error budgets, and alerting pipelines.

Key capabilities:

- Weighted random selection between multiple endpoints (GET/POST).
- Configurable error injection rates to simulate malformed inputs and missing
  resources.
- Asynchronous implementation built with `aiohttp` for efficiency and
  long-running reliability.
- Periodic logging summaries that expose status code counts and exception rates.
- YAML/JSON configuration file support along with convenience scripts for common
  “good” and “chaos” traffic patterns.

> **Safety first:** Always direct the generator at non-production environments or
> sandbox deployments to avoid unwanted load on critical systems.

## Repository layout

| Path | Description |
| ---- | ----------- |
| `Scripts/traffic_core.py` | Core library, CLI, and configuration loader. |
| `Scripts/good_traffic.py` | Preset generator for mostly successful traffic. |
| `Scripts/bad_traffic.py` | Preset generator for chaos/error heavy traffic. |
| `Scripts/traffic_config.example.yaml` | Example configuration file. |
| `README.md` | Project overview (this file). |
| `DOCUMENTATION.md` | Change log and future improvement notes. |

## Getting started

1. Create a virtual environment: `python -m venv .venv && source .venv/bin/activate`
2. Install dependencies: `pip install -r Scripts/requirements.txt`
3. Copy the example configuration and tailor it: `cp Scripts/traffic_config.example.yaml my_config.yaml`
4. Run the generator: `python Scripts/traffic_core.py --config my_config.yaml`

See `DOCUMENTATION.md` for the detailed rundown of changes in this fork and
suggestions for future enhancements.
