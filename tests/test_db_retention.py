"""The deprecated cleanup entry must never open or mutate a database."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "db_retention", Path(__file__).parents[1] / "scripts/db_retention.py")
db_retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db_retention)


def test_execute_rejected_before_audit_import(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "db_audit", SimpleNamespace(
        main=lambda _: pytest.fail("Audit reached despite execute refusal")))
    with pytest.raises(SystemExit) as exc:
        db_retention.main(["--days", "999999", "--execute"])
    assert exc.value.code == 2
    assert "No database opened" in capsys.readouterr().err


def test_compatibility_entry_uses_supervised_audit_and_forwards_status(monkeypatch, capsys):
    calls = []
    monkeypatch.setitem(sys.modules, "db_audit", SimpleNamespace(
        main=lambda args: calls.append(args) or 2))
    assert db_retention.main(["--days", "1"]) == 2
    assert calls == [[]]
    captured = capsys.readouterr()
    assert not captured.out
    assert "--days is ignored" in captured.err
