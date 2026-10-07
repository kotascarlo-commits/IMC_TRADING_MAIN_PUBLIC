"""Read-only finalized Solana receipts. Never trusts amounts from fill JSON."""
from datetime import datetime, timezone
from datetime import timedelta
import json
import os
import subprocess
import sys
from pathlib import Path
from decimal import Decimal
import time
from contextlib import contextmanager

from execution_state import StateError, raw, decimal, age, timestamp

SOL = "So11111111111111111111111111111111111111112"
GMGN_SHIM = Path(__file__).resolve().parents[3] / 'TOOLS/GMGN_READ_SHIM/gmgn-cli.cmd'


def rpc_u64(value):
    if type(value) is not int or not 0 <= value < 2**64:
        raise StateError('rpc_u64_required')
    return value


@contextmanager
def read_budget(reader, seconds, *, deadline=None):
    previous = getattr(reader, 'read_deadline', None)
    deadline = min(time.monotonic() + seconds, deadline) if deadline is not None else time.monotonic() + seconds
    reader.read_deadline = min(previous, deadline) if previous is not None else deadline
    try:
        yield
    finally:
        if previous is None:
            del reader.read_deadline
        else:
            reader.read_deadline = previous


class RPCError(StateError):
    def __init__(self, code=None, *, context_slot=None, minimum_floor=0):
        self.code = code if type(code) is int and -(2**31) <= code < 2**31 else None
        self.context_slot = context_slot if type(context_slot) is int and 0 <= context_slot < 2**64 else None
        self.minimum_floor = minimum_floor
        super().__init__('rpc_error' + (':' + str(self.code) if self.code is not None else ''))


class RPCTransportError(StateError):
    def __init__(self, kind, status):
        self.kind, self.http_status = kind, status
        super().__init__('rpc_worker_response_invalid:' + kind)


class RPCSnapshotDriftError(StateError):
    """Only fully validated, non-regressing read rounds exhausted their budget."""


class GMGNOrderEvidence:
    """Read-only query of a previously bound order; never invokes swap.

    Both locally documented success formats require a full matching report.
    Unknown/missing fields fail closed instead of inventing an API mapping.
    """
    def __init__(self, cfg, query=None):
        self.cfg = cfg
        self.query = query or self.request

    def request(self, order_id):
        result = subprocess.run([str(GMGN_SHIM), 'order', 'get', '--chain', 'sol',
                                 '--order-id', order_id, '--raw'], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=self.cfg['http_timeout_seconds'])
        if result.returncode:
            raise StateError('gmgn_order_query_failed')
        return json.loads(result.stdout, parse_float=Decimal)

    @staticmethod
    def normalize(event, order):
        """Map the documented SDK order response, without inferring a fill.

        State 30 is a conservative provider acceptance rule, never chain
        finality. RPC and the ledger still prove quantities and reconciliation.
        Parallel response fields must agree rather than select a fallback.
        """
        if not isinstance(order, dict) or order.get('order_id') != event.get('order_id'):
            raise StateError('order_identity_mismatch')
        if ('chain' in order and order['chain'] != 'sol'
                or 'from_address' in order and order['from_address'] != event.get('wallet')):
            raise StateError('order_wallet_or_chain_mismatch')
        if 'confirmation' not in order:
            return order
        confirmation = order['confirmation']
        if not isinstance(confirmation, dict) or confirmation.get('state') not in {
                'processed', 'confirmed', 'failed', 'expired'}:
            raise StateError('unknown_order_confirmation')
        status = confirmation['state']
        if ('status' in order and order['status'] != status
                and not (status == 'confirmed' and order['status'] == 'successful')):
            raise StateError('contradictory_order_status')
        normalized = dict(order, status=status)
        if status == 'confirmed':
            report = {'input_token': order.get('input_token'), 'output_token': order.get('output_token'),
                      'input_amount': order.get('filled_input_amount'),
                      'output_amount': order.get('filled_output_amount')}
            for field in ('input_amount', 'output_amount'):
                if raw(report[field]) <= 0:
                    raise StateError('gmgn_execution_amount_missing')
            if 'report' in order:
                parallel = order['report']
                if not isinstance(parallel, dict) or any(parallel.get(k) != v for k, v in report.items()):
                    raise StateError('contradictory_order_report')
            normalized['report'] = report
        return normalized

    def verify(self, event, signature, *, order=None):
        if not event.get('order_id'):
            raise StateError('missing_trusted_order_binding')
        order = self.normalize(event, self.query(event['order_id']) if order is None else order)
        success = (order.get('status') in {'confirmed', 'successful'}
                   and type(order.get('state')) is int and order['state'] == 30)
        if not success or order.get('order_id') != event['order_id'] or order.get('hash') != signature:
            raise StateError('order_signature_not_confirmed')
        if (order.get('error_code') not in (None, '', 0, '0')
                or order.get('error_status') not in (None, '')):
            raise StateError('order_error_not_resolved')
        if (not isinstance(signature, str) or not 64 <= len(signature) <= 100
                or not signature.isascii() or not signature.isalnum()):
            raise StateError('invalid_order_signature')
        report = order.get('report')
        if not isinstance(report, dict):
            raise StateError('gmgn_execution_report_missing')
        input_token = SOL if event['side'] == 'BUY' else event['mint']
        output_token = event['mint'] if event['side'] == 'BUY' else SOL
        if report.get('input_token') != input_token or report.get('output_token') != output_token:
            raise StateError('gmgn_order_token_pair_mismatch')
        if raw(report.get('input_amount')) <= 0 or raw(report.get('output_amount')) <= 0:
            raise StateError('gmgn_execution_amount_missing')
        return {'event_id': event['event_id'], 'order_id': event['order_id'],
                'swap_input_raw': str(raw(report.get('input_amount'))),
                'swap_output_raw': str(raw(report.get('output_amount'))), 'order_terminal': True}

    def observe(self, event, order):
        """A valid nonfinal observation never proves a fill or an absent send."""
        if not isinstance(order, dict) or not event.get('order_id') or order.get('order_id') != event['order_id']:
            raise StateError('order_identity_mismatch')
        order = self.normalize(event, order)
        status = order.get('status')
        if status not in {'pending', 'processed', 'failed', 'expired', 'confirmed', 'successful'}:
            raise StateError('unknown_order_status')
        state = order.get('state')
        if state is not None and type(state) is not int:
            raise StateError('invalid_order_state')
        if state == 30 and status not in {'confirmed', 'successful'}:
            raise StateError('contradictory_order_status')
        signature = order.get('hash')
        if signature is not None and (not isinstance(signature, str) or not 64 <= len(signature) <= 100
                                      or not signature.isascii() or not signature.isalnum()):
            raise StateError('invalid_order_signature')
        report = order.get('report')
        if report is not None:
            if not isinstance(report, dict):
                raise StateError('invalid_order_report')
            for field in ('input_token', 'output_token'):
                if not isinstance(report.get(field), str) or not report[field] or len(report[field]) > 128:
                    raise StateError('invalid_order_report')
            for field in ('input_amount', 'output_amount'):
                raw(report.get(field))
        if status in {'confirmed', 'successful'}:
            if not signature:
                raise StateError('order_signature_not_confirmed')
            return {'state': 'FILLED', **self.verify(event, signature, order=order)}
        return {'state': 'UNRESOLVED', 'order_id': event['order_id'], 'status': status}


class SolanaEvidence:
    def __init__(self, cfg, transport=None):
        self.cfg = cfg
        self.native_transport = transport is None
        self.transport = transport or self.request

    def request(self, method, params, *, retry_context=True):
        if method not in {'getBalance', 'getTokenAccountsByOwner', 'getTransaction'}:
            raise StateError('read_only_rpc_method_required')
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        deadline = getattr(self, 'read_deadline', time.monotonic() + self.cfg['rpc_timeout_seconds'])
        for attempt in range(2 if retry_context else 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StateError('rpc_read_deadline')
            body = self._request_once(payload, deadline)
            if time.monotonic() >= deadline:
                raise StateError('rpc_read_deadline')
            if len(body) > 4 * 1024 * 1024:
                raise StateError('rpc_response_too_large')
            data = json.loads(body)
            if time.monotonic() >= deadline:
                raise StateError('rpc_read_deadline')
            if not isinstance(data, dict) or data.get('jsonrpc') != '2.0' or type(data.get('id')) is not int or data['id'] != 1:
                raise StateError('rpc_envelope_invalid')
            error = data.get('error')
            if 'result' in data and 'error' in data:
                raise StateError('rpc_envelope_invalid')
            if error or 'result' not in data:
                code = error.get('code') if isinstance(error, dict) else None
                if retry_context and type(code) is int and code == -32016 and attempt == 0 and deadline - time.monotonic() > .1:
                    time.sleep(.1)
                    continue
                raise RPCError(code, context_slot=error.get('context_slot') if isinstance(error, dict) else None)
            if time.monotonic() >= deadline:
                raise StateError('rpc_read_deadline')
            return data['result']

    def _request_once(self, payload, deadline):
        """Cancel network work in our own child on budget expiry.

        OS process start/termination can add scheduling latency. No result
        after the shared deadline is accepted and no read survives cancellation.
        """
        message = json.dumps({'url': self.cfg['rpc_url'], 'payload': payload,
                              'timeout': self.cfg['rpc_timeout_seconds']}).encode()
        if len(message) > 65536:
            raise StateError('rpc_request_too_large')
        worker = Path(__file__).with_name('rpc_read_worker.py').resolve()
        # This stdlib-only child uses the actual base interpreter, avoiding the
        # Windows venv redirector and its separately created network child.
        interpreter = Path(sys._base_executable).resolve(strict=True)
        if not interpreter.is_absolute() or (sys.platform == 'win32'
                and interpreter.parent != Path(sys.base_prefix).resolve()):
            raise StateError('rpc_base_interpreter_invalid')
        process = subprocess.Popen([str(interpreter), str(worker)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        try:
            limit = (self.cfg['reconciliation_max_age_seconds'] if payload.get('operation') in {'wallet_snapshot','wallet_snapshot_batch'}
                     else self.cfg['rpc_timeout_seconds'])
            remaining = min(limit, deadline - time.monotonic())
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, 0)
            body, _ = process.communicate(message, timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()  # exact owned Popen handle; no PID enumeration
            process.communicate()
            raise StateError('rpc_read_deadline') from None
        if process.returncode:
            if len(body) <= 4096:
                try:
                    failure = json.loads(body)
                    details = failure['worker_failure']
                    if (set(failure) == {'worker_failure'} and isinstance(details, dict)
                            and set(details) == {'kind', 'http_status'}
                            and details['kind'] in {'HTTP_STATUS', 'TIMEOUT', 'RESPONSE_INVALID', 'NETWORK'}
                            and (details['http_status'] is None or
                                 (type(details['http_status']) is int and 100 <= details['http_status'] <= 599))
                            and ((details['kind'] == 'HTTP_STATUS') == (details['http_status'] is not None))):
                        raise RPCTransportError(details['kind'], details['http_status'])
                except (KeyError, TypeError, json.JSONDecodeError):
                    pass
            raise StateError('rpc_worker_response_invalid')
        return body

    def fill(self, event, signature):
        if not isinstance(signature, str) or not 64 <= len(signature) <= 100 or not signature.isalnum():
            raise StateError("invalid_signature")
        tx = self.transport("getTransaction", [signature, {
            "encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0}])
        if not tx:
            raise StateError("transaction_not_finalized_or_missing")
        meta = tx.get("meta")
        if not meta or meta.get("err") is not None:
            raise StateError("transaction_failed_or_missing_metadata")
        if signature not in tx.get("transaction", {}).get("signatures", []):
            raise StateError("rpc_signature_mismatch")
        keys = tx["transaction"]["message"]["accountKeys"]
        addresses = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
        wallet = event["wallet"]
        if wallet not in addresses:
            raise StateError("wallet_missing_from_transaction")
        index = addresses.index(wallet)
        if not isinstance(keys[index], dict) or not keys[index].get("signer"):
            raise StateError("wallet_did_not_sign_transaction")
        # Require the signer/payer balance; all native debits, tips and rent are
        # included in the net wallet delta, so fees must not be subtracted again.
        if index != 0:
            raise StateError("unexpected_fee_payer")
        mint = event["mint"]

        def token_total(balances):
            result = 0
            for balance in balances:
                if balance.get("mint") == mint:
                    if not balance.get("owner"):
                        raise StateError("token_owner_missing")
                    if balance["owner"] == wallet:
                        result += raw(balance["uiTokenAmount"]["amount"])
            return result

        if "preTokenBalances" not in meta or "postTokenBalances" not in meta:
            raise StateError("token_balances_missing")
        delta = token_total(meta["postTokenBalances"]) - token_total(meta["preTokenBalances"])
        if (event["side"] == "BUY" and delta <= 0) or (event["side"] == "SELL" and delta >= 0):
            raise StateError("token_delta_direction_mismatch")
        pre = raw(meta["preBalances"][index])
        post = raw(meta["postBalances"][index])
        if not isinstance(tx.get("blockTime"), int) or not isinstance(tx.get("slot"), int):
            raise StateError("missing_chain_time")
        return {"signature": signature, "wallet": wallet, "mint": mint, "side": event["side"],
                "slot": tx["slot"], "block_time": datetime.fromtimestamp(tx["blockTime"], timezone.utc).isoformat(),
                "token_delta_raw": str(delta), "wallet_delta_lamports": str(post - pre),
                "fees_lamports": str(raw(meta["fee"])), "native_pre_lamports": str(pre),
                "native_post_lamports": str(post), "source": self.cfg["rpc_url"], "commitment": "finalized"}

    def wallet_snapshot(self, wallet, minimum_slot=0):
        # Reserve application time for every RPC group, including lag/drift
        # recovery. Small fixture configurations retain the same proportion.
        reserve = min(3, self.cfg['reconciliation_max_age_seconds'] / 5)
        previous = getattr(self, 'read_deadline', None)
        rpc_deadline = (min(previous, time.monotonic() + self.cfg['reconciliation_max_age_seconds'])
                        if previous is not None else time.monotonic() + self.cfg['reconciliation_max_age_seconds']) - reserve
        with read_budget(self, self.cfg['reconciliation_max_age_seconds'], deadline=rpc_deadline):
            self._balance_retry_available = True
            minimum = raw(minimum_slot)
            for attempt in range(3):
                if time.monotonic() >= self.read_deadline:
                    raise StateError('rpc_read_deadline')
                try:
                    positions, decimals, balance, slots = self._wallet_snapshot_round(wallet, minimum)
                except RPCError as error:
                    if error.code != -32016 or attempt == 2 or self.read_deadline - time.monotonic() <= .1:
                        raise
                    minimum = max(minimum, raw(error.minimum_floor))
                    time.sleep(.1)
                    continue
                if time.monotonic() >= self.read_deadline:
                    raise StateError('rpc_read_deadline')
                if any(slot < minimum for slot in slots):
                    raise StateError('inconsistent_chain_snapshot_slots')
                if len(set(slots)) == 1:
                    return positions, decimals, balance, slots[0]
                minimum = max(slots)
            raise RPCSnapshotDriftError('inconsistent_chain_snapshot_slots')

    def _wallet_snapshot_round(self, wallet, minimum):
        positions, decimals, slots, accounts = {}, {}, [], set()
        programs = ('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
                    'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb')
        group = self._snapshot_request(wallet, minimum) if self.native_transport else None
        for index, program in enumerate(programs):
            result = group[index] if group else self.transport('getTokenAccountsByOwner', [wallet, {'programId': program},
                            {'encoding': 'jsonParsed', 'commitment': 'finalized', 'minContextSlot': minimum}])
            inventory, precision, slot, addresses = self._token_inventory(result, wallet, program)
            slots.append(slot)
            if accounts & addresses:
                raise StateError('rpc_duplicate_or_unknown_account')
            accounts.update(addresses)
            for mint, quantity in inventory.items():
                if mint in decimals and decimals[mint] != precision[mint]:
                    raise StateError('token_precision_disagreement')
                decimals[mint] = precision[mint]
                positions[mint] = str(raw(positions.get(mint, '0')) + raw(quantity))
        native = group[2] if group else self.transport('getBalance', [wallet, {'commitment': 'finalized', 'minContextSlot': max(slots)}])
        slots.append(rpc_u64(native['context']['slot']))
        # Validate the balance before deciding whether this entire read round
        # may be repeated for slot divergence. Invalid data is never retried.
        balance = str(rpc_u64(native['value']))
        return {k:v for k,v in positions.items() if raw(v)}, decimals, balance, slots

    @staticmethod
    def _token_inventory(result, wallet, program):
        positions, decimals, accounts = {}, {}, set()
        slot = rpc_u64(result['context']['slot'])
        if not isinstance(result.get('value'), list):
            raise StateError('rpc_token_inventory_list_required')
        for account in result['value']:
            address = account['pubkey']
            if not isinstance(address, str) or not address or address in accounts:
                raise StateError('rpc_duplicate_or_unknown_account')
            accounts.add(address)
            if account['account'].get('owner') != program or account['account'].get('executable') is not False:
                raise StateError('rpc_account_program_mismatch')
            parsed = account['account']['data']['parsed']
            if parsed.get('type') != 'account':
                raise StateError('rpc_account_type_unknown')
            info = parsed['info']
            if info['owner'] != wallet:
                raise StateError('rpc_account_owner_mismatch')
            if info.get('extensions'):
                raise StateError('unsupported_token_extensions')
            mint, amount = info['mint'], info['tokenAmount']
            if not isinstance(mint, str) or not mint:
                raise StateError('rpc_account_mint_unknown')
            precision = raw(amount['decimals'])
            if precision > 18 or (mint in decimals and decimals[mint] != precision):
                raise StateError('token_precision_disagreement')
            decimals[mint] = precision
            positions[mint] = str(raw(positions.get(mint, '0')) + raw(amount['amount']))
        return positions, decimals, slot, accounts

    def _snapshot_request(self, wallet, minimum):
        operation=getattr(self,'snapshot_operation','wallet_snapshot')
        payload = {'operation': operation, 'wallet': wallet, 'minimum_slot': minimum}
        if operation=='wallet_snapshot_batch':
            payload['balance_retry_allowed']=getattr(self,'_balance_retry_available',False)
        programs = ('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
                    'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb')
        body = self._request_once(payload, self.read_deadline)
        if len(body) > 4 * 1024 * 1024:
            raise StateError('rpc_response_too_large')
        data = json.loads(body)
        if time.monotonic() >= self.read_deadline:
            raise StateError('rpc_read_deadline')
        if operation=='wallet_snapshot_batch':
            if (not isinstance(data,dict) or set(data)!={'snapshot_responses','balance_retry_used'}
                    or type(data['balance_retry_used']) is not bool
                    or (data['balance_retry_used'] and not payload['balance_retry_allowed'])):
                raise StateError('rpc_snapshot_retry_budget_invalid')
            if data['balance_retry_used']:self._balance_retry_available=False
        replies = data.get('snapshot_responses') if isinstance(data, dict) else None
        if not isinstance(replies, list) or len(replies) != 3:
            raise StateError('rpc_snapshot_envelope_invalid')
        errors, valid_slots, accounts, precisions = [], [minimum], set(), {}
        highest_validated_slot = minimum
        for index, reply in enumerate(replies):
            if (not isinstance(reply, dict) or reply.get('jsonrpc') != '2.0'
                    or type(reply.get('id')) is not int or reply['id'] != index + 1
                    or (('result' in reply) == ('error' in reply))):
                raise StateError('rpc_snapshot_envelope_invalid')
            if 'error' in reply:
                error = reply['error']
                if not isinstance(error, dict) or type(error.get('code')) is not int:
                    raise StateError('rpc_snapshot_envelope_invalid')
                if 'context_slot' in error and (type(error['context_slot']) is not int or not 0 <= error['context_slot'] < 2**64):
                    raise StateError('rpc_snapshot_envelope_invalid')
                errors.append(error['code'])
            elif index < 2:
                inventory, precision, slot, addresses = self._token_inventory(reply['result'], wallet, programs[index])
                if slot < minimum:
                    raise StateError('rpc_context_below_minimum')
                if accounts & addresses:
                    raise StateError('rpc_duplicate_or_unknown_account')
                accounts.update(addresses)
                for mint, value in precision.items():
                    if mint in precisions and precisions[mint] != value:
                        raise StateError('token_precision_disagreement')
                    precisions[mint] = value
                valid_slots.append(slot)
                highest_validated_slot = max(highest_validated_slot, slot)
            else:
                if rpc_u64(reply['result']['context']['slot']) < (minimum if operation=='wallet_snapshot_batch' else max(valid_slots)):
                    raise StateError('rpc_context_below_minimum')
                rpc_u64(reply['result']['value'])
                highest_validated_slot = max(highest_validated_slot, rpc_u64(reply['result']['context']['slot']))
        # At most one extra balance READ per whole wallet snapshot. Both token
        # inventories have already passed owner/program/quantity validation.
        if (operation!='wallet_snapshot_batch' and errors == [-32016] and len(valid_slots) == 3 and len(set(valid_slots[1:])) == 1
                and 'error' in replies[2] and getattr(self, '_balance_retry_available', False)
                and self.read_deadline - time.monotonic() > .1):
            self._balance_retry_available = False
            floor = valid_slots[1]
            time.sleep(.1)
            # Leave time for GMGN holdings and application; never renew the
            # whole snapshot deadline or invoke the generic context retry.
            with read_budget(self, min(self.cfg['rpc_timeout_seconds'],
                                       self.read_deadline-time.monotonic())):
                try:
                    native = self.request('getBalance', [wallet, {'commitment': 'finalized',
                                          'minContextSlot': floor}], retry_context=False)
                except RPCError as error:
                    error.minimum_floor = floor
                    raise
            if raw(native['context']['slot']) < floor:
                raise StateError('rpc_context_below_minimum')
            raw(native['value'])
            replies[2] = {'jsonrpc': '2.0', 'id': 3, 'result': native}
            errors = []
        if errors:
            error_replies = [r['error'] for r in replies if 'error' in r]
            selected = next((error for error in error_replies if error['code'] != -32016), error_replies[0])
            raise RPCError(selected['code'], minimum_floor=highest_validated_slot,
                           context_slot=selected.get('context_slot'))
        if time.monotonic() >= self.read_deadline:
            raise StateError('rpc_read_deadline')
        return [reply['result'] for reply in replies]


class ReconciliationReader:
    """Independent read queries, with full holdings pagination and RPC inventory.

    GMGN performs its own API authentication for read queries. No IMC signing,
    swap, control, cancel or broadcast command is available in this adapter.
    """
    def __init__(self, cfg, cli=None, rpc=None, clock=timestamp):
        self.cfg, self.clock = cfg, clock
        self.cli = cli or self.request
        self.rpc = rpc or SolanaEvidence(cfg)

    def request(self, args):
        if args[:2] not in [['portfolio', 'info'], ['portfolio', 'holdings'], ['order', 'get']]:
            raise StateError('read_only_gmgn_command_required')
        remaining = getattr(self, 'read_deadline', time.monotonic() + self.cfg['http_timeout_seconds']) - time.monotonic()
        if remaining <= 0:
            raise StateError('reconciliation_read_deadline')
        environment = dict(os.environ)
        # The host broker receives the remaining read budget, not a new lease.
        environment['IMC_GMGN_READ_DEADLINE_UTC'] = (datetime.now(timezone.utc) + timedelta(seconds=remaining)).isoformat(timespec='microseconds')
        result = subprocess.run([str(GMGN_SHIM), *args, '--raw'], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, env=environment,
                                timeout=min(self.cfg['http_timeout_seconds'], remaining))
        if result.returncode:
            raise StateError('gmgn_read_query_failed')
        return json.loads(result.stdout, parse_float=Decimal)

    def snapshot(self, store, *, deadline=None):
        with read_budget(self, self.cfg['reconciliation_max_age_seconds'], deadline=deadline):
            if isinstance(self.rpc, SolanaEvidence):
                with read_budget(self.rpc, max(0, self.read_deadline - time.monotonic())):
                    return self._snapshot(store)
            return self._snapshot(store)

    def _snapshot(self, store):
        started = self.clock()
        info = self.cli(['portfolio', 'info'])
        wallets = [w for w in info.get('wallets', []) if w.get('chain') == 'sol']
        if len(wallets) != 1 or not wallets[0].get('address'):
            raise StateError('ambiguous_gmgn_wallet_binding')
        wallet = wallets[0]['address']
        if isinstance(self.rpc, SolanaEvidence):
            with store.transaction(deadline=self.read_deadline) as c:
                minimum_slot = max((raw(r['proof']['slot']) for r in store.all(c, 'receipts')), default=0)
            chain, precision, balance, slot = self.rpc.wallet_snapshot(wallet, minimum_slot=minimum_slot)
        else:
            chain, precision, balance, slot = self.rpc.wallet_snapshot(wallet)
        holdings, cursor, visited = {}, None, set()
        for _ in range(self.cfg['max_snapshot_pages']):
            args = ['portfolio', 'holdings', '--chain', 'sol', '--wallet', wallet,
                    '--limit', '50', '--hide-airdrop', 'false', '--hide-abnormal', 'false', '--hide-closed', 'false']
            if cursor:
                args += ['--cursor', cursor]
            page = self.cli(args)
            if not isinstance(page.get('list'), list):
                raise StateError('incomplete_gmgn_holdings_page')
            for row in page['list']:
                mint = row['token']['token_address']
                quantity = decimal(row['balance'])
                if not quantity:
                    continue
                if mint not in precision or mint in holdings:
                    raise StateError('unknown_or_duplicate_gmgn_position')
                amount = quantity * (10 ** precision[mint])
                if amount != amount.to_integral_value():
                    raise StateError('gmgn_quantity_precision_loss')
                holdings[mint] = str(int(amount))
            cursor = page.get('next')
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in visited:
                raise StateError('invalid_gmgn_pagination')
            visited.add(cursor)
        else:
            raise StateError('gmgn_inventory_page_limit')
        status = store.status(expire=False, deadline=self.read_deadline)
        trades = {t['trade_id']: t for t in status['trades']}
        orders, recovered = {}, []
        for event in status['events']:
            if trades[event['trade_id']]['state'] == 'CLOSED' or event['state'] in {'READY', 'REJECTED'}:
                continue
            if not event['order_id']:
                raise StateError('unbound_execution_obligation')
            order = self.cli(['order', 'get', '--chain', 'sol', '--order-id', event['order_id']])
            verified = GMGNOrderEvidence(self.cfg, query=lambda _: order).observe(event, order)
            if verified['state'] != 'FILLED':
                orders[event['event_id']] = verified
                continue
            # Recover a missed/late callback using the known executor order and
            # independent finalized receipt. No unknown order is retried here.
            proof = self.rpc.fill(event, order['hash'])
            proof.update({k: v for k, v in verified.items() if k != 'state'})
            if raw(proof['slot']) > slot:
                raise StateError('wallet_snapshot_precedes_fill')
            data = {'feedback_id': 'reconcile:' + event['event_id'] + ':' + order['hash'],
                    'event_id': event['event_id'], 'trade_id': event['trade_id'], 'mint': event['mint'],
                    'side': event['side'], 'status': 'FILLED', 'order_id': event['order_id'], 'signature': order['hash']}
            if time.monotonic() >= self.read_deadline:
                raise StateError('reconciliation_read_deadline')
            # Observation is pure. The ledger applies recovery only after its
            # wallet guard, atomically with reconciliation under one lock.
            recovered.append({'data': data, 'proof': proof})
            orders[event['event_id']] = {'state': 'FILLED', 'order_id': verified['order_id']}
        observed = self.clock()
        if time.monotonic() >= self.read_deadline or age(started, observed) > self.cfg['reconciliation_max_age_seconds']:
            raise StateError('reconciliation_reads_too_slow')
        return {'wallet': wallet, 'observed_at': started, 'chain_positions': chain,
                'executor_positions': holdings, 'orders': orders, 'balance_lamports': balance,
                'chain_slot': slot, 'source': 'GMGN_READ_QUERIES_AND_FINALIZED_RPC',
                'recovered_fills': recovered}
