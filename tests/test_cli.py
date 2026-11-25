"""CLI tests using Typer's CliRunner across JSON and SQLite backends."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from todo.cli import app

runner = CliRunner()


@pytest.mark.parametrize("backend,filename", [("json", "tasks.json"), ("sqlite", "tasks.db")])
def test_cli_crud_filter_sort_undo_reminders_export(
    tmp_path: Path, backend: str, filename: str
) -> None:
    db_file = tmp_path / filename
    base = ["--backend", backend, "--db", str(db_file)]

    res = runner.invoke(
        app,
        [
            *base,
            "add",
            "buy milk",
            "--due",
            "2026-12-01",
            "--priority",
            "high",
            "--tag",
            "work",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "#1" in res.output

    res2 = runner.invoke(
        app,
        [
            *base,
            "add",
            "overdue task",
            "--due",
            "2020-01-01",
            "--priority",
            "low",
            "--tag",
            "work",
            "--repeat",
            "weekly",
        ],
    )
    assert res2.exit_code == 0, res2.output

    list_res = runner.invoke(app, [*base, "list", "--tag", "work", "--sort", "due"])
    assert list_res.exit_code == 0, list_res.output
    assert "OVERDUE" in list_res.output
    assert list_res.output.index("overdue task") < list_res.output.index("buy milk")

    done_res = runner.invoke(app, [*base, "done", "2"])
    assert done_res.exit_code == 0, done_res.output
    assert "Scheduled next weekly occurrence" in done_res.output
    assert "#3" in done_res.output

    pending_res = runner.invoke(app, [*base, "list", "--pending"])
    assert pending_res.exit_code == 0
    assert "buy milk" in pending_res.output

    done_only_res = runner.invoke(app, [*base, "list", "--done"])
    assert done_only_res.exit_code == 0
    assert "overdue task" in done_only_res.output

    edit_res = runner.invoke(app, [*base, "edit", "1", "--title", "buy oat milk", "--priority", "low"])
    assert edit_res.exit_code == 0
    assert "buy oat milk" in edit_res.output

    undo_res = runner.invoke(app, [*base, "undo"])
    assert undo_res.exit_code == 0

    reopen_res = runner.invoke(app, [*base, "reopen", "2"])
    assert reopen_res.exit_code == 0

    rem_res = runner.invoke(app, [*base, "reminders", "--days", "30"])
    assert rem_res.exit_code == 0
    assert "Overdue" in rem_res.output

    csv_file = tmp_path / "out.csv"
    exp_csv = runner.invoke(app, [*base, "export", "--format", "csv", "--output", str(csv_file)])
    assert exp_csv.exit_code == 0
    assert "buy milk" in csv_file.read_text(encoding="utf-8")

    exp_md = runner.invoke(app, [*base, "export", "--format", "markdown", "--style", "table"])
    assert exp_md.exit_code == 0
    assert "| ID | Status | Title |" in exp_md.output

    del_res = runner.invoke(app, [*base, "delete", "1"])
    assert del_res.exit_code == 0


def test_cli_error_cases(tmp_path: Path) -> None:
    bad_json = tmp_path / "broken.json"
    bad_json.write_text("{corrupt", encoding="utf-8")

    res_corrupt = runner.invoke(app, ["--backend", "json", "--db", str(bad_json), "list"])
    assert res_corrupt.exit_code == 1
    assert "corrupted" in res_corrupt.output.lower()

    valid_json = tmp_path / "tasks.json"
    res_conflict = runner.invoke(
        app, ["--backend", "json", "--db", str(valid_json), "list", "--pending", "--done"]
    )
    assert res_conflict.exit_code == 1
    assert "Cannot combine" in res_conflict.output

    res_missing = runner.invoke(app, ["--backend", "json", "--db", str(valid_json), "done", "42"])
    assert res_missing.exit_code == 1
    assert "#42" in res_missing.output


def test_cli_config_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file = tmp_path / "cfg.toml"
    monkeypatch.setenv("TODO_CONFIG", str(cfg_file))

    save_res = runner.invoke(app, ["config", "--backend", "sqlite", "--path", str(tmp_path / "my.db")])
    assert save_res.exit_code == 0
    assert cfg_file.exists()

    show_res = runner.invoke(app, ["config"])
    assert show_res.exit_code == 0
    assert "sqlite" in show_res.output
