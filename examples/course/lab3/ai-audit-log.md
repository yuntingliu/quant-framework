# AI audit log

Name:            Student ID:

Record every interaction in which an AI assistant produced code, a formula or
an interpretation that you used. Keep entries short and concrete. The aim is to
show that **you** decided what was correct.

| # | Lab / file / function | Prompt (short) | What came back | How you checked it (test name or oracle) | What you changed and why |
|---|---|---|---|---|---|
| 1 | Lab 1 / `my_pricer.py` / `greeks` | "Implement greeks() with theta per calendar day" | theta divided by 252 | `test_fd_theta_per_calendar_day` failed by a factor of 1.45 | divided by 365; unit added to docstring |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |

## Reflection (3–5 sentences)

Which AI output looked most convincing but was wrong? Which check exposed it,
and would you have found it without that check?
