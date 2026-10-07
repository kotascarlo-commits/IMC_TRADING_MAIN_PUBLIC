"""Immutable order build and single-dispatch contract.

The gateway attaches the existing guarded SDK host. This module performs no
CLI swap or signing; all sends require the original fresh Risk permit.
"""
import hashlib
import json
import re
from dataclasses import dataclass

from execution_state import StateError, canonical, decimal, raw

SOL = 'So11111111111111111111111111111111111111112'


@dataclass(frozen=True)
class TransportReply:
    http_status: int
    body: bytes


def sdk_ack_reply(permit, response):
    """Accept only the existing SDK boundary's ACK for this exact permit."""
    fields = {'state', 'order_id', 'eventId', 'requestId', 'tradeId', 'payloadHash'}
    if (type(response) is not dict or set(response) != fields
            or response['state'] != 'ACKNOWLEDGED'
            or response['eventId'] != permit.event_id
            or response['requestId'] != permit.request_id
            or response['tradeId'] != permit.trade_id
            or response['payloadHash'] != permit.payload_hash
            or type(response['order_id']) is not str
            or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', response['order_id']) is None):
        raise StateError('sdk_ack_identity_or_response_unknown')
    # API status/amount metadata cannot become a fill through this projection.
    return TransportReply(200, canonical({'order_id': response['order_id']}).encode())


def build_swap_payload(event, quote, cfg):
    if event.get('state') != 'READY' or event.get('side') not in {'BUY', 'SELL'}:
        raise StateError('build_requires_ready_intent')
    expected_input = SOL if event['side'] == 'BUY' else event['mint']
    expected_output = event['mint'] if event['side'] == 'BUY' else SOL
    if (quote.get('source') != 'GMGN_ORDER_QUOTE' or quote.get('event_id') != event['event_id']
            or quote.get('input_token') != expected_input or quote.get('output_token') != expected_output
            or not isinstance(quote.get('wallet'), str) or not quote['wallet']):
        raise StateError('build_quote_identity_mismatch')
    amount, minimum = raw(quote.get('input_raw')), raw(quote.get('minimum_output_raw'))
    slippage = decimal(quote.get('slippage_bps'))
    if not amount or not minimum or slippage > cfg['max_slippage_bps']:
        raise StateError('build_amount_or_slippage_invalid')
    if event['side'] == 'SELL' and (amount != raw(event.get('amount_raw')) or quote['wallet'] != event.get('wallet')):
        raise StateError('build_sell_quantity_or_wallet_mismatch')
    percent = slippage / 100
    # The SDK signs JSON.stringify(payload). Integral values must have the
    # same bytes in the durable Python JSON and the signed JavaScript JSON.
    percent = int(percent) if percent == percent.to_integral_value() else float(percent)
    payload = {'chain': 'sol', 'from_address': quote['wallet'], 'input_token': expected_input,
               'output_token': expected_output, 'input_amount': str(amount), 'min_output_amount': str(minimum),
               'slippage': percent, 'auto_slippage': False, 'is_anti_mev': True,
               'priority_fee': '0.00001', 'tip_fee': '0.00001'}
    if raw(cfg['max_order_fee_lamports']) < 20000:
        raise StateError('build_fee_budget_insufficient')
    return payload


def unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise StateError('duplicate_response_field')
        value[key] = item
    return value


class SingleSendAdapter:
    def __init__(self, risk, probes, *, transport=None, contract_transport=None, jupiter_builder=None):
        if transport is not None and contract_transport is not None:
            raise StateError('ambiguous_execution_transport')
        self.risk, self.store, self.probes = risk, risk.store, probes
        self._contract_transport = contract_transport is not None
        self.transport = contract_transport if self._contract_transport else transport
        self.jupiter_builder = jupiter_builder

    def assess(self, event, quote, lease=None):
        probes = self.probes(event['side'])
        capabilities = dict(probes['capabilities'])
        # Presence is necessary, never sufficient: independent capability
        # checks still have to pass, including actual runtime component health.
        for name in ('buy_transport', 'sell_transport'):
            capabilities[name] = capabilities.get(name) is True and self.transport is not None
        return self.risk.assess(heartbeats=probes['heartbeats'], capabilities=capabilities,
                                side=event['side'], event=event, quote=quote, lease=lease)

    def unknown(self, event, reason):
        self.store.feedback({'feedback_id': 'adapter:' + event['request_id'] + ':unknown',
            'event_id': event['event_id'], 'trade_id': event['trade_id'], 'mint': event['mint'],
            'side': event['side'], 'status': 'UNKNOWN_EXECUTION'})
        with self.store.transaction() as c:
            self.store.audit(c, 'adapter_dispatch_uncertain', {'event_id': event['event_id'],
                             'request_id': event['request_id'], 'reason': reason}, event['trade_id'])
        return {'state': 'UNKNOWN', 'event_id': event['event_id']}

    def execute(self, event, quote):
        if quote.get('source') == 'JUPITER_V2_ORDER_QUOTE_OBSERVATION':
            return self.execute_jupiter(event, quote)
        payload = build_swap_payload(event, quote, self.store.cfg)
        digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
        decision = self.assess(event, quote)
        if not decision['authorized']:
            return {'state': 'NO_BROADCAST' if decision['ready'] else 'BLOCKED',
                    'event_id': event['event_id'], 'payload_hash': digest, 'risk': decision}
        try:
            claimed, lease = self.risk.reserve(event['event_id'], quote, payload)
        except StateError:
            return {'state': 'BLOCKED', 'event_id': event['event_id'], 'reason': 'CLAIM_BLOCKED'}
        try:
            decision = self.assess(claimed, quote, lease)
            if not decision['authorized']:
                self.risk.revoke(lease)
                return self.unknown(claimed, 'POST_CLAIM_PREFLIGHT_BLOCKED_NO_IO')
            permit = self.risk.authorize_send(lease, decision)
            # Both handoffs consume the same permit at the same guarded SEND
            # boundary. Contract transports retain durable identity and the
            # original SEND/response deadlines. Never fall back or redispatch.
            dispatch = self.risk.dispatch_contract if self._contract_transport else self.risk.dispatch_payload
            contract = dispatch(permit)
            if self._contract_transport and hasattr(self.transport, 'send_with_guard'):
                response = self.transport.send_with_guard(contract,
                    send_guard=lambda: self.risk.send_window(permit))
            else:
                response = self.transport(contract)
            if self._contract_transport:
                response = sdk_ack_reply(permit, response)
            if (type(response) is not TransportReply or type(response.http_status) is not int
                    or response.http_status != 200 or type(response.body) is not bytes
                    or not response.body or len(response.body) > self.store.cfg['max_http_body_bytes']):
                return self.unknown(claimed, 'TRANSPORT_RESPONSE_UNKNOWN')
            def reject_constant(value):
                raise StateError('nonfinite_response')
            body = json.loads(response.body, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
            order_id = body.get('order_id') if isinstance(body, dict) else None
            if not isinstance(order_id, str) or not order_id or len(order_id) > 256:
                return self.unknown(claimed, 'ORDER_BINDING_UNKNOWN')
            if not self.store.bind_order(claimed['event_id'], order_id):
                return self.unknown(claimed, 'ORDER_BINDING_CONFLICT')
            self.store.feedback({'feedback_id': 'adapter:' + lease.request_id + ':ack',
                'event_id': claimed['event_id'], 'trade_id': claimed['trade_id'], 'mint': claimed['mint'],
                'side': claimed['side'], 'status': 'ACK', 'order_id': order_id})
            return {'state': 'ACKNOWLEDGED', 'event_id': claimed['event_id'], 'request_id': lease.request_id}
        except Exception as error:
            self.risk.revoke(lease)
            return self.unknown(claimed, type(error).__name__)

    def execute_jupiter(self, event, quote):
        from execution_jupiter import bind_signature, IsolatedJupiterTransport, JupiterRPCTransport
        with self.store.transaction() as c:
            current = self.store.get(c, 'events', event['event_id'])
            if not current or current['state'] != 'READY' or current.get('sent_at'):
                return {'state': 'BLOCKED', 'reason': 'JUPITER_EVENT_ALREADY_CONSUMED_OR_UNKNOWN'}
        if (self.jupiter_builder is None or not self._contract_transport
                or type(self.transport) not in (IsolatedJupiterTransport, JupiterRPCTransport)):
            return {'state': 'BLOCKED', 'reason': 'JUPITER_COMPONENTS_UNAVAILABLE'}
        decision = self.assess(event, quote)
        if not decision['authorized']:
            if not decision['ready']:
                # A competing instance may have claimed while assessment was
                # running. Its valid obligation must survive our stale result.
                with self.store.transaction() as c:
                    current=self.store.get(c,'events',event['event_id'])
                    if current and current['state']=='READY' and not current.get('sent_at'):
                        self.store.halt(c,'validation_risk_failure')
            return {'state': 'NO_BROADCAST' if decision['ready'] else 'BLOCKED', 'risk': decision}
        try:
            claimed, lease = self.risk.reserve(event['event_id'], quote, None, provider='JUPITER_V2')
        except StateError:
            return {'state': 'BLOCKED', 'reason': 'CLAIM_BLOCKED'}
        try:
            prepared = self.jupiter_builder.build(claimed, quote)
            claimed, lease = self.risk.bind_jupiter_build(lease, prepared)
            decision = self.assess(claimed, quote, lease)
            if not decision['authorized']:
                raise StateError('JUPITER_POST_BUILD_RISK_FAILURE')
            signed = self.transport.sign(claimed)
            claimed = bind_signature(self.store, claimed['event_id'], signed)
            decision = self.assess(claimed, quote, lease)
            if not decision['authorized']:
                raise StateError('JUPITER_PRE_SEND_RISK_FAILURE')
            permit = self.risk.authorize_send(lease, decision)
            contract = self.risk.dispatch_contract(permit)
            response = self.transport.send_with_guard(contract, send_guard=lambda: self.risk.send_window(permit))
            reply = sdk_ack_reply(permit, response)
            body = json.loads(reply.body, object_pairs_hook=unique_pairs)
            if body['order_id'] != claimed['order_id']:
                raise StateError('JUPITER_ACK_SIGNATURE_MISMATCH')
            self.store.feedback({'feedback_id': 'adapter:' + lease.request_id + ':ack',
                **{key: claimed[key] for key in ('event_id', 'trade_id', 'mint', 'side', 'order_id')}, 'status': 'ACK'})
            return {'state': 'ACKNOWLEDGED', 'event_id': claimed['event_id'], 'request_id': lease.request_id}
        except Exception as error:
            self.risk.revoke(lease)
            return self.unknown(claimed, type(error).__name__)
