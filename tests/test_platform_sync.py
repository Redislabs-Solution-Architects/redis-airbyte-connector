import pytest
from types import SimpleNamespace

from scripts import platform_test


class StubAPI(platform_test.API):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, path, body):
        self.calls.append((path, body))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def test_next_sync_waits_for_connection_workflow_after_terminal_job(monkeypatch):
    sleeps = []
    monkeypatch.setattr(platform_test, 'time', SimpleNamespace(monotonic=lambda: 0, sleep=sleeps.append))
    api = StubAPI([platform_test.AirbyteHTTPError('connections/sync', 409), {'job': {'id': 42}}])
    assert api.sync('connection') == 42
    assert api.calls == [('connections/sync', {'connectionId': 'connection'})] * 2
    assert sleeps == [2]


@pytest.mark.parametrize('error', [
    *(platform_test.AirbyteHTTPError('connections/sync', code) for code in (400, 401, 403, 429, 500)),
    TimeoutError('Response lost; job acceptance is unknown'),
])
def test_sync_does_not_retry_ambiguous_or_nonconflict_failures(monkeypatch, error):
    monkeypatch.setattr(platform_test, 'time', SimpleNamespace(
        monotonic=lambda: 0, sleep=lambda _: pytest.fail('Must not retry')))
    api = StubAPI([error])
    with pytest.raises(type(error)) as caught:
        api.sync('connection')
    assert caught.value is error
    assert len(api.calls) == 1


def test_permanent_sync_conflict_has_a_bounded_wait(monkeypatch):
    ticks = iter([0, 61])
    monkeypatch.setattr(platform_test, 'time', SimpleNamespace(
        monotonic=lambda: next(ticks), sleep=lambda _: pytest.fail('Deadline has expired')))
    api = StubAPI([platform_test.AirbyteHTTPError('connections/sync', 409)])
    with pytest.raises(platform_test.AirbyteHTTPError, match='HTTP 409'):
        api.sync('connection', timeout=60)
    assert len(api.calls) == 1
