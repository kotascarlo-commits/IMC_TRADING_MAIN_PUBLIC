"""Single stdlib RPC read. Parent owns cancellation; never starts children."""
import json
import sys
import urllib.request
import http.client
import time
import urllib.error
from urllib.parse import urlsplit

LIMIT = 4 * 1024 * 1024
METHODS = {'getBalance', 'getTokenAccountsByOwner', 'getTransaction'}
PROGRAMS = ('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
            'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb')


def u64(value):
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError('rpc_u64')
    return value


def pubkey(value):
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        raise ValueError('rpc_pubkey')
    number = 0
    for character in value:
        if character not in alphabet: raise ValueError('rpc_pubkey')
        number = number * 58 + alphabet.index(character)
    size = (number.bit_length() + 7) // 8
    if size + len(value) - len(value.lstrip('1')) != 32:
        raise ValueError('rpc_pubkey')
    return value


def validated_inventory(result, wallet, program, accounts, precision):
    """Stdlib validation before a harmless balance READ; parent validates again."""
    slot = u64(result['context']['slot'])
    if not isinstance(result.get('value'), list): raise ValueError('token_inventory_list')
    for entry in result['value']:
        address = pubkey(entry['pubkey'])
        if address in accounts: raise ValueError('duplicate_account')
        accounts.add(address)
        account = entry['account']
        if account.get('owner') != program or account.get('executable') is not False:
            raise ValueError('token_program')
        parsed = account['data']['parsed']; info = parsed['info']
        if parsed.get('type') != 'account' or info.get('owner') != wallet or info.get('state') != 'initialized':
            raise ValueError('token_owner_state')
        extensions = info.get('extensions')
        if extensions and (program != PROGRAMS[1] or extensions != [{'extension':'immutableOwner'}]):
            raise ValueError('token_extensions')
        mint = pubkey(info['mint']); amount = info['tokenAmount']; digits = amount['amount']
        if not isinstance(digits,str) or not digits or not digits.isascii() or not digits.isdecimal():
            raise ValueError('token_amount')
        u64(int(digits)); decimals = u64(amount['decimals'])
        if decimals > 18 or (mint in precision and precision[mint] != decimals):
            raise ValueError('token_precision')
        precision[mint] = decimals
    return slot


class HTTPReadError(ValueError):
    def __init__(self, status):
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        super().__init__('http_status')


def envelope(value, expected_id):
    if (not isinstance(value, dict) or value.get('jsonrpc') != '2.0'
            or type(value.get('id')) is not int or value['id'] != expected_id
            or (('result' in value) == ('error' in value))):
        raise ValueError('envelope')
    if 'error' in value:
        error = value['error']
        if not isinstance(error, dict) or type(error.get('code')) is not int:
            raise ValueError('error_envelope')
        safe = {'code': error['code']}
        details = error.get('data')
        if isinstance(details, dict) and 'contextSlot' in details:
            slot = details['contextSlot']
            if type(slot) is not int or not 0 <= slot < 2**64:
                raise ValueError('error_context_slot')
            safe['context_slot'] = slot
        return {'jsonrpc': '2.0', 'id': expected_id, 'error': safe}
    return value


def snapshot(message):
    """Three sequential READs on one connection; balance retains token floor."""
    payload = message['payload']
    wallet, minimum = payload['wallet'], payload['minimum_slot']
    if not isinstance(wallet, str) or not wallet or type(minimum) is not int or minimum < 0:
        raise ValueError('snapshot_request')
    url = urlsplit(message['url'])
    if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError('rpc_url')
    connection_type = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
    connection = connection_type(url.hostname, url.port, timeout=message['timeout'])
    path = (url.path or '/') + ('?' + url.query if url.query else '')
    replies, body_size = [], 0
    try:
        if payload['operation']=='wallet_snapshot_batch':
            allowed=payload.get('balance_retry_allowed')
            if type(allowed) is not bool:raise ValueError('balance_retry_budget')
            requests=[]
            for index in range(3):
                method='getTokenAccountsByOwner' if index<2 else 'getBalance'
                params=([wallet,{'programId':PROGRAMS[index]},
                    {'encoding':'jsonParsed','commitment':'finalized','minContextSlot':minimum}] if index<2
                    else [wallet,{'commitment':'finalized','minContextSlot':minimum}])
                if index==1:
                    method='getProgramAccounts'
                    params=[PROGRAMS[1],{'encoding':'jsonParsed','commitment':'finalized','minContextSlot':minimum,
                        'withContext':True,'filters':[{'memcmp':{'offset':32,'bytes':wallet}}]}]
                requests.append({'jsonrpc':'2.0','id':index+1,'method':method,'params':params})
            connection.request('POST',path,json.dumps(requests),{'Content-Type':'application/json'})
            response=connection.getresponse()
            if response.status!=200:raise HTTPReadError(response.status)
            body=response.read(LIMIT+1)
            if len(body)>LIMIT:raise ValueError('response_size')
            values=json.loads(body)
            if (not isinstance(values,list) or len(values)!=3 or any(not isinstance(v,dict)
                    or type(v.get('id')) is not int for v in values)
                    or {v['id'] for v in values}!={1,2,3}):raise ValueError('batch_envelope')
            by_id={v['id']:v for v in values}
            replies=[envelope(by_id[i],i) for i in (1,2,3)]
            slots=[];accounts=set();precision={};used=False
            for index,reply in enumerate(replies[:2]):
                if 'result' in reply:
                    slot=validated_inventory(reply['result'],wallet,PROGRAMS[index],accounts,precision)
                    if slot<minimum:raise ValueError('context_below_floor')
                    slots.append(slot)
            native=replies[2]; lag=False
            if 'result' in native:
                slot=u64(native['result']['context']['slot']);u64(native['result']['value'])
                if slot<minimum:raise ValueError('context_below_floor')
                lag=len(slots)==2 and slots[0]==slots[1] and slot==slots[0]-1
            else:lag=native['error']['code']==-32016
            if allowed and lag and len(slots)==2 and slots[0]==slots[1]:
                used=True  # Consumed before the extra I/O, including error/late responses.
                time.sleep(.2)  # Bounded bank-lag wait; parent owns the unchanged hard deadline.
                request={'jsonrpc':'2.0','id':3,'method':'getBalance','params':[
                    wallet,{'commitment':'finalized','minContextSlot':slots[0]}]}
                connection.request('POST',path,json.dumps(request),{'Content-Type':'application/json'})
                response=connection.getresponse()
                if response.status!=200:raise HTTPReadError(response.status)
                extra=response.read(LIMIT+1-len(body))
                if len(body)+len(extra)>LIMIT:raise ValueError('response_size')
                replies[2]=envelope(json.loads(extra),3)
                if 'result' in replies[2]:
                    u64(replies[2]['result']['value'])
                    if u64(replies[2]['result']['context']['slot'])<slots[0]:raise ValueError('context_below_floor')
            return {'snapshot_responses':replies,'balance_retry_used':used}
        for index in range(3):
            if index < 2:
                method = 'getTokenAccountsByOwner'
                params = [wallet, {'programId': PROGRAMS[index]},
                          {'encoding': 'jsonParsed', 'commitment': 'finalized', 'minContextSlot': minimum}]
            else:
                slots = [minimum]
                for reply in replies:
                    if 'result' in reply:
                        slot = reply['result']['context']['slot']
                        if type(slot) is not int or slot < minimum:
                            raise ValueError('context_slot')
                        slots.append(slot)
                method, params = 'getBalance', [wallet, {'commitment': 'finalized', 'minContextSlot': max(slots)}]
            request = {'jsonrpc': '2.0', 'id': index + 1, 'method': method, 'params': params}
            connection.request('POST', path, json.dumps(request), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                raise HTTPReadError(response.status)
            body = response.read(LIMIT + 1 - body_size)
            body_size += len(body)
            if body_size > LIMIT:
                raise ValueError('response_size')
            reply = envelope(json.loads(body), index + 1)
            if index < 2 and 'result' in reply and not isinstance(reply['result'].get('value'), list):
                raise ValueError('token_inventory_list')
            replies.append(reply)
        return {'snapshot_responses': replies}
    finally:
        connection.close()


def read(message):
    payload = message['payload']
    if isinstance(payload, dict) and payload.get('operation') in {'wallet_snapshot','wallet_snapshot_batch'}:
        value = snapshot(message)
        encoded = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
        if len(encoded) > LIMIT:
            raise ValueError('serialized_size')
        return encoded
    if (not isinstance(payload, dict) or payload.get('method') not in METHODS
            or payload.get('jsonrpc') != '2.0' or type(payload.get('id')) is not int
            or payload['id'] != 1 or not isinstance(payload.get('params'), list)):
        raise ValueError('read_only_rpc_required')
    request = urllib.request.Request(message['url'], data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=message['timeout']) as response:
        body = response.read(LIMIT + 1)
    if len(body) > LIMIT:
        raise ValueError('response_size')
    value = envelope(json.loads(body), 1)
    encoded = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > LIMIT:
        raise ValueError('serialized_size')
    return encoded


def main():
    try:
        message = sys.stdin.buffer.read(65537)
        if len(message) > 65536:
            raise ValueError('request_size')
        result = read(json.loads(message))
    except Exception as error:
        # Lossy, closed classification. Never serialize exception text/URL,
        # response bodies/headers or unknown attributes.
        status = (error.status if isinstance(error, HTTPReadError) else
                  error.code if isinstance(error, urllib.error.HTTPError) else None)
        if status is not None:
            kind = 'HTTP_STATUS'
        elif isinstance(error, TimeoutError):
            kind = 'TIMEOUT'
        elif isinstance(error, (ValueError, KeyError, TypeError, IndexError)):
            kind = 'RESPONSE_INVALID'
        else:
            kind = 'NETWORK'
        sys.stdout.buffer.write(json.dumps({'worker_failure': {'kind': kind, 'http_status': status}}).encode())
        return 1
    sys.stdout.buffer.write(result)
    return 0


if __name__ == '__main__':
    sys.exit(main())
