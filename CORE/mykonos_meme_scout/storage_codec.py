"""Lossless primitives for offline storage normalization; no database writes.

An unsafe or ambiguous JSON split returns None so the caller keeps the full
original value. Row fingerprints include SQLite value types and boundaries.
"""
from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Iterable


def _unique_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('ambiguous_json_key')
        result[key]=value
    return result


def _invalid_constant(value):
    raise ValueError('nonstandard_json_constant')


_DECODER=json.JSONDecoder(object_pairs_hook=_unique_pairs,parse_constant=_invalid_constant)
_WHITESPACE=' \t\r\n'


def _skip_space(raw,index):
    while index<len(raw) and raw[index] in _WHITESPACE:index+=1
    return index


def split_snapshot_json(raw):
    """Return original prefix/value/suffix for one top-level captured_at.

    No timestamp comes from another column, and nothing is reserialized.
    Escaped keys are decoded for recognition only. BLOBs and ambiguous or
    malformed documents retain their original full-value representation.
    """
    if type(raw) is not str:return None
    try:
        start=_skip_space(raw,0)
        document,end=_DECODER.raw_decode(raw,start)
        if (_skip_space(raw,end)!=len(raw) or type(document) is not dict
                or type(document.get('captured_at')) is not str):return None
        index=_skip_space(raw,start+1)
        while index<len(raw) and raw[index]!='}':
            key,index=_DECODER.raw_decode(raw,index)
            if type(key) is not str:return None
            index=_skip_space(raw,index)
            if raw[index]!=':':return None
            value_start=_skip_space(raw,index+1)
            _,value_end=_DECODER.raw_decode(raw,value_start)
            if key=='captured_at':
                parts=(raw[:value_start],raw[value_start:value_end],raw[value_end:])
                rebuilt=''.join(parts)
                if rebuilt!=raw or rebuilt.encode('utf-8')!=raw.encode('utf-8'):return None
                return parts
            index=_skip_space(raw,value_end)
            if raw[index]==',':index=_skip_space(raw,index+1)
            elif raw[index]!='}':return None
        return None
    except (ValueError,TypeError,IndexError,RecursionError,UnicodeError):
        return None


def sqlite_value_frame(value):
    """An unambiguous type/byte frame for a value read back from SQLite."""
    if value is None:tag,body=b'N',b''
    elif type(value) is int:tag,body=b'I',str(value).encode('ascii')
    elif type(value) is float:tag,body=b'F',struct.pack('>d',value)
    elif type(value) is str:tag,body=b'T',value.encode('utf-8')
    elif type(value) is bytes:tag,body=b'B',value
    else:raise TypeError('unsupported_sqlite_value_type')
    return tag+len(body).to_bytes(8,'big')+body


def dictionary_key(*values):
    """Length-framed lookup key; callers still compare original values.

    A digest match alone must never establish equality or authorize dedup.
    """
    digest=hashlib.sha256(b'IMC_STORAGE_DICTIONARY_1\0')
    digest.update(len(values).to_bytes(8,'big'))
    for value in values:digest.update(sqlite_value_frame(value))
    return digest.hexdigest()


def fingerprint_rows(rows: Iterable,*,table: str):
    """Hash ordered rows, including any required hidden rowid supplied.

    The caller fixes ordering and source snapshot; this function neither
    opens a database nor establishes a writer freeze or physical backup.
    """
    if type(table) is not str or not table:raise ValueError('invalid_fingerprint_table')
    digest=hashlib.sha256(b'IMC_STORAGE_ROWS_1\0'+sqlite_value_frame(table))
    count=0
    for row in rows:
        values=tuple(row)
        digest.update(len(values).to_bytes(8,'big'))
        for value in values:digest.update(sqlite_value_frame(value))
        count+=1
    return {'rows':count,'sha256':digest.hexdigest()}
