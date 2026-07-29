# Native Zepp Stress implementation record

Status: I001.1–I001.5 IMPLEMENTED

Do NOT restart protobuf reverse engineering.

## Preferred factual contract

Use:

`eventType = all_day_stress`

Known native payload:

- data
- minStress
- maxStress
- avgStress
- relaxProportion
- normalProportion
- mediumProportion
- highProportion

`data` is a sparse 5-minute timeline:

- time = epoch milliseconds
- value = Stress score 0–100

Missing intervals are missing measurements, not zero.

## Score categories

- 0–39 Relaxed
- 40–59 Normal
- 60–79 Medium
- 80–100 High

These were mathematically validated against native category proportions.

## Implemented sequence

1. Cloud GET/readback path for all_day_stress — done.
2. First-class normalization — done.
3. Separate daily summary and sparse sample persistence — done (schema v7).
4. Incremental sync/upsert — done and production-validated.
5. Raw payload/provenance preservation — done internally.
6. Local-date factual freshness — done (`current`, `stale`, `missing`).
7. SQLite-only CLI text and JSON output — done.
8. Offline database and CLI tests — done.
9. Native avgStress remains authoritative.

Commands:

```bash
python3 zepp_health.py stress --days 7
python3 zepp_health.py stress --days 7 --json
python3 zepp_health.py stress --days 1 --samples --json
```

## Important do-not-repeat rule

`Charge/stress_data/stressInfo` is an internal protobuf-like feature/model
package.

Do not decode it unless a future requirement needs a metric that
all_day_stress does not expose.
