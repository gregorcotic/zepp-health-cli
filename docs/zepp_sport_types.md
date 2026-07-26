# Zepp sport type catalog

The mapping identity is the exact `(type, sport_mode)` pair. Modes are never
collapsed: mode 5 identifies a Zepp Coach activity even when its sport family
matches mode 0.

Every mapping below was manually verified against a real activity in the
user's Zepp app. This evidence grade is
`PRODUCTION_PROVEN_MANUAL_APP_MATCH`. No other pair is mapped.

| Type | Mode | Zepp sport | Family | Coach mode | Evidence |
|---:|---:|---|---|---|---|
| 105 | 0 | Ski | Ski | no | production-proven manual app match |
| 130 | 0 | Cross-training | Cross-training | no | production-proven manual app match |
| 14 | 0 | Pool Swim | Swimming | no | production-proven manual app match |
| 15 | 0 | Open Water Swim | Swimming | no | production-proven manual app match |
| 15 | 5 | Open Water Swim - Zepp Coach | Swimming | yes | production-proven manual app match |
| 207 | 0 | E-MTB | Cycling | no | production-proven manual app match |
| 208 | 0 | Gravel Cycling | Cycling | no | production-proven manual app match |
| 22 | 0 | Hiking | Hiking | no | production-proven manual app match |
| 22 | 5 | Hiking - Zepp Coach | Hiking | yes | production-proven manual app match |
| 224 | 0 | Mountain Hiking | Hiking | no | production-proven manual app match |
| 6 | 0 | Walking | Walking | no | production-proven manual app match |
| 6 | 5 | Walking - Zepp Coach | Walking | yes | production-proven manual app match |
| 9 | 0 | Outdoor Cycling | Cycling | no | production-proven manual app match |
| 9 | 5 | Outdoor Cycling - Zepp Coach | Cycling | yes | production-proven manual app match |

`GET /v1/sport/run/history.json` returned all of these production groups, so it
is proven broader than literal running for this account/window. Account-wide
completeness and cursor semantics remain separate unresolved questions.

## Ski semantic fixture

The January 2 Ski fixture (`trackid=1767339463`) returned:

- `altitude_ascend=0`
- `altitude_descend=5921`
- `climb_dis_descend=28133`
- `max_altitude=1913`
- `min_altitude=965`

The Zepp app displayed about 5913 m vertical and the TCX was identified as the
same activity. The app metric is vertical **descent**, represented by
`altitude_descend` in this API capture; it is not ascent. The small 5913/5921
difference may reflect display revision or processing and is not silently
corrected.

Never commit personal payloads, coordinates, device/user identifiers, tokens,
URLs, or workout notes.

## Z001.8 coverage confirmation

The bounded 2026 capability audit matched one approved representative for
every catalog pair, and every record retained its expected exact
`(type, sport_mode)` identity. No mapping was added or changed. Run and Trail
Run remain `NOT_FOUND_IN_CURRENT_PRODUCTION_SAMPLE`.
