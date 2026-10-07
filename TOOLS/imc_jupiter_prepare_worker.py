"""One bounded keyless build GET or read/simulation RPC. No send method exists."""
import http.client
import json
import sys
import base64
import re
from urllib.parse import urlencode

LIMIT=1048576
RPC_METHODS={"getAccountInfo","getMultipleAccounts","getLatestBlockhash","getFeeForMessage","simulateTransaction","getBlockHeight"}


def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise ValueError("duplicate_field")
        result[key]=value
    return result


def pubkey(value):
    alphabet="123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    if not isinstance(value,str) or not 32<=len(value)<=44 or any(c not in alphabet for c in value):
        raise ValueError("invalid_public_key")
    number=0
    for char in value:number=number*58+alphabet.index(char)
    if (number.bit_length()+7)//8+len(value)-len(value.lstrip("1"))!=32:
        raise ValueError("invalid_public_key")
    return value


def validate(message):
    if not isinstance(message,dict):raise ValueError("invalid_request")
    operation=message.get("operation")
    if operation=="build":
        query=message.get("query")
        fields={"inputMint","outputMint","amount","taker","slippageBps","mode"}
        if (set(message)!={"operation","query"} or not isinstance(query,dict) or
                set(query) not in (fields, fields|{'dexes'}) or
                'dexes' in query and query['dexes'] not in ('Whirlpool','Raydium CLMM')):
            raise ValueError("invalid_build_query")
        for key in ("inputMint","outputMint","taker"):pubkey(query[key])
        if (query["inputMint"]==query["outputMint"] or not isinstance(query["amount"],str) or
            not re.fullmatch(r"[1-9][0-9]{0,19}",query["amount"]) or int(query["amount"])>=2**64 or
            type(query["slippageBps"]) is not int or not 0<=query["slippageBps"]<=150 or query["mode"]!="fast"):
            raise ValueError("invalid_build_query")
    elif operation=="rpc":
        payload=message.get("payload")
        if (set(message)!={"operation","payload"} or not isinstance(payload,dict) or
            set(payload)!={"jsonrpc","id","method","params"} or payload["method"] not in RPC_METHODS or
            type(payload["id"]) is not int or payload["id"]!=1 or payload["jsonrpc"]!="2.0"):
            raise ValueError("read_only_rpc_required")
        method,params=payload["method"],payload["params"]
        if not isinstance(params,list):raise ValueError("invalid_rpc_params")
        if method in {"getLatestBlockhash","getBlockHeight"}:
            if params!=[{"commitment":"finalized"}]:raise ValueError("finalized_required")
        elif method=="getAccountInfo":
            if len(params)!=2 or params[1]!={"encoding":"base64","commitment":"finalized"}:raise ValueError("finalized_required")
            pubkey(params[0])
        elif method=="getMultipleAccounts":
            if (len(params)!=2 or params[1]!={"encoding":"base64","commitment":"finalized"}
                    or not isinstance(params[0],list) or not 1<=len(params[0])<=64
                    or len(set(params[0]))!=len(params[0])):
                raise ValueError('bounded_finalized_accounts_required')
            for key in params[0]:pubkey(key)
        else:
            expected={"commitment":"finalized"} if method=="getFeeForMessage" else {
                "encoding":"base64","sigVerify":False,"replaceRecentBlockhash":False,"commitment":"finalized"}
            if len(params)!=2 or not isinstance(params[1],dict) or set(params[1])!=set(expected) or any(
                type(params[1][k]) is not type(v) or params[1][k]!=v for k,v in expected.items()):
                raise ValueError("unsigned_finalized_required")
            if not isinstance(params[0],str) or len(base64.b64decode(params[0],validate=True))>1232:
                raise ValueError("invalid_transaction_data")
    else:raise ValueError("operation_blocked")
    return operation


def read(message,*,connection=None):
    operation=validate(message)
    own_connection=connection is None
    if connection is not None and operation!='rpc':raise ValueError('rpc_connection_only')
    if operation=="build":
        query=message.get("query")
        if not isinstance(query,dict):
            raise ValueError("invalid_build_query")
        connection=http.client.HTTPSConnection("api.jup.ag",timeout=6)
        method,path,body="GET","/swap/v2/build?"+urlencode(query),None
    elif operation=="rpc":
        payload=message.get("payload")
        if not isinstance(payload,dict) or payload.get("method") not in RPC_METHODS or payload.get("id")!=1 or payload.get("jsonrpc")!="2.0":
            raise ValueError("read_only_rpc_required")
        if payload["method"]=="simulateTransaction":
            options=payload["params"][1]
            if options.get("sigVerify") is not False or options.get("replaceRecentBlockhash") is not False:
                raise ValueError("unsigned_simulation_required")
        if connection is None:connection=http.client.HTTPSConnection("api.mainnet-beta.solana.com",timeout=6)
        method,path,body="POST","/",json.dumps(payload)
    else:
        raise ValueError("operation_blocked")
    try:
        connection.request(method,path,body,headers={"Accept":"application/json","Content-Type":"application/json"})
        response=connection.getresponse()
        if response.status!=200:
            return {"error_class":"HTTP_STATUS","http_status":response.status}
        if (response.getheader("Content-Type","").split(";",1)[0].strip().lower()!="application/json" or
            response.getheader("Content-Encoding","identity").lower()!="identity"):
            raise ValueError("response_format_unknown")
        raw=response.read(LIMIT+1)
        if len(raw)>LIMIT:
            raise ValueError("response_too_large")
        value=json.loads(raw,object_pairs_hook=unique)
        if not isinstance(value,dict):raise ValueError("response_schema_unknown")
        if operation=="rpc":
            if value.get("jsonrpc")!="2.0" or type(value.get("id")) is not int or value["id"]!=1:
                raise ValueError("rpc_envelope_invalid")
            if "error" in value:
                code=value["error"].get("code")
                return {"error_class":"RPC_ERROR","rpc_code":code if type(code) is int else None}
        elif value.get("error"):
            return {"error_class":"BUILD_UNAVAILABLE"}
        return {"result":value}
    finally:
        if own_connection:connection.close()


def rpc_session_main():
    connection=http.client.HTTPSConnection('api.mainnet-beta.solana.com',timeout=2)
    try:
        for index in range(24):
            raw=sys.stdin.buffer.readline(4097)
            if not raw:return 0
            if not raw.endswith(b'\n') or len(raw)>4096:raise ValueError('rpc_session_frame_unknown')
            message=json.loads(raw,object_pairs_hook=unique)
            if message.get('operation')!='rpc' or index>0 and connection.sock is None:
                raise ValueError('rpc_session_terminal')
            result=read(message,connection=connection)
            output=json.dumps({'index':index,'response':result},separators=(',',':'))
            if len(output)>LIMIT:raise ValueError('rpc_session_response_unbounded')
            sys.stdout.buffer.write(output.encode()+b'\n');sys.stdout.buffer.flush()
            if 'result' not in result:return 1
        return 0
    except Exception:return 1
    finally:connection.close()


if __name__=="__main__":
    if sys.argv[1:]==['--rpc-session']:raise SystemExit(rpc_session_main())
    try:
        raw=sys.stdin.buffer.read(262145)
        if len(raw)>262144:
            raise ValueError("request_too_large")
        result=read(json.loads(raw,object_pairs_hook=unique))
    except Exception:
        result={"error_class":"READ_UNAVAILABLE"}
    output=json.dumps(result,separators=(",",":"))
    if len(output)>LIMIT:result={'error_class':'READ_UNAVAILABLE'};output=json.dumps(result)
    print(output)
    raise SystemExit(0 if "result" in result else 1)
