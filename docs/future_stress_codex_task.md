# Future Codex task — native Zepp Stress completion

Status: DEFERRED / READY FOR FOCUSED INVESTIGATION

## Objective

Complete first-class Zepp Stress support without repeating the manual research
already performed.

## Read first

- `docs/current_project_handoff.md`
- `docs/legacy_zepp_audit.md`
- `docs/project_history.md`
- `docs/troubleshooting/zepp_health_troubleshooting.md`

## Known UI fixture — 28-May-2026

Daily:

- Min = 9
- Max = 61
- Avg = 34
- Relaxed = 52%
- Normal = 47%
- Medium = 1%
- High = 0%

5-minute points:

- 05:50 = 40
- 05:55 = 28
- 06:00 = missing
- 06:35 = missing

Thresholds:

- 0–39 Relaxed
- 40–59 Normal
- 60–79 Medium
- 80–100 High

## Native evidence

Captured upload:

POST `/v2/users/me/events`

`eventType=Charge`
`subType=stress_data`

Payload includes:

- `value.startTime`
- `samples[].minuteOfDay`
- `samples[].stressInfo`

`stressInfo` is base64 protobuf-like binary.

Manual reverse engineering established:

- FIELD 1 is not the direct UI Stress score stream
- FIELD 1 is not a simple validity mask
- ±15-minute offset mapping is disproven
- fields 6/8 are feature-vector style data, not final scores
- do not fit a Stress formula from two UI points

Separate legacy event evidence contains explicit:

`avgStress`

Known captures include:

- avgStress = 28
- avgStress = 34

The `avgStress=34` capture matches the 28-May-2026 UI daily average.

## Required investigation order

1. Search the complete capture corpus for `avgStress`.
2. Identify the exact eventType/subType and full payload containing it.
3. Look for related:
   - minStress
   - maxStress
   - stress timeline
   - category distribution
   - per-5-minute scores
4. Reconstruct the 28-May-2026 UI fixture from explicit native fields if
   possible.
5. Only if no direct score contract exists, return to `stressInfo` protobuf
   analysis.
6. Do not invent semantics.
7. Preserve raw values and provenance.
8. Add first-class normalization, SQLite persistence, CLI access, tests, and
   factual freshness only after the native final-score contract is proven.

## Success criteria

A production-backed implementation must reproduce at least:

28-May-2026:

- Avg = 34
- Min = 9
- Max = 61
- 05:50 = 40
- 05:55 = 28
- 06:00 = missing
- 06:35 = missing

without reverse-engineered guesswork.

