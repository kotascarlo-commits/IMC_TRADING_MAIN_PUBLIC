"""Raw source identity checks; no production database access or migration."""
import json
import sqlite3

import pytest

from mykonos_meme_scout.storage_codec import dictionary_key,fingerprint_rows,split_snapshot_json


@pytest.mark.parametrize('raw',[
    '{"captured_at":"2026-10-03T03:00:00Z","price":1}',
    ' \n{ "price" : 1.00, "captured_at" : "escaped\\u0032" , "utf8":"日本🦊"}\r\n',
    '{"nested":{"captured_at":"keep"},"captured\\u005fat":"top\\\"quoted"}',
    '{"price":1e-03,"captured_at":"a\\/b","nested":[1,true,null]}',
])
def test_split_keeps_every_source_byte_and_original_lexeme(raw):
    parts=split_snapshot_json(raw)
    assert parts is not None
    assert ''.join(parts).encode('utf-8')==raw.encode('utf-8')
    assert json.loads(parts[1])==json.loads(raw)['captured_at']
    # The lexical original, including escapes and whitespace, is preserved.
    assert parts[1] in raw and parts[1].startswith('"') and parts[1].endswith('"')


@pytest.mark.parametrize('raw',[
    '{"captured_at":"a","captured_at":"b"}',
    '{"captured_at":"a","captured\\u005fat":"b"}',
    '{"captured_at":"a","nested":{"x":1,"x":2}}',
    '{"captured_at":null}', '{"captured_at":2}', '{"other":"a"}',
    '[{"captured_at":"a"}]', '{"captured_at":"a"} trailing',
    '{"captured_at":"a","price":NaN}', '{"captured_at":"a",}',
    b'{"captured_at":"blob"}', b'\xff\x00', None,
])
def test_ambiguous_or_other_values_require_full_original_fallback(raw):
    assert split_snapshot_json(raw) is None


def test_different_capture_values_share_only_the_unchanged_fragments():
    first='{"captured_at":"first","price":2.00}'
    second='{"captured_at":"second","price":2.00}'
    p1,p2=split_snapshot_json(first),split_snapshot_json(second)
    assert p1[1]!=p2[1]
    assert (p1[0],p1[2])==(p2[0],p2[2])
    assert dictionary_key(p1[0],p1[2])==dictionary_key(p2[0],p2[2])
    assert dictionary_key('ab','c')!=dictionary_key('a','bc')
    assert dictionary_key('1')!=dictionary_key(1)
    assert dictionary_key('x')!=dictionary_key(b'x')


def test_row_fingerprint_preserves_sqlite_types_order_and_hidden_rowid():
    with sqlite3.connect(':memory:') as c:
        c.execute('CREATE TABLE registry(family TEXT,version TEXT,created TEXT,PRIMARY KEY(family,version))')
        c.executemany('INSERT INTO registry(rowid,family,version,created) VALUES(?,?,?,?)',[
            (3,'one','old','same'),(99,'two','new','same')])
        rows=c.execute('SELECT rowid,* FROM registry ORDER BY rowid').fetchall()
        original=fingerprint_rows(iter(rows),table='model_registry')
        assert original['rows']==2
        assert original!=fingerprint_rows([(1,*rows[0][1:]),(2,*rows[1][1:])],table='model_registry')
        assert original!=fingerprint_rows(reversed(rows),table='model_registry')
        assert original!=fingerprint_rows(rows,table='different')
        assert c.execute('SELECT version FROM registry ORDER BY created DESC,rowid DESC LIMIT 1').fetchone()[0]=='new'
    assert fingerprint_rows([(1,)],table='t')!=fingerprint_rows([('1',)],table='t')
    assert fingerprint_rows([(0.0,)],table='t')!=fingerprint_rows([(-0.0,)],table='t')
    assert fingerprint_rows([(b'\xff',None)],table='t')==fingerprint_rows([(b'\xff',None)],table='t')
    with pytest.raises(TypeError):dictionary_key(True)
