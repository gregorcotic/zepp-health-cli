# Outdoor Free Diving activity semantics

Status: F001 factual integration, 2026-08-10.

## Verified identity and evidence

Read-only Zepp validation of activity `1786105959` verifies Outdoor Free Diving
as exact native pair `type=196`, `sport_mode=0`. History supplies depth
summaries, dive count, dive/surface durations, diving speeds, HR summaries, and
device temperature. Detail supplies 477 `divingDepth` records, 455 HR records,
a temperature stream, and five 70-component `lap` records.

The referenced `Zepp20260807143239.fit` and `.tcx` files were absent from both
projects, so no FIT/TCX comparison or precedence rule was invented.

## Canonical rules

- Family: `Free Diving`; display name: `Outdoor Free Diving`.
- Depth is positive metres below surface. Ten metres below surface is `10.0`,
  never `-10.0` altitude.
- `detail.divingDepth` is decoded independently into the `depth` stream.
- Depth never populates elevation gain/loss, vertical descent, ski vertical,
  climbing load, or mountain records. Those metrics are not applicable.
- Native millisecond dive/surface summaries become explicitly named seconds
  metrics. Per-dive surface intervals are not derived because lap component
  meanings are not proven.
- Diving speed remains separate from GPS speed.
- `temperature_c` is device temperature, not assumed water temperature.
- Existing generic lap persistence keeps each record in deterministic order
  without speculative names for its 70 components.

No migration is needed: the additive metric-name, stream/sample, and lap tables
already support these facts. Unknown diving-like types remain unknown; no
substring mapping is used. F001 adds no LMRI, recovery, safety, or coaching
interpretation; recovery semantics remain F002.
