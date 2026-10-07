"""Authenticated shim errors only; no credentials, network or live execution."""
import json
from types import SimpleNamespace

import pytest
from mykonos_meme_scout import paper_forward as paper


def evidence(**changes):
    return dict(origin='provider_response', code='GMGN_RATE_LIMITED', http_status=429,
                api_code=429, retry_after_seconds=120, quota_limit=5, quota_remaining=0,
                reset_source='header_and_body', api_error=None) | changes


def stderr(value):
    return 'SECRET provider response\nGMGN_READ_EVIDENCE=' + json.dumps(value)


def test_reader_keeps_only_typed_rate_evidence(monkeypatch):
    monkeypatch.setattr(paper.subprocess, 'run', lambda *a, **k:
        SimpleNamespace(returncode=125, stdout='SECRET', stderr=stderr(evidence())))
    with pytest.raises(paper.GMGNReadError) as caught:
        paper.GMGNReader({}).read('portfolio', 'info', '--raw')
    assert dict(caught.value.evidence) == evidence()
    assert str(caught.value) == 'gmgn_read_unavailable'
    assert 'SECRET' not in repr(caught.value) + repr(caught.value.evidence)
    with pytest.raises(TypeError):
        caught.value.evidence['retry_after_seconds'] = 0


@pytest.mark.parametrize('change', [
    {'retry_after_seconds': True}, {'retry_after_seconds': 0}, {'retry_after_seconds': -2},
    {'retry_after_seconds': 86401}, {'retry_after_seconds': '120'}, {'http_status': False},
    {'quota_remaining': 6}, {'quota_limit': 1000000001}, {'origin': 'unknown'},
    {'code': 'raw SECRET text'}, {'reset_source': 'SECRET'}, {'api_code': -1},
    {'origin': 'local_cooldown'}, {'http_status': None, 'api_code': None}, {'extra': 'SECRET'},
    {'code': 'GMGN_INTERNAL_ERROR'}, {'quota_remaining': []}, {'origin': []},
    {'api_error': 'SECRET'}, {'api_error': []}, {'api_error': True},
])
def test_invalid_evidence_becomes_unavailable_without_leaking(change):
    error = paper.GMGNReadError.from_stderr(stderr(evidence(**change)))
    assert error.evidence is None and str(error) == 'gmgn_read_unavailable'


@pytest.mark.parametrize('body', [
    '', 'SECRET', 'GMGN_READ_EVIDENCE=null', 'GMGN_READ_EVIDENCE=[]',
    'GMGN_READ_EVIDENCE={"code":"a","code":"b"}', 'x' * 4097,
    stderr(evidence()) + '\n' + stderr(evidence()),
])
def test_ambiguous_or_unbounded_error_never_supplies_retry_evidence(body):
    assert paper.GMGNReadError.from_stderr(body).evidence is None


@pytest.mark.parametrize('tag', ['RATE_LIMIT_EXCEEDED', 'RATE_LIMIT_BANNED', 'ERROR_RATE_LIMIT_BLOCKED'])
def test_provider_rate_enum_is_preserved_without_inferring_plan(tag):
    value = evidence(api_error=tag)
    assert dict(paper.GMGNReadError.from_stderr(stderr(value)).evidence) == value
    local = evidence(origin='local_cooldown', http_status=None, api_code=None,
                     quota_limit=None, quota_remaining=None, reset_source='unknown', api_error=tag)
    assert paper.GMGNReadError.from_stderr(stderr(local)).evidence is None


@pytest.mark.parametrize('seconds', [-1, 1, 86400])
def test_local_cooldown_schema_preserves_unknown_and_original_deadline(seconds):
    value = evidence(origin='local_cooldown', http_status=None, api_code=None,
                     quota_limit=None, quota_remaining=None, reset_source='unknown',
                     retry_after_seconds=seconds)
    assert dict(paper.GMGNReadError.from_stderr(stderr(value)).evidence) == value
