"""Passive-loop liveness and current errors are distinct from trade readiness."""
import json

import pytest

from test_execution import bridge


class EndFixture(BaseException):
    pass


@pytest.mark.parametrize('fails', [False, True])
def test_every_bridge_loop_updates_heartbeat_and_preserves_evidence(tmp_path, monkeypatch, fails):
    state = tmp_path / 'state.json'
    history = {'last_event_id': 'historical-event', 'last_error': 'historical-failure',
               'last_success_at': '2000-01-01T00:00:00+00:00'}
    state.write_text(json.dumps(history))
    monkeypatch.setattr(bridge, 'STATE', state)
    monkeypatch.setattr(bridge, 'OUT', tmp_path / 'outbox')
    monkeypatch.setattr(bridge, 'LATEST', tmp_path / 'outbox/latest.json')
    monkeypatch.setattr(bridge, 'reconcile_obligations', lambda: None)
    monkeypatch.setattr(bridge, 'report_error', lambda error: None)

    def get(url):
        if fails:
            raise OSError('SECRET must not enter state')
        return {'signal': None}

    def stop(delay):
        raise EndFixture()

    monkeypatch.setattr(bridge, 'execute_next', lambda: get(None))
    monkeypatch.setattr(bridge.time, 'sleep', stop)
    with pytest.raises(EndFixture):
        bridge.main()
    result = json.loads(state.read_text())
    assert result['last_event_id'] == history['last_event_id']
    assert result['heartbeat_at']
    if fails:
        assert result['state'] == 'READ_UNAVAILABLE' and result['last_error'] == 'OSError'
        assert result['last_success_at'] == history['last_success_at']
        assert result['last_error_at']
    else:
        assert result['state'] == 'PASSIVE_LOOP' and result['last_error'] is None
        assert result['last_success_at'] != history['last_success_at']
    assert 'SECRET' not in state.read_text()
    assert not bridge.LATEST.exists()

@pytest.mark.parametrize('diagnostic_fails',[False,True])
def test_fatal_write_retains_nonzero_exit_and_only_safe_diagnostic(tmp_path,monkeypatch,diagnostic_fails):
    log=tmp_path/'bridge_fatal.log'
    monkeypatch.setattr(bridge,'FATAL_LOG',log)
    monkeypatch.setattr(bridge,'PHASE','HEARTBEAT_WRITE')
    def fails():
        error=PermissionError(13,'SECRET credentials must never enter logs')
        error.winerror=32
        raise error
    monkeypatch.setattr(bridge,'main',fails)
    if diagnostic_fails:
        log.mkdir()
    assert bridge.run_main() == 1
    if not diagnostic_fails:
        record=json.loads(log.read_text())
        assert record['phase']=='HEARTBEAT_WRITE' and record['error_class']=='PermissionError'
        assert record['errno']==13 and record['winerror']==32
        assert 'SECRET' not in log.read_text()

def test_actual_heartbeat_replace_exhaustion_becomes_fatal_exit(tmp_path,monkeypatch):
    from pathlib import Path
    state=tmp_path/'state.json';state.write_text('{"last_event_id":null}')
    log=tmp_path/'fatal.log'
    monkeypatch.setattr(bridge,'STATE',state);monkeypatch.setattr(bridge,'FATAL_LOG',log)
    monkeypatch.setattr(bridge,'OUT',tmp_path/'out')
    monkeypatch.setattr(bridge,'reconcile_obligations',lambda:None)
    monkeypatch.setattr(bridge,'execute_next',lambda:{'signal':None})
    def denied(self,path):
        error=PermissionError(13,'SECRET');error.winerror=5;raise error
    monkeypatch.setattr(Path,'replace',denied)
    assert bridge.run_main()==1
    record=json.loads(log.read_text())
    assert record['phase']=='HEARTBEAT_WRITE' and record['winerror']==5
    assert json.loads(state.read_text())=={'last_event_id':None}
