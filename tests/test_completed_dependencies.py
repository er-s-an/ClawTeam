"""Completed prerequisites must not block newly scheduled work."""

import json

import pytest
from typer.testing import CliRunner

from clawteam.cli.commands import app
from clawteam.team.models import TaskStatus
from clawteam.team.tasks import TaskStore


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("remaining", [False, True])
def test_adding_completed_dependency(operation, remaining, team_name):
    store = TaskStore(team_name)
    completed = store.create("Implementation")
    store.update(completed.id, status=TaskStatus.completed)
    outstanding = store.create("Other prerequisite")
    dependencies = [completed.id]
    if remaining:
        dependencies += [outstanding.id, "unknown-task"]

    if operation == "create":
        task = store.create("Review", blocked_by=dependencies)
    else:
        task = store.create("Review")
        task = store.update(task.id, add_blocked_by=dependencies)

    expected = [outstanding.id, "unknown-task"] if remaining else []
    assert task.blocked_by == expected
    assert task.status == (TaskStatus.blocked if remaining else TaskStatus.pending)
    loaded = TaskStore(team_name).get(task.id)
    assert loaded.blocked_by == expected
    assert loaded.status == task.status

    if remaining:
        store.update(outstanding.id, status=TaskStatus.completed)
        assert store.get(task.id).blocked_by == ["unknown-task"]
        assert store.get(task.id).status == TaskStatus.blocked


def test_cli_create_after_prerequisite_completed(team_name):
    runner = CliRunner()
    result = runner.invoke(app, ["--json", "task", "create", team_name, "Implementation"])
    assert result.exit_code == 0, result.output
    prerequisite_id = json.loads(result.output)["id"]
    result = runner.invoke(
        app, ["task", "update", team_name, prerequisite_id, "--status", "completed"]
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        ["--json", "task", "create", team_name, "Review", "--blocked-by", prerequisite_id],
    )
    assert result.exit_code == 0, result.output
    task = json.loads(result.output)
    assert task["status"] == "pending"
    assert task["blockedBy"] == []
