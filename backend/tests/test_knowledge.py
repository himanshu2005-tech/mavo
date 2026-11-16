"""Mavo's knowledge base: what the person tells it is kept and used later ("if I said my LinkedIn
id, it remembers in the future"), a newer fact replaces an older one about the same thing, and
nothing that unlocks an account or pays for something is ever kept."""

from mavo.shared_memory import learn, relevant, secret


def test_profile_facts_and_remember_requests_are_learned_from_the_persons_words():
    assert learn("my linkedin id is linkedin.com/in/asha-r") == [
        "My linkedin id is linkedin.com/in/asha-r"
    ]
    assert learn("My GitHub username is asha-dev and my college is City College.") == [
        "My GitHub username is asha-dev",
        "My college is City College",
    ]
    assert learn("remember that I prefer remote jobs") == ["I prefer remote jobs"]
    assert learn("what is my linkedin") == []
    assert learn("my day is busy") == []  # not a profile fact, and no "remember"


def test_passwords_codes_and_card_numbers_are_never_kept():
    assert learn("my password is hunter2") == []
    assert learn("remember my OTP 482913") == []
    assert secret("card 4111 1111 1111 1111")
    assert secret("my api key is sk-abcdefghijklmnop")
    assert not secret("My phone number is 98480 12345")


def test_the_facts_that_match_the_work_come_first_once_there_are_many():
    lines = [f"Fact {i} about cooking" for i in range(100)] + ["My LinkedIn is linkedin.com/in/x"]
    picked = relevant(lines, "Fill the job application with my LinkedIn", limit=10)
    assert "My LinkedIn is linkedin.com/in/x" in picked and len(picked) == 10
    assert relevant(lines[:5], "anything") == lines[:5]


def test_the_api_keeps_replaces_edits_and_refuses_secrets(client, auth):
    first = client.post(
        "/api/v1/memory", headers=auth, json={"text": "My LinkedIn is linkedin.com/in/old"}
    )
    assert first.status_code == 200
    newer = client.post(
        "/api/v1/memory", headers=auth, json={"text": "My LinkedIn is linkedin.com/in/new"}
    ).json()
    assert [f["text"] for f in newer] == ["My LinkedIn is linkedin.com/in/new"]
    edited = client.patch(
        f"/api/v1/memory/{newer[0]['id']}",
        headers=auth,
        json={"text": "My LinkedIn is linkedin.com/in/asha"},
    ).json()
    assert [f["text"] for f in edited] == ["My LinkedIn is linkedin.com/in/asha"]
    refused = client.post(
        "/api/v1/memory", headers=auth, json={"text": "My Gmail password is hunter2"}
    )
    assert refused.status_code == 400 and "never keeps" in refused.json()["detail"]
