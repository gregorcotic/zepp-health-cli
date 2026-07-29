# Native Food / Nutrition implementation record

Status: FOOD LOG IMPLEMENTED IN I002

## Native Food Log

POST `/v2/users/me/events`

- eventType: `Food`
- subType: `real_data`

Production-validated fields:

- foodLogId
- mealType
- mealName
- foodName
- measureWeight
- weightUnit
- energy
- carbohydrates
- protein
- fatTotal
- fiber
- servings
- labels
- emoji
- recognizeType
- recognizeSourceType

## mealType

1 = Breakfast
2 = Morning Snack
3 = Lunch
4 = Afternoon Snack
5 = Dinner
6 = Evening Snack

## Primary fixture

Banana controlled production capture:

- Afternoon Snack
- 13:36
- x2
- 240 g -> 250 g controlled edit
- original 210 kcal
- edited payload ~218.75 kcal
- stable foodLogId

## Food Goals

Property:

`huami.mifit.user.settings.food.goal`

Fields:

- calorie
- carb_percent
- protein_percent
- fat_percent

Percentages must sum to 100.

## Implementation status

Implemented:

1. native `Food / real_data` event reads
2. canonical normalizer
3. factual Food entry model
4. SQLite schema v8 persistence
5. incremental sync/upsert by foodLogId
6. SQLite-only `food --days N` text/JSON CLI
7. deterministic banana, mealType, migration, idempotency, read, and CLI tests

Not implemented:

- Food Goal read/write transport
- derived or native daily nutrition totals
- Food Insight as a factual source
- nutrition recommendations

The known macro split remains factual evidence:

`carb_percent + protein_percent + fat_percent = 100`

I002 does not silently normalize invalid splits and adds no goal mutation.

Read-only live GET validation over 30 and 90 days returned a successful empty
Food domain. The captured banana remains the non-empty production regression
fixture.

Do not use Food Insight as the factual source of nutrition intake.
