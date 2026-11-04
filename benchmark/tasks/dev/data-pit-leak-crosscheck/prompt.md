I'm about to build a point-in-time value factor on the fundamentals in this project, with a
decision date of 2025-05-07.

Before I do, I need you to confirm the fundamentals are actually safe to use point-in-time —
no look-ahead. If anything is off, list the affected rows with their symbol and quarter so I
can go fix them upstream.

Close with a structured summary: `point_in_time_safe` (true/false), `leaked_keys` (each affected row as
`symbol:quarter`), and `validator_cleared_it` (true/false — did the built-in data
validation report this dataset as passing?).

Deliver that summary as the research-result table, one row per field, with the two columns
`field` and `value`. Where a field holds several values, give it one row per value; where it
holds none, give it a single row with an empty value.
