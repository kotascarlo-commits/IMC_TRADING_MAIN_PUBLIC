"""Read-only Jupiter signature evidence for the existing receipt ledger.

Requires a durable pre-send V2 binding. Does not build, sign, send, retry,
authorize a trial, relax quality gates or invent a binding after timeout.
The production Claim/Send path is not yet accepted for this contract.
"""
import base64
import hashlib
import json
from datetime import datetime, timezone

from solders.message import to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction, TransactionError

from execution_state import StateError, age, timestamp, canonical
from execution_evidence import SolanaEvidence, read_budget
from mykonos_meme_scout.jupiter_simulation import associated, token_states, raw, u64, TOKEN, SOL

SOURCE = "JUPITER_FINALIZED_SIGNATURE_AND_EXACT_MESSAGE"
# Block timestamps have whole-second resolution and an independent node clock.
# This is a bounded observation skew, never a renewal of the send deadline.
MAX_CHAIN_SEND_SKEW_SECONDS = 2


def keys_digest(keys):
    return hashlib.sha256(json.dumps(keys,separators=(",", ":")).encode()).hexdigest()


def binding(event,*,now=None):
    """Reject an unbound attempt before any RPC I/O."""
    try:
        value=event["jupiter_binding"]
        required={"provider","contract_version","message_sha256","account_keys_sha256",
                  "signature","wallet","input_token","output_token","input_raw","minimum_output_raw"}
        if (not isinstance(value,dict) or set(value)!=required or value["provider"]!="JUPITER_V2" or
                type(value["contract_version"]) is not int or value["contract_version"]!=2 or
                event.get("provider")!="JUPITER_V2" or event.get("adapter_contract_version")!=2 or
                event.get("quote",{}).get("source")!="JUPITER_V2_ORDER_QUOTE_OBSERVATION" or
                not event.get("send_started_at") or not event.get("request_id") or
                event.get("order_id")!=value["signature"] or event.get("wallet")!=value["wallet"]):
            raise ValueError()
        def unique(pairs):
            result={}
            for key,item in pairs:
                if key in result:raise ValueError()
                result[key]=item
            return result
        payload=json.loads(event["request_payload_json"],object_pairs_hook=unique)
        if (not isinstance(payload,dict) or set(payload)!=required-{"signature"} or
                any(payload[k]!=value[k] for k in payload) or
                canonical(payload)!=event["request_payload_json"] or
                hashlib.sha256(event["request_payload_json"].encode()).hexdigest()!=event["payload_hash"]):
            raise ValueError()
        # Signature binding is durable before the one SEND, never fabricated
        # after a missing response. Claim and SEND retain their original clocks.
        age(event["sent_at"],event["signature_bound_at"])
        age(event["signature_bound_at"],event["send_started_at"])
        age(event["send_started_at"],now or timestamp())
        signature=Signature.from_string(value["signature"])
        if signature==Signature.default() or str(signature)!=value["signature"]:
            raise ValueError()
        side=event["side"]
        if side not in ("BUY","SELL"):
            raise ValueError()
        expected=(SOL,event["mint"]) if side=="BUY" else (event["mint"],SOL)
        if (value["input_token"],value["output_token"])!=expected:
            raise ValueError()
        for key in ("message_sha256","account_keys_sha256"):
            if len(value[key])!=64 or any(c not in "0123456789abcdef" for c in value[key]):
                raise ValueError()
        if (not 0<raw(value["input_raw"])==raw(event["actual_input_raw"]) or
                not 0<raw(value["minimum_output_raw"])==raw(event["quote"]["minimum_output_raw"])):
            raise ValueError()
        Pubkey.from_string(value["wallet"])
        return value
    except (ValueError,TypeError,KeyError,AttributeError,StateError):
        raise StateError("jupiter_pre_send_binding_missing_or_changed") from None


def verify_receipt(event,transaction,cfg,*,now=None):
    expected=binding(event,now=now)
    try:
        if not isinstance(transaction,dict) or type(transaction.get("version")) is not int or transaction["version"]!=0:
            raise ValueError("jupiter_transaction_version_unknown")
        wire=transaction["transaction"]
        if not isinstance(wire,list) or len(wire)!=2 or wire[1]!="base64" or len(wire[0])>1644:
            raise ValueError("jupiter_transaction_encoding_unknown")
        encoded=base64.b64decode(wire[0],validate=True)
        if not 0<len(encoded)<=1232:
            raise ValueError("jupiter_transaction_size_unknown")
        tx=VersionedTransaction.from_bytes(encoded)
        if (tx.message.header.num_required_signatures!=1 or len(tx.signatures)!=1 or
                str(tx.signatures[0])!=expected["signature"] or
                str(tx.message.account_keys[0])!=expected["wallet"] or
                hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest()!=expected["message_sha256"]):
            raise ValueError("jupiter_signed_message_binding_mismatch")
        tx.verify_and_hash_message()
        meta=transaction["meta"]
        if not isinstance(meta,dict) or meta.get("err","MISSING") is not None:
            raise ValueError("jupiter_not_finalized_success")
        loaded=meta["loadedAddresses"]
        if not isinstance(loaded,dict) or set(loaded)!={"writable","readonly"}:
            raise ValueError("jupiter_loaded_addresses_unknown")
        for name,indexes in (("writable","writable_indexes"),("readonly","readonly_indexes")):
            if (not isinstance(loaded[name],list) or len(loaded[name])!=sum(
                    len(getattr(lookup,indexes)) for lookup in tx.message.address_table_lookups)):
                raise ValueError("jupiter_lookup_count_mismatch")
        keys=[str(k) for k in tx.message.account_keys]+loaded["writable"]+loaded["readonly"]
        if len(keys)!=len(set(keys)) or keys_digest(keys)!=expected["account_keys_sha256"]:
            raise ValueError("jupiter_resolved_keys_binding_mismatch")
        for key in keys:Pubkey.from_string(key)
        pre,post=meta["preBalances"],meta["postBalances"]
        if not isinstance(pre,list) or not isinstance(post,list) or len(pre)!=len(keys) or len(post)!=len(keys):
            raise ValueError("jupiter_native_balances_missing")
        pre,post=[u64(v) for v in pre],[u64(v) for v in post]
        fee=u64(meta["fee"])
        if not 0<fee<=int(cfg["max_order_fee_lamports"]) or sum(post)-sum(pre)!=-fee:
            raise ValueError("jupiter_native_fee_or_conservation_mismatch")
        old=token_states(meta["preTokenBalances"],keys)
        new=token_states(meta["postTokenBalances"],keys)
        wallet=expected["wallet"];mint=event["mint"];delta=0;rent=0;found=False
        for index in set(old)|set(new):
            before,after=old.get(index),new.get(index)
            if before and after and any(before[k]!=after[k] for k in ("mint","owner","program","decimals")):
                raise ValueError("jupiter_token_identity_changed")
            state=after or before
            if state["owner"]!=wallet:continue
            if state["mint"]!=mint or state["program"]!=TOKEN or keys[index]!=associated(wallet,mint,TOKEN) or found:
                raise ValueError("jupiter_wallet_asset_or_recipient_unknown")
            if (not before and pre[index]!=0) or (not after and post[index]!=0):
                raise ValueError("jupiter_token_state_missing")
            found=True
            delta=(after["amount"] if after else 0)-(before["amount"] if before else 0)
            rent+=post[index]-pre[index]
        wsol=associated(wallet,SOL,TOKEN)
        if wsol not in keys or pre[keys.index(wsol)]!=0 or post[keys.index(wsol)]!=0:
            raise ValueError("jupiter_existing_wsol_or_cleanup_unknown")
        amount=raw(expected["input_raw"]);minimum=raw(expected["minimum_output_raw"])
        cash=post[0]-pre[0]
        if event["side"]=="BUY":
            output=delta
            if not found or delta<minimum or cash>=0 or -cash-amount!=fee+rent:
                raise ValueError("jupiter_buy_debit_or_output_mismatch")
        else:
            output=cash+fee+rent
            if not found or delta!=-amount or output<minimum:
                raise ValueError("jupiter_sell_debit_or_output_mismatch")
        slot=u64(transaction["slot"]);block=transaction["blockTime"]
        if slot==0 or type(block) is not int or block<=0:
            raise ValueError("jupiter_chain_time_unknown")
        block_time=datetime.fromtimestamp(block,timezone.utc).isoformat()
        age(block_time,now or timestamp())
        sent=datetime.fromisoformat(event["sent_at"].replace("Z","+00:00"))
        if sent.tzinfo is None or (sent-datetime.fromtimestamp(block,timezone.utc)).total_seconds()>cfg["request_timeout_seconds"]:
            raise ValueError("jupiter_fill_predates_order")
        send=datetime.fromisoformat(event["send_started_at"].replace("Z","+00:00"))
        if send.tzinfo is None or (send-datetime.fromtimestamp(block,timezone.utc)).total_seconds()>MAX_CHAIN_SEND_SKEW_SECONDS:
            raise ValueError("jupiter_fill_predates_send")
        return {"provider":"JUPITER_V2","contract_version":2,"signature":expected["signature"],
            "message_sha256":expected["message_sha256"],"account_keys_sha256":expected["account_keys_sha256"],
            "event_id":event["event_id"],"order_id":event["order_id"],"wallet":wallet,"mint":mint,"side":event["side"],
            "slot":slot,"block_time":block_time,"token_delta_raw":str(delta),"wallet_delta_lamports":str(cash),
            "fees_lamports":str(fee),"native_pre_lamports":str(pre[0]),"native_post_lamports":str(post[0]),
            "swap_input_raw":str(amount),"swap_output_raw":str(output),"order_terminal":True,
            "commitment":"finalized","source":SOURCE}
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,OverflowError,TransactionError):
        raise StateError("jupiter_finalized_receipt_unverified") from None


class JupiterChainEvidence:
    def __init__(self,cfg,*,rpc=None,clock=timestamp):
        self.cfg,self.rpc,self.clock=cfg,rpc or SolanaEvidence(cfg),clock

    def observe(self,event):
        expected=binding(event,now=self.clock())
        with read_budget(self.rpc,self.cfg["reconciliation_max_age_seconds"]):
            tx=self.rpc.transport("getTransaction",[expected["signature"],{
                "encoding":"base64","commitment":"finalized","maxSupportedTransactionVersion":0}])
            if tx is None:
                return {"state":"UNRESOLVED","order_id":event["order_id"],"provider":"JUPITER_V2"}
            if isinstance(tx,dict) and isinstance(tx.get('meta'),dict) and tx['meta'].get('err') is not None:
                return {'state':'FAILED','proof':verify_failed_receipt(event,tx,self.cfg,now=self.clock()),
                        'order_id':event['order_id'],'provider':'JUPITER_V2'}
            proof=verify_receipt(event,tx,self.cfg,now=self.clock())
            if event.get('claim_id'):
                proof.update(claim_id=event['claim_id'],request_id=event['request_id'],
                             route_identity=event['jupiter_prepared']['route_identity'],
                             evidence_domain=event['evidence_domain'],receipt_identity=expected['signature'])
            return {"state":"FILLED","proof":proof,"order_id":event["order_id"],"provider":"JUPITER_V2"}


def verify_failed_receipt(event,transaction,cfg,*,now=None):
    """Failed atomic transaction: exact signed message, fee-only cash effect.

    It remains an aborted execution obligation; it never creates a fill,
    closes a validation trade, or authorizes a retry.
    """
    expected=binding(event,now=now)
    try:
        if type(transaction.get('version')) is not int or transaction['version']!=0:
            raise ValueError()
        wire=transaction['transaction']
        if type(wire) is not list or len(wire)!=2 or wire[1]!='base64':raise ValueError()
        encoded=base64.b64decode(wire[0],validate=True)
        if not 0<len(encoded)<=1232:raise ValueError()
        tx=VersionedTransaction.from_bytes(encoded); tx.verify_and_hash_message()
        if (len(tx.signatures)!=1 or tx.message.header.num_required_signatures!=1
                or str(tx.signatures[0])!=expected['signature'] or str(tx.message.account_keys[0])!=expected['wallet']
                or hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest()!=expected['message_sha256']):raise ValueError()
        meta=transaction['meta'];loaded=meta['loadedAddresses']
        if meta.get('err') is None or set(loaded)!={'writable','readonly'}:raise ValueError()
        for label,index in (('writable','writable_indexes'),('readonly','readonly_indexes')):
            if len(loaded[label])!=sum(len(getattr(t,index)) for t in tx.message.address_table_lookups):raise ValueError()
        keys=[str(k) for k in tx.message.account_keys]+loaded['writable']+loaded['readonly']
        if len(set(keys))!=len(keys) or keys_digest(keys)!=expected['account_keys_sha256']:raise ValueError()
        pre,post=meta['preBalances'],meta['postBalances'];fee=u64(meta['fee'])
        if (len(pre)!=len(keys) or len(post)!=len(keys) or not 0<fee<=int(cfg['max_order_fee_lamports'])
                or u64(pre[0])-u64(post[0])!=fee or pre[1:]!=post[1:]
                or any(type(v) is not int or v<0 or v>=2**64 for v in pre+post)
                or token_states(meta['preTokenBalances'],keys)!=token_states(meta['postTokenBalances'],keys)):
            raise ValueError()
        slot=u64(transaction['slot']); block=u64(transaction['blockTime'])
        block_at=datetime.fromtimestamp(block,timezone.utc).isoformat()
        age(block_at,now or timestamp())
        if slot==0 or (datetime.fromisoformat(event['send_started_at'])-datetime.fromtimestamp(block,timezone.utc)).total_seconds()>2:
            raise ValueError()
        return {'signature':expected['signature'],'wallet':event['wallet'],'mint':event['mint'],'side':event['side'],
                'slot':slot,'block_time':block_at,'token_delta_raw':'0','wallet_delta_lamports':str(-fee),
                'fees_lamports':str(fee),'event_id':event['event_id'],'order_id':event['order_id'],
                'swap_input_raw':'0','swap_output_raw':'0','source':'JUPITER_FINALIZED_FAILED_EXACT_MESSAGE',
                'commitment':'finalized','provider':'JUPITER_V2','contract_version':2,
                'evidence_domain':event.get('evidence_domain','FINALIZED_CHAIN'),
                'message_sha256':expected['message_sha256'],'account_keys_sha256':expected['account_keys_sha256']}
    except (KeyError,ValueError,TypeError,TransactionError,OverflowError):
        raise StateError('jupiter_failed_receipt_mismatch') from None
