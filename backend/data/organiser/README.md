# Organiser data goes here

Put the hackathon's synthetic match files (CSV, JSON or JSON lines) in this folder.
The Gallery lists them under **More sources → Organiser data file** and auto-detects common
column and event names. If something is detected wrongly, copy `mapping.example.json` to
`mapping.json` and set the column or event names.

Organiser data is git-ignored: check the hackathon rules before publishing any of it.

`example_synthetic_match.csv` is NOT organiser data: it's one of The Gallery's own synthetic
matches written in a different shape (other column names, team names, minute + second,
a second-half clock that restarts at 0, a 105 × 68 pitch) to prove the adapter works.
