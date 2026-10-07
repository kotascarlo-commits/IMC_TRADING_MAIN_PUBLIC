"""Isolated source-boundary fixtures; never accepted creator/forward evidence."""
from datetime import datetime, timezone

import pytest
from mykonos_meme_scout.providers import GoPlusProvider
from mykonos_meme_scout import paper_forward as paper


@pytest.mark.parametrize('unqualified_source', [
    {'code': 5000, 'message': 'system error', 'result': None},
    {'message': 'Invalid credentials'},
    {'creator': 'AwknxcVV9u7Yox1LKNQzTkDMrTuJUpXbyc1ff5zPevWM',
     'creatorTokens': None, 'risks': [], 'rugged': False, 'score': 1},
    {'mint_authority_disabled': True, 'freeze_authority_disabled': True,
     'queried_owner_raw': '0', 'supply_reconciliation': 'PASS'},
])
def test_non_creator_assertions_cannot_become_creator_pass(unqualified_source):
    assert GoPlusProvider._normalize(unqualified_source).malicious_creator is None


def test_real_token_response_shape_does_not_gain_creator_flag():
    raw = {'creators': [], 'mintable': {'status': '0'}, 'freezable': {'status': '0'},
           'transfer_hook': [], 'non_transferable': '0', 'metadata_mutable': {'status': '0'}}
    result = GoPlusProvider._normalize(raw)
    assert result.mint_authority_disabled is True and result.freeze_authority_disabled is True
    assert result.malicious_creator is None and result.creator_holder_pct is None


@pytest.mark.parametrize('creator_flag,expected_status', [(None, 'UNKNOWN'), (True, 'FAIL')])
def test_strict_security_gate_blocks_unknown_or_positive_creator_risk(creator_flag, expected_status):
    now = datetime.now(timezone.utc)
    sec = {'checked': True, 'checked_at': now.isoformat(), 'mint_authority_disabled': True,
           'freeze_authority_disabled': True, 'non_transferable': False,
           'transfer_hook_present': False, 'malicious_creator': creator_flag,
           'top10_holder_pct': 1, 'creator_holder_pct': 0}
    current = {'address': 'FNgMfPgP6pHwxkqYYJT2jTzfzQgoSiCtEXZhGB13pump',
               'market_data_stale': False, 'security': sec, 'captured_at': now.isoformat()}
    evidence = {}
    failures = paper.brain(current, None, paper.load_config(), now.timestamp(), evidence=evidence)
    assert 'security:malicious_creator' in failures
    assert evidence['security_checks']['malicious_creator']['status'] == expected_status
    if creator_flag is None:
        assert evidence['security_complete'] is False
