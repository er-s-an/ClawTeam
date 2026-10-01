"""Regression coverage for dependencies that completed before being attached."""

import json
from contextlib import contextmanager

import pytest
from typer.testing import CliRunner

from clawteam.cli.commands import app
from clawteam.team.models import TaskStatus
from clawteam.team.tasks import TaskStore


@pytest.mark.parametrize("operation", ["create", "update"])
def test_completed_dependency_does_not_block_task(team_name, operation):
    store = TaskStore(team_name)
    prerequisite = store.create("implement feature")
    store.update(prerequisite.id, status=TaskStatus.completed)

    if operation == "create":
        task = store.create("review feature", blocked_by=[prerequisite.id])
    else:
        task = store.create("review feature")
        task = store.update(task.id, add_blocked_by=[prerequisite.id])

    assert task.status == TaskStatus.pending
    assert task.blocked_by == []
    persisted = store.get(task.id)
    assert persisted.status == TaskStatus.pending
    assert persisted.blocked_by == []


@pytest.mark.parametrize("operation", ["create", "update"])
def test_completed_dependency_is_removed_from_mixed_dependencies(team_name, operation):
    store = TaskStore(team_name)
    completed = store.create("completed prerequisite")
    active = store.create("active prerequisite")
    store.update(completed.id, status=TaskStatus.completed)
    dependencies = [completed.id, active.id, "not-created-yet"]

    if operation == "create":
        task = store.create("review feature", blocked_by=dependencies)
    else:
        task = store.create("review feature")
        task = store.update(task.id, add_blocked_by=dependencies)

    assert task.status == TaskStatus.blocked
    assert task.blocked_by == [active.id, "not-created-yet"]
    store.update(active.id, status=TaskStatus.completed)
    task = store.get(task.id)
    assert task.status == TaskStatus.blocked
    assert task.blocked_by == ["not-created-yet"]
    assert dependencies == [completed.id, active.id, "not-created-yet"]


def test_adding_completed_dependency_preserves_existing_blocker(team_name):
    store = TaskStore(team_name)
    active = store.create("still running")
    completed = store.create("already done")
    store.update(completed.id, status=TaskStatus.completed)
    task = store.create("needs both", blocked_by=[active.id])

    updated = store.update(task.id, add_blocked_by=[completed.id])
    assert updated.status == TaskStatus.blocked
    assert updated.blocked_by == [active.id]

    store.update(active.id, status=TaskStatus.completed)
    assert store.get(task.id).status == TaskStatus.pending
    assert store.get(task.id).blocked_by == []


@pytest.mark.parametrize("status", [TaskStatus.in_progress, TaskStatus.completed])
def test_adding_completed_dependency_preserves_task_status(team_name, status):
    store = TaskStore(team_name)
    prerequisite = store.create("already done")
    store.update(prerequisite.id, status=TaskStatus.completed)
    task = store.create("work")
    store.update(task.id, status=status, caller="worker")

    updated = store.update(task.id, add_blocked_by=[prerequisite.id])
    assert updated.status == status
    assert updated.blocked_by == []
    if status == TaskStatus.in_progress:
        assert updated.locked_by == "worker"


def test_create_reads_dependency_status_inside_write_lock(team_name, monkeypatch):
    store = TaskStore(team_name)
    other_store = TaskStore(team_name)
    prerequisite = store.create("finishing prerequisite")
    original_lock = store._write_lock

    @contextmanager
    def finish_before_lock():
        # Reproduce another writer completing while create waits for the lock.
        other_store.update(prerequisite.id, status=TaskStatus.completed)
        with original_lock():
            yield

    monkeypatch.setattr(store, "_write_lock", finish_before_lock)
    task = store.create("review feature", blocked_by=[prerequisite.id])

    assert task.status == TaskStatus.pending
    assert store.get(task.id).blocked_by == []


@pytest.mark.parametrize("operation", ["create", "update"])
def test_cli_attaches_completed_dependency_without_blocking(team_name, operation):
    runner = CliRunner()

    def invoke(*args):
        result = runner.invoke(app, ["--json", "task", *args])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    prerequisite = invoke("create", team_name, "implement feature")
    invoke("update", team_name, prerequisite["id"], "--status", "completed")
    if operation == "create":
        task = invoke(
            "create", team_name, "review feature", "--blocked-by", prerequisite["id"],
        )
    else:
        task = invoke("create", team_name, "review feature")
        task = invoke(
            "update", team_name, task["id"], "--add-blocked-by", prerequisite["id"],
        )

    persisted = invoke("get", team_name, task["id"])
    assert persisted["status"] == "pending"
    assert persisted["blockedBy"] == []
