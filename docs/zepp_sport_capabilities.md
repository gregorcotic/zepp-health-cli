# Zepp native sport capabilities and semantics

Raw metric presence does not prove metric semantics. Activity processing has
two independent questions:

1. Is the recorded number numerically trustworthy?
2. What does that number mean for this sport?

Ojstrica remains the numerical elevation-quality fixture. Ski is the semantic
fixture: a correct vertical-descent number would still be wrong if exposed as
athlete-powered ascent.

## Production-proven catalog capability matrix

`SUMMARY` means the native history summary provides the category in observed
schemas; population still varies by activity/sensor. `N/A` means the metric is
not a primary sport semantic. Raw GPS samples remain undiscovered in history.

| Sport | Type/mode | Mapping | Distance | Duration | HR/load | Ascent | Descent | Altitude | GPS | Swim | Strength | Semantic note |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Ski | 105/0 | PROVEN | SUMMARY | SUMMARY | SUMMARY | not climbing load | `altitude_descend`, PROVEN | SUMMARY | location only | N/A | N/A | lift gain excluded; vertical descent primary |
| Cross-training | 130/0 | PROVEN | secondary | SUMMARY | SUMMARY | N/A | N/A | N/A | N/A | N/A | PARTIAL | exertion/strength structures may be empty |
| Pool Swim | 14/0 | PROVEN | SUMMARY | SUMMARY | SUMMARY | N/A | N/A | N/A | not expected | SUMMARY | N/A | pool length/strokes/SWOLF/style |
| Open Water Swim | 15/0 | PROVEN | SUMMARY | SUMMARY | SUMMARY | N/A | N/A | N/A | summary location; raw track unknown | SUMMARY | N/A | open-water semantics |
| Open Water Swim - Coach | 15/5 | PROVEN | SUMMARY | SUMMARY | SUMMARY | N/A | N/A | N/A | raw track unknown | SUMMARY | N/A | mode 5 retained |
| E-MTB | 207/0 | PROVEN | SUMMARY | SUMMARY | SUMMARY | sport-relevant, field semantics inferred | SUMMARY | SUMMARY | raw track unknown | N/A | N/A | cadence/power sensor-dependent |
| Gravel Cycling | 208/0 | PROVEN | SUMMARY | SUMMARY | SUMMARY | sport-relevant, field semantics inferred | SUMMARY | SUMMARY | raw track unknown | N/A | N/A | cadence/power sensor-dependent |
| Hiking | 22/0 | PROVEN | SUMMARY | SUMMARY | SUMMARY | `altitude_ascend`, fixture-proven | `altitude_descend` | SUMMARY | raw track unknown | N/A | N/A | athlete-powered ascent eligible |
| Hiking - Coach | 22/5 | PROVEN | SUMMARY | SUMMARY | SUMMARY | sport-relevant, field semantics inferred | SUMMARY | SUMMARY | raw track unknown | N/A | N/A | mode 5 retained |
| Mountain Hiking | 224/0 | PROVEN | SUMMARY | SUMMARY | SUMMARY | sport-relevant, field semantics inferred | SUMMARY | SUMMARY | raw track unknown | N/A | N/A | athlete-powered ascent eligible |
| Walking | 6/0 | PROVEN | SUMMARY | SUMMARY | SUMMARY | sport-relevant, field semantics inferred | SUMMARY | SUMMARY | raw track unknown | N/A | N/A | steps primary |
| Walking - Coach | 6/5 | PROVEN | SUMMARY | SUMMARY | SUMMARY | sport-relevant, field semantics inferred | SUMMARY | SUMMARY | raw track unknown | N/A | N/A | mode 5 retained |
| Outdoor Cycling | 9/0 | PROVEN | SUMMARY | SUMMARY | SUMMARY | sport-relevant, field semantics inferred | SUMMARY | SUMMARY | raw track unknown | N/A | N/A | cycling metrics sensor-dependent |
| Outdoor Cycling - Coach | 9/5 | PROVEN | SUMMARY | SUMMARY | SUMMARY | sport-relevant, field semantics inferred | SUMMARY | SUMMARY | raw track unknown | N/A | N/A | mode 5 retained |

Mapping confidence and metric-semantic confidence are separate. All table
mappings are production-proven. Only field meanings backed by the supplied
fixtures are marked semantically proven; other sport-profile expectations are
`INFERRED` or `UNKNOWN`.

## Central semantic safety rule

The semantic layer retains raw fields, selects normalized meanings, and emits
provenance. A vertical value can enter climbing load only when the sport
profile explicitly sets both and the exact pair's field semantics are
`PROVEN`:

- `athlete_powered_ascent=true`
- `climbing_effort_relevant=true`

For Ski both are false. `altitude_descend` becomes `vertical_descent_m` and
`elevation_loss_m`; normalized `elevation_gain_m` and climbing-load ascent are
null. Raw `altitude_ascend`, `altitude_descend`, and related fields remain
available for forensics.

At present the exact metric semantics are proven for 105/0 Ski, 22/0 Hiking,
and the non-elevation 130/0 Cross-training profile. Other catalog mappings are
production-proven as sport identities, but their metric profiles remain
`INFERRED`; their vertical values cannot yet enter climbing load.

## Units and remaining limits

- `run_time`: seconds in matched fixtures.
- `highPrecisionDistance`/`dis`: metres in matched fixtures.
- `altitude_ascend`/`altitude_descend`: whole metres in the Ski and Ojstrica
  captures.
- Ojstrica's parallel high-precision elevation/altitude fields support `/100`
  scaling on that fixture only.
- GPS/altitude/HR sample streams remain undiscovered through history.
- Titles and Workout Notes remain unavailable through the proven summary path.

Strava remains optional validation/enrichment for titles, notes, exported
routes, and cross-source comparison; it is not automatic ground truth.

## Z001.8 representative production audit

A bounded `GET /v1/sport/run/history.json` request for 2026-01-01 through
2026-07-26 matched all 14 approved representatives among 135 summaries and
ended with `data.next=-1`. This proves population for these activities, not an
account-wide contract.

| Sport | Basic facts populated | Sport-specific facts populated | Basic coach coverage | Advanced coverage |
|---|---|---|---|---|
| Ski | duration, HR, calories, load, TE | 28.13 km downhill distance, 5921 m vertical descent, 14 runs, altitude | READY | raw route/downhill timing missing |
| Cross-training | duration, HR, calories, load, TE, RPE | balance payload present | READY WITH LIMITATIONS | notes, exercises, muscle/body-region load missing |
| Pool Swim | distance, duration, HR, calories, load, TE, RPE | 25 m pool, SWOLF, strokes, style | READY | interval/lap detail missing |
| Open Water Swim | distance, duration, HR, load, TE, RPE | SWOLF, strokes, style; location metadata | READY | raw route missing |
| Open Water Swim - Coach | distance, duration, HR, load, TE | SWOLF, strokes, style; location metadata | READY | raw route and proven coach-plan detail missing |
| E-MTB | distance, duration, HR, load, TE, ascent/descent | location metadata; cadence zero, power unavailable | READY | raw route and sensor streams missing |
| Gravel Cycling | distance, duration, HR, load, TE, RPE, ascent/descent | cadence populated; power unavailable | READY | raw route and power stream missing |
| Hiking | distance, duration, HR, load, TE, RPE, ascent/descent, steps | altitude range and climb-distance summaries | READY | raw route/altitude/HR streams missing |
| Hiking - Coach | distance, duration, HR, load, TE, RPE, ascent/descent, steps | altitude and climb-distance summaries | READY | raw streams and proven coach-plan detail missing |
| Mountain Hiking | distance, duration, HR, load, TE, RPE, ascent/descent, steps | altitude and climb-distance summaries | READY | raw route/altitude/HR streams missing |
| Walking | distance, duration, HR, load, TE, RPE, ascent/descent, steps | cadence/stride summaries | READY | raw route missing |
| Walking - Coach | distance, duration, HR, load, TE, ascent/descent, steps | cadence/stride summaries | READY | raw route and proven coach-plan detail missing |
| Outdoor Cycling | distance, duration/pause, HR, TE, RPE, ascent/descent | cadence populated; load sentinel and power unavailable | READY WITH LIMITATIONS | raw route and sensor streams missing |
| Outdoor Cycling - Coach | distance, duration, HR, load, TE, ascent/descent | cadence populated; power unavailable | READY | raw route and proven coach-plan detail missing |

Raw `te=3` in the Cross-training fixture matches the app's 0.3. This is
retained as evidence rather than silently treated as a universal conversion
for every sport. No representative history summary contained a detected
sample-level GPS, altitude, or workout-HR track. Location metadata is only a
GPS indication. Pool Swim and Cross-training correctly classify GPS as not
applicable.

Cycling sensor absence is activity-specific. Positive cadence occurred for
Gravel and both Outdoor Cycling fixtures; E-MTB reported zero. Power fields
contained unavailable candidates. This does not prove that the sport/API can
never carry power. Known Cross-training Workout Notes remain
`APP_DATA_KNOWN_TO_EXIST_API_LOCATION_NOT_YET_DISCOVERED`.

### Basic coach coverage versus advanced analysis coverage

The summaries provide useful basic facts across all 14 representatives:
identity, duration, distance where relevant, HR, calories, usually native
load/effect metrics, and sport-specific swim, hiking, cycling, or Ski facts.
This supports Zepp as the primary source for the observed activity mix at
basic-coaching level.

Advanced analysis remains incomplete. Route reconstruction, native elevation
validation, workout-HR streams, swim intervals, cycling power streams, and
structured strength/body-region analysis require a detail/track contract not
yet discovered. Strava can optionally add titles, notes, routes, GPX access,
and cross-source validation without becoming a normal-operation dependency.
