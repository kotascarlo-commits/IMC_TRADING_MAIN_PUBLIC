"""Admission negatives, not forward trades or performance evidence."""
import json

import pytest
from mykonos_meme_scout import live_parity_preflight as pre


def inputs():
    return (pre.read(pre.ROOT / 'RUNTIME/current-system-state.json'),
            pre.read(pre.ROOT / 'config/imc-execution.json'),
            pre.read(pre.ROOT / 'config/owner-broadcast-gate.json'),
            (pre.ROOT / 'TOOLS/imc_jupiter_prepare_worker.py').read_text(encoding='utf-8'))


def test_current_contract_returns_blockers_without_importing_transport():
    state, execution, owner, worker = inputs()
    result = pre.validate(state, execution, owner, worker)
    assert result[0]['code'] == 'CREATOR_SECURITY_PROVIDER_NO_RESULT'
    assert state['LIVE_PARITY_READY'] is False


@pytest.mark.parametrize('field', list(pre.EXPECTED))
def test_missing_canonical_field_cannot_start(field):
    state, execution, owner, worker = inputs()
    del state[field]
    with pytest.raises(ValueError, match='canonical_state_rejected'):
        pre.validate(state, execution, owner, worker)


@pytest.mark.parametrize('field', ['EXECUTION_PROVIDER', 'GMGN_EXECUTION', 'GMGN_BROADCAST', 'GMGN_RUNTIME_DEPENDENCY'])
def test_active_gmgn_or_fallback_is_rejected(field):
    state, execution, owner, worker = inputs()
    state[field] = 'GMGN'
    with pytest.raises(ValueError, match='canonical_state_rejected'):
        pre.validate(state, execution, owner, worker)


@pytest.mark.parametrize('field', ['signing', 'broadcast_enabled', 'live_trading_enabled', 'wallet_access'])
def test_real_authority_cannot_pass(field):
    state, execution, owner, worker = inputs()
    execution[field] = True
    with pytest.raises(ValueError, match='real_authority_rejected'):
        pre.validate(state, execution, owner, worker)


@pytest.mark.parametrize('unsafe', ['import gmgn', 'from solders.keypair import Keypair',
                                  'import imc_local_wallet_verify', 'sendTransaction()'])
def test_unsafe_active_worker_is_rejected_before_import(unsafe):
    state, execution, owner, worker = inputs()
    with pytest.raises(ValueError, match='active_execution_transport_rejected'):
        pre.validate(state, execution, owner, worker + '\n' + unsafe)


def test_modified_rpc_allowlist_is_rejected():
    state, execution, owner, worker = inputs()
    with pytest.raises(ValueError, match='read_only_rpc_allowlist_rejected'):
        pre.validate(state, execution, owner, worker.replace('"getBlockHeight"', '"requestAirdrop"'))


@pytest.mark.parametrize('flag', [0, 'false', None, True])
def test_false_flags_require_actual_boolean_false(flag):
    state, execution, owner, worker = inputs()
    state['PRIVATE_KEY_ACCESS'] = flag
    with pytest.raises(ValueError, match='canonical_state_rejected'):
        pre.validate(state, execution, owner, worker)


def test_readiness_label_cannot_activate_unaccepted_runtime():
    state, execution, owner, worker = inputs()
    state['EVIDENCE_STATUS'] = 'READY'; state['LIVE_PARITY_READY'] = True
    with pytest.raises(ValueError, match='runtime_not_accepted'):
        pre.validate(state, execution, owner, worker)


def test_duplicate_execution_provider_is_ambiguous(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text('{"EXECUTION_PROVIDER":"JUPITER","EXECUTION_PROVIDER":"GMGN"}')
    with pytest.raises(ValueError, match='ambiguous_canonical_state'):
        pre.read(path)


@pytest.mark.parametrize('index', [0, 1, 2])
@pytest.mark.parametrize('invalid', [None, [], 'OFF'])
def test_non_object_configuration_is_rejected(index, invalid):
    args = list(inputs()); args[index] = invalid
    with pytest.raises(ValueError, match='configuration_object_required'):
        pre.validate(*args)


@pytest.mark.parametrize('invalid', [[None], 'blocked', [{}], [{'code': 'X', 'reason': None}]])
def test_invalid_blockers_are_rejected(invalid):
    state, execution, owner, worker = inputs()
    state['BLOCKERS'] = invalid
    with pytest.raises(ValueError, match='blocker_objects_required'):
        pre.validate(state, execution, owner, worker)


@pytest.mark.parametrize('broken', ['null_config', 'null_blocker'])
def test_invalid_config_still_produces_blocked_report(tmp_path, monkeypatch, broken):
    state, execution, owner, worker = inputs()
    if broken == 'null_config': execution = None
    else: state['BLOCKERS'] = [None]
    for directory in ('RUNTIME', 'config', 'TOOLS'):
        (tmp_path / directory).mkdir()
    for relative, value in [('RUNTIME/current-system-state.json', state),
                            ('config/imc-execution.json', execution),
                            ('config/owner-broadcast-gate.json', owner)]:
        (tmp_path / relative).write_text(json.dumps(value), encoding='utf-8')
    (tmp_path / 'TOOLS/imc_jupiter_prepare_worker.py').write_text(worker, encoding='utf-8')
    monkeypatch.setattr(pre, 'ROOT', tmp_path)
    monkeypatch.setattr('sys.argv', ['preflight'])
    assert pre.main() == 2
    reports = list((tmp_path / 'RUNTIME/test-runs').glob('*/live-parity-report.json'))
    assert len(reports) == 1
    report = pre.read(reports[0])
    assert report['status'] == 'BLOCKED' and report['preflight_safety'] == 'FAIL'
    assert report['runtime_started'] is False and report['LIVE_PARITY_CLOSED'] == 0


def test_launcher_uses_isolated_stdlib_version_check_only():
    source = (pre.ROOT / 'START_IMC_LIVE_PARITY.ps1').read_text(encoding='utf-8')
    assert '$python=Get-IMCPythonPath' in source
    assert '$python=Get-IMCPython\n' not in source
    assert '& $python -I -S -c' in source and '& $python -I -S (Join-Path' in source
