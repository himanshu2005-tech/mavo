"""All registered tools have a safe setup path. No real services or desktop execution."""

from unittest.mock import AsyncMock

import pytest

from mavo.connectors.catalog import action_grant
from mavo.database import ActionRequest, CrewInstance, GoogleAccount, WorkspaceSetting
from mavo.google_actions import MODELS, ActionDraft, ActionPolicyError
from mavo.verification_preview import PREVIEW_ACTIONS, READ_ACTIONS, preview_action
from tests.test_actions import action_env  # noqa: F401


def arguments(operation, folder):
    common = {
        "command": "system.info",
        "folder": str(folder),
        "url": "https://example.org",
        "query": "sample",
        "goal": "Jobs",
        "text": "Sample text",
        "option": "Sample",
        "key": "Enter",
        "window_id": 1,
        "process_id": 1,
        "process_started": 1.0,
        "level": 20,
        "path": str(folder / "new.md"),
        "source": str(folder / "sample.txt"),
        "destination_folder": str(folder / "destination"),
        "name": "Sample project",
        "project": "Sample project",
        "tool": "python",
        "content": "print('sample')",
        "role": "critic",
        "instructions": "Review the sample",
        "repository": "owner/repo",
        "title": "Sample title",
        "number": 1,
        "body": "Sample body",
        "to": "recipient@example.com",
        "subject": "Sample subject",
        "message_id": "observed-mail",
        "summary": "Sample event",
        "start": "2026-11-10T10:00:00+05:30",
        "end": "2026-11-10T11:00:00+05:30",
        "item_id": "observed-item",
        "etag": '"v1"',
        "file": "Resume.pdf",
    }
    args = {key: common[key] for key in MODELS[operation].model_json_schema().get("required", [])}
    if operation.startswith("browser.") and operation.rsplit(".", 1)[1] in {
        "click",
        "double_click",
        "hover",
        "drag",
        "fill",
        "upload",
        "select",
    }:
        args["ref"] = "e1"
    if operation == "browser.drag":
        args["to_ref"] = "e2"
    if operation == "files.backup":
        args["folder"] = str(folder / "source-folder")
    if operation == "terminal.run":
        args["command"] = "system.info"  # one of command / shell, neither "required" alone
    if operation == "windows.key":
        args["key"] = "enter"
    if operation == "windows.media":
        args["key"] = "play_pause"
    if operation.startswith("project."):
        if "path" in args:
            args["path"] = "sample.py"
        if operation == "project.run":
            args["script"] = "sample.py"
    return args


def private_action(env, operation, folder):
    _, _, engine, _ = env
    grant = action_grant(operation)
    permissions = ["model:invoke", grant]
    if operation.split(".")[0] in {"gmail", "calendar", "tasks"}:
        permissions.append("google:" + operation.split(".")[0] + ":read")
    with engine.factory() as session:
        account = session.get(GoogleAccount, "google")
        account.cursors = {
            service: {
                "snapshot": [
                    {
                        "id": "observed-mail" if service == "gmail" else "observed-item",
                        "etag": '"v1"',
                    },
                ]
            }
            for service in ("gmail", "calendar", "tasks")
        }
        if operation.startswith("github."):
            session.add(
                WorkspaceSetting(
                    key="connector-github",
                    value={
                        "subject": "fixture",
                        "capabilities": ["github:issues:write"],
                        "repositories": ["owner/repo"],
                    },
                )
            )
            engine.connectors.credentials.set("connector-github-tokens", "fixture-token")
        source = CrewInstance(
            name="Setup source",
            status="draft",
            permissions=permissions,
            template_id="operator",
            responsibilities="Fixture",
            provider="ollama",
            model="fixture",
        )
        session.add(source)
        session.flush()
        adapter = CrewInstance(
            name="Private verification",
            status="active",
            is_running=True,
            permissions=permissions,
            template_id="operator",
            responsibilities="Fixture",
            provider="ollama",
            model="fixture",
            configuration_plan={"first_run_source": source.id},
        )
        session.add(adapter)
        session.flush()
        source.configuration_plan = {
            "first_run": {
                "status": "running",
                "adapter_id": adapter.id,
                "generation": source.runtime_generation,
            }
        }
        rule = {
            "enabled": True,
            "mode": "unrestricted",
            "revision": "fixture",
            "folders": [str(folder)],
            "domains": [],
            "apps": [],
            "recipients": [],
            "repositories": ["owner/repo"],
        }
        for owner in (source.id, adapter.id):
            for op in (
                {operation, "project.create"} if operation.startswith("project.") else {operation}
            ):
                session.add(WorkspaceSetting(key=engine.rules.key(op, owner), value=rule))
        session.commit()
        adapter_id, generation, source_id = adapter.id, adapter.runtime_generation, source.id
    action = engine.propose(
        ActionDraft(operation=operation, arguments=arguments(operation, folder)),
        adapter_id,
        generation,
    )
    return action, source_id, adapter_id


@pytest.mark.parametrize("operation", sorted(MODELS))
def test_every_registered_action_is_read_only_or_intercepted_without_execution(
    action_env, tmp_path, operation
):
    client, _, engine, state = action_env
    folder = tmp_path / "allowed"
    folder.mkdir()
    (folder / "sample.txt").write_text("untouched", encoding="utf-8")
    (folder / "source-folder").mkdir()
    action, _, _ = private_action(action_env, operation, folder)
    engine.perform = AsyncMock(side_effect=AssertionError("Preview must never reach execution"))
    engine.auth.access_token = AsyncMock(
        side_effect=AssertionError("No token request for a preview")
    )
    engine.connectors.access_token = AsyncMock(side_effect=AssertionError("No connector write"))
    if operation in READ_ACTIONS:
        with engine.factory() as session:
            assert preview_action(engine, session, session.get(ActionRequest, action["id"])) is None
    else:
        result = client.portal.call(engine.maybe_execute, action["id"])
        assert result["status"] == "previewed", result
        assert result["result"]["applied"] is False
        assert result["result"]["verification"] == "validated_proposal_only"
        assert "execution_result" in result["result"]["not_verified"]
        assert client.portal.call(engine.execute, action["id"])["status"] == "previewed"
    engine.perform.assert_not_called()
    engine.auth.access_token.assert_not_called()
    engine.connectors.access_token.assert_not_called()
    assert state["writes"] == []
    assert (folder / "sample.txt").read_text(encoding="utf-8") == "untouched"
    assert not (folder / "new.md").exists() and not (folder / "destination").exists()
    assert not (folder / "sample-project").exists()


def test_registry_partition_defaults_new_effectful_tools_to_non_execution():
    assert not READ_ACTIONS.intersection(PREVIEW_ACTIONS)
    assert READ_ACTIONS | PREVIEW_ACTIONS == set(MODELS)


@pytest.mark.parametrize(
    "change", ["policy", "scope", "account", "target", "ended", "paused", "ceiling"]
)
def test_mail_preview_rechecks_all_authorities(action_env, tmp_path, change):
    client, _, engine, state = action_env
    action, source_id, adapter_id = private_action(action_env, "gmail.mark_read", tmp_path)
    with engine.factory() as session:
        account = session.get(GoogleAccount, "google")
        if change == "policy":
            setting = session.get(WorkspaceSetting, engine.rules.key("gmail.mark_read", source_id))
            setting.value = {**setting.value, "enabled": False}
        elif change == "scope":
            account.scopes = []
        elif change == "account":
            account.subject = "someone-else"
        elif change == "target":
            account.cursors = {}
        elif change == "ended":
            session.get(CrewInstance, source_id).configuration_plan = {}
        elif change == "paused":
            session.add(WorkspaceSetting(key="actions-paused", value={"paused": True}))
        elif change == "ceiling":
            session.add(WorkspaceSetting(key="pool-access", value={"gmail": "read"}))
        session.commit()
    engine.perform = AsyncMock(side_effect=AssertionError("Never execute a preview"))
    with pytest.raises((ActionPolicyError, ValueError)):
        client.portal.call(engine.maybe_execute, action["id"])
    assert not state["writes"]
    engine.perform.assert_not_called()


def test_project_preview_does_not_grant_trust_or_approve_script(action_env, tmp_path):
    client, auth, engine, _ = action_env
    action, _, _ = private_action(action_env, "project.run", tmp_path)
    result = client.portal.call(engine.maybe_execute, action["id"])
    assert result["status"] == "previewed" and result["approved_by"] is None
    assert "script_approval" in result["result"]["not_verified"]
    with engine.factory() as session:
        assert session.get(WorkspaceSetting, "run:" + action["id"]) is None
        assert session.get(WorkspaceSetting, "trusted-project:Sample project") is None
    response = client.post(
        f"/api/v1/actions/{action['id']}/approve", headers=auth, json={"sha256": "0" * 64}
    )
    assert response.status_code == 409


@pytest.mark.parametrize("operation", ["files.move", "notes.write", "project.write"])
def test_path_confinement_is_checked_without_creating_anything(action_env, tmp_path, operation):
    client, _, engine, _ = action_env
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "sample.txt").write_text("untouched", encoding="utf-8")
    action, _, _ = private_action(action_env, operation, root)
    with engine.factory() as session:
        row = session.get(ActionRequest, action["id"])
        key = "destination_folder" if operation == "files.move" else "path"
        row.arguments = {
            **row.arguments,
            key: "../outside.py"
            if operation == "project.write"
            else str(tmp_path / ("outside.md" if operation == "notes.write" else "outside")),
        }
        session.commit()
    engine.perform = AsyncMock(side_effect=AssertionError("No execution after scope rejection"))
    with pytest.raises(ActionPolicyError):
        client.portal.call(engine.maybe_execute, action["id"])
    assert not (tmp_path / "outside").exists()
    assert not (tmp_path / "outside.md").exists()
    assert (root / "sample.txt").read_text(encoding="utf-8") == "untouched"
    engine.perform.assert_not_called()


@pytest.mark.parametrize(
    "operation", ["gmail.send", "windows.shutdown", "project.run", "helper.ask"]
)
def test_direct_execution_cannot_bypass_private_preview_boundary(action_env, tmp_path, operation):
    client, _, engine, state = action_env
    action, _, _ = private_action(action_env, operation, tmp_path)
    with engine.factory() as session:
        row = session.get(ActionRequest, action["id"])
    with pytest.raises(ActionPolicyError, match="cannot execute changes"):
        client.portal.call(engine.perform, row)
    assert not state["writes"]


def test_invalid_preview_arguments_report_field_names_not_sensitive_values(action_env, tmp_path):
    client, _, engine, state = action_env
    action, _, _ = private_action(action_env, "gmail.send", tmp_path)
    secret = "DO_NOT_LOG_PRIVATE_HEADER\r\nInjected"
    with engine.factory() as session:
        row = session.get(ActionRequest, action["id"])
        row.arguments = {**row.arguments, "to": secret}
        session.commit()
    with pytest.raises(ActionPolicyError) as error:
        client.portal.call(engine.maybe_execute, action["id"])
    assert "Invalid preview arguments: to" in str(error.value)
    assert "DO_NOT_LOG" not in str(error.value)
    assert not state["writes"]


def test_missing_project_read_does_not_create_a_default_workspace(tmp_path):
    from mavo.projects import Projects

    root = tmp_path / "not-created"
    projects = Projects(default_root=root)
    with pytest.raises(ValueError, match="Create it first"):
        projects.read({"project": "Missing project"}, {"folders": []})
    assert not root.exists()
