# Future Codex task — Respiratory Rate

Status: DEFERRED

## Production-proven contract

`eventType = RespiratoryRate`
`subType = real_data`

`value.measurements` is encoded/binary and still requires investigation.

## Important provenance warning

Legacy code contains a parser/model for:

`RespiratoryRate / record`

including:

- respiratoryRate
- recoveryScore
- minuteOfDay

But no production raw capture of this `record` contract was found.

Treat those fields as fixture-derived until independently proven.

## Required investigation order

1. Search the full production capture corpus for any actual
   `RespiratoryRate/record` payload.
2. Search current live traffic for a factual respiratory readback contract.
3. If no direct contract exists, reverse engineer `real_data.measurements`.
4. Validate decoded respiratory rate against Zepp UI/device evidence.
5. Only then implement normalization, persistence, sync, CLI and tests.

Do not migrate the legacy fixture contract as factual truth without evidence.

