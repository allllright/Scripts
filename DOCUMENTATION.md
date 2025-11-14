# Traffic Generator Update Report

## Summary of work performed

- Rebuilt the core traffic generator (`Scripts/traffic_core.py`) into a reusable
  module with a CLI, configuration loader, improved rate control, and periodic
  metric summaries.
- Added structured logging, graceful shutdown on SIGINT/SIGTERM, and better
  status/exception tracking.
- Updated the `good_traffic.py` and `bad_traffic.py` helpers to use the new
  configuration class so they remain useful presets.
- Introduced an example YAML configuration file to make the generator easier to
  customise without code changes.
- Authored a repository `README.md` (this was previously missing) to document
  capabilities, layout, and quickstart steps.

## Suggestions and next steps

1. **Metrics export for observability platforms** – Instrument `traffic_core`
   with Prometheus metrics (e.g., using `prometheus-client`) and expose an HTTP
   endpoint so dashboards and alerting tools can consume live counters.
2. **Pluggable scenarios** – Consider loading endpoint definitions from the
   configuration file so you can simulate entirely different APIs without code
   edits. A JSON schema could describe method, path, payload templates, and error
   injection strategies.
3. **Distributed load** – For higher traffic volumes, add support for running
   multiple generator instances with a coordinator that aggregates metrics.
4. **Containerisation** – Package the generator into a lightweight Docker image
   with sensible defaults to simplify deployment into Kubernetes or ECS for
   longer-term burn-rate experiments.
5. **Secrets management** – If targeting authenticated APIs in future, provide a
   secure mechanism (environment variables or secret stores) for credentials,
   with periodic token refresh support.

These enhancements would round out the project for production-grade SRE load and
observability drills.
