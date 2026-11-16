"""A one-time task's reply is written before it runs, so it never says the work is done."""

from types import SimpleNamespace

from mavo.crew_planner import tidy_reply


def decision(reply, task=True):
    return SimpleNamespace(
        reply=reply,
        questions=[],
        crew_proposal=None,
        crew_proposals=[],
        task=SimpleNamespace(title="Plan today's day") if task else None,
    )


def test_a_task_reply_claiming_it_is_done_says_it_has_started():
    claimed = (
        "I pulled your calendar, tasks, mail and Drive and built a prioritized plan for today "
        "— it's on your desktop now."
    )
    assert tidy_reply(decision(claimed)) == (
        "On it: Plan today's day. I'll show the result here when it's done."
    )
    assert tidy_reply(decision("Here is your plan.")).startswith("On it:")
    assert tidy_reply(decision("")).startswith("On it:")


def test_an_honest_task_reply_and_ordinary_replies_are_kept():
    honest = "I'll go through your calendar, mail and tasks and plan your day."
    assert tidy_reply(decision(honest)) == honest
    assert tidy_reply(decision("I pulled up the weather: 31 degrees.", task=False)).startswith(
        "I pulled up"
    )
