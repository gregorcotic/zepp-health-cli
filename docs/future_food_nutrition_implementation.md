# Future implementation — native Food / Nutrition

Status: CONTRACT SOLVED / READY FOR IMPLEMENTATION

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

## Implementation target

Add:

1. native Food fetch/read support
2. normalizer
3. canonical factual model
4. SQLite persistence
5. incremental sync/upsert by foodLogId
6. CLI factual output
7. tests using production fixtures
8. future TRC nutrition features

Do not use Food Insight as the factual source of nutrition intake.
