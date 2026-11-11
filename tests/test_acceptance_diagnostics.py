import importlib
import json
from pathlib import Path
from unittest.mock import Mock


def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    return importlib.import_module('scripts.acceptance_test')


def test_engine_failure_diagnostics_redact_fixture_credentials(tmp_path, monkeypatch, capsys):
    acceptance = harness(monkeypatch)
    folder = tmp_path / 'acceptance'
    folder.mkdir()
    (tmp_path / 'state.json').write_text(json.dumps({'password': 'db-secret', 'admin_password': 'admin-secret'}))
    (folder / 'config.json').write_text(json.dumps({'uri': 'rediss://user:db-secret@host/0'}))
    run = Mock(return_value=Mock(stdout='db-secret admin-secret rediss://user:db-secret@host/0', stderr='engine unavailable'))
    monkeypatch.setattr(acceptance.subprocess, 'run', run)
    acceptance.engine_diagnostics('owned-engine', folder)
    output = capsys.readouterr().err
    assert 'engine unavailable' in output
    assert '[REDACTED]' in output
    assert 'db-secret' not in output and 'admin-secret' not in output
    assert run.call_count == 2
    assert all(call.kwargs['timeout'] == 15 for call in run.call_args_list)


def test_engine_diagnostic_failure_is_safe_and_nonfatal(tmp_path, monkeypatch, capsys):
    acceptance = harness(monkeypatch)
    acceptance.engine_diagnostics('owned-engine', tmp_path)
    assert capsys.readouterr().err == 'Acceptance engine diagnostics unavailable.\n'
