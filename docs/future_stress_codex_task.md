# Future implementation task — native Zepp Stress

Status: FACTUAL CONTRACT SOLVED / READY FOR IMPLEMENTATION

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

## Implementation order

1. Verify a cloud GET/readback path for all_day_stress.
2. Add first-class normalization.
3. Persist daily summary separately from 5-minute samples.
4. Add incremental sync/upsert.
5. Preserve raw payload/provenance.
6. Add factual freshness.
7. Add CLI JSON output.
8. Add production-fixture tests.
9. Keep native avgStress authoritative.

## Important do-not-repeat rule

`Charge/stress_data/stressInfo` is an internal protobuf-like feature/model
package.

Do not decode it unless a future requirement needs a metric that
all_day_stress does not expose.

