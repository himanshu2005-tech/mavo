"""The only AI-call limits Mavo enforces: large ceilings that stop a runaway, never ordinary work.

A crew used to carry several small budgets (calls per run, calls per agent, a cycle budget, a
setup ceiling, "spare" calls), each set by a model when the crew was designed. An agent cannot
know in advance how many steps a job takes, so those numbers cut good work short ("It needed one
more step to finish", "2 measured + 3 spare = 5 calls; ceiling 3"). Now the per-crew numbers are
kept only as stored history; what counts is here.
"""

RUN_MAX = 40  # AI calls one run (one agent's turn, one task step) may make
CYCLE_MAX = 160  # one task or crew cycle, all its agents together
DAY_MAX = 500  # one crew in one day: a mail storm or a loop cannot run up a bill


def day_limit(crew) -> int:
    """A crew's daily ceiling: DAY_MAX, or more if the person's schedule asked for more."""
    return max(int(getattr(crew, "daily_call_limit", 0) or 0), DAY_MAX)
