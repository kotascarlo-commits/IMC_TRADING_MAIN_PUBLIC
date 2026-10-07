"""Isolated account bank for the production builder/adapter/receipt contracts.

Only fixture transport and RPC reads are replaced. No alternative lifecycle,
claims, risk checks, fill accounting, recovery or close counter exists here.
No real keys, wallets or endpoints are used.
"""
import base64
import copy
import hashlib
import math
import time

from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from execution_state import StateError, raw
from mykonos_meme_scout.jupiter_route import WHIRLPOOL, USDC_AUTHORITIES
from mykonos_meme_scout.jupiter_prepare import SOL, USDC, JUPITER
from mykonos_meme_scout.jupiter_simulation import associated, TOKEN, ATA, message_keys


def ix(program, data, keys):
    return {'programId': program, 'data': base64.b64encode(data).decode(),
            'accounts': [{'pubkey': key, 'isSigner': signer, 'isWritable': writable}
                         for key, signer, writable in keys]}


class IsolatedBank:
    def __init__(self):
        self.key = Keypair(); self.wallet = str(self.key.pubkey())
        self.balance = 1_000_000_000; self.tokens = 0; self.slot = 100
        self.accounts = {}; self.receipts = {}; self.sent = []
        self.pool = str(Pubkey.new_unique()); self.vault_a = str(Pubkey.new_unique()); self.vault_b = str(Pubkey.new_unique())
        self.put(self.wallet, '11111111111111111111111111111111', b'', self.balance)
        for mint, decimals in ((SOL, 9), (USDC, 6)):
            body = bytearray(82); body[44] = decimals; body[45] = 1
            if mint == USDC:
                body[:4] = (1).to_bytes(4, 'little'); body[46:50] = (1).to_bytes(4, 'little')
                body[4:36] = bytes(Pubkey.from_string(USDC_AUTHORITIES[0]))
                body[50:82] = bytes(Pubkey.from_string(USDC_AUTHORITIES[1]))
            self.put(mint, TOKEN, body)
        pool = bytearray(653); pool[:8] = hashlib.sha256(b'account:Whirlpool').digest()[:8]
        pool[41:43] = (64).to_bytes(2, 'little'); pool[45:47] = (100).to_bytes(2, 'little')
        pool[49:65] = (10**18).to_bytes(16, 'little')
        pool[65:81] = int(math.sqrt(.12)*2**64).to_bytes(16, 'little')
        for lo, key in ((101, SOL), (133, self.vault_a), (181, USDC), (213, self.vault_b)):
            pool[lo:lo+32] = bytes(Pubkey.from_string(key))
        self.put(self.pool, WHIRLPOOL, pool)
        self.token_account(self.vault_a, SOL, self.pool, 10**14)
        self.token_account(self.vault_b, USDC, self.pool, 10**12)
        self.token_account(associated(self.wallet, USDC, TOKEN), USDC, self.wallet, 0)
        self.ticks = {}
        for start in (-11264, -5632, 0, 5632, 11264):
            key = str(Pubkey.find_program_address([b'tick_array', bytes(Pubkey.from_string(self.pool)), str(start).encode()],
                                                   Pubkey.from_string(WHIRLPOOL))[0])
            body = bytearray(9988); body[:8] = hashlib.sha256(b'account:TickArray').digest()[:8]
            body[8:12] = start.to_bytes(4, 'little', signed=True); body[-32:] = bytes(Pubkey.from_string(self.pool))
            self.put(key, WHIRLPOOL, body); self.ticks[start] = key

    def put(self, key, owner, body, lamports=2039280):
        self.accounts[key] = {'owner': owner, 'executable': False, 'lamports': lamports,
                              'data': [base64.b64encode(bytes(body)).decode(), 'base64']}

    def token_account(self, key, mint, owner, amount):
        body = bytearray(165); body[:32] = bytes(Pubkey.from_string(mint)); body[32:64] = bytes(Pubkey.from_string(owner))
        body[64:72] = amount.to_bytes(8, 'little'); body[108] = 1
        self.put(key, TOKEN, body)

    def build(self, query):
        wallet = self.wallet; amount = raw(query['amount']); buy = query['inputMint'] == SOL
        output = amount * 12 // 100 if buy else amount * 100 // 12
        source = associated(wallet, query['inputMint'], TOKEN); dest = associated(wallet, query['outputMint'], TOKEN)
        data = hashlib.sha256(b'global:route_v2').digest()[:8] + amount.to_bytes(8, 'little') + output.to_bytes(8, 'little')
        data += (100).to_bytes(2, 'little') + bytes(4) + (1).to_bytes(4, 'little') + bytes([17, int(buy), 16, 39, 0, 1])
        oracle = str(Pubkey.find_program_address([b'oracle', bytes(Pubkey.from_string(self.pool))],
                                                 Pubkey.from_string(WHIRLPOOL))[0])
        header = [(wallet, True, False), (source, False, True), (dest, False, True),
                  (query['inputMint'], False, False), (query['outputMint'], False, False),
                  (TOKEN, False, False), (TOKEN, False, False), (JUPITER, False, False),
                  ('D8cy77BBepLMngZx6ZukaTff5hCt1HrWyKk3Hnd9oitf', False, False), (JUPITER, False, False)]
        dex = [WHIRLPOOL, TOKEN, wallet, self.pool, associated(wallet, SOL, TOKEN), self.vault_a,
               associated(wallet, USDC, TOKEN), self.vault_b]
        dex += [self.ticks[i] for i in (0, -5632, -11264) if buy] if buy else [self.ticks[i] for i in (0, 5632, 11264)]
        dex.append(oracle)
        accounts = header + [(key, False, 3 <= i <= 10) for i, key in enumerate(dex)]
        wsol = associated(wallet, SOL, TOKEN)
        setup = [ix(ATA, b'\x01', [(wallet, True, True), (wsol, False, True), (wallet, False, False),
                                  (SOL, False, False), ('11111111111111111111111111111111', False, False), (TOKEN, False, False)])]
        if buy:
            setup += [ix('11111111111111111111111111111111', (2).to_bytes(4, 'little') + amount.to_bytes(8, 'little'),
                         [(wallet, True, True), (wsol, False, True)]), ix(TOKEN, b'\x11', [(wsol, False, True)])]
        return {'inputMint': query['inputMint'], 'outputMint': query['outputMint'], 'inAmount': query['amount'],
                'outAmount': str(output), 'otherAmountThreshold': str(output*99//100), 'swapMode': 'ExactIn',
                'slippageBps': 100, 'priceImpactPct': '0.0001', 'routePlan': [{'percent': 100, 'bps': 10000,
                    'swapInfo': {'ammKey': self.pool, 'label': 'Whirlpool', 'inputMint': query['inputMint'],
                                 'outputMint': query['outputMint'], 'inAmount': query['amount'], 'outAmount': str(output)}}],
                'swapInstruction': ix(JUPITER, data, accounts), 'setupInstructions': setup,
                'computeBudgetInstructions': [ix('ComputeBudget111111111111111111111111111111', b'\x03'+(100).to_bytes(8, 'little'), [])],
                'cleanupInstruction': ix(TOKEN, b'\x09', [(wsol, False, True), (wallet, False, True), (wallet, True, False)])}

    def evaluate(self, tx):
        from solders.signature import Signature
        unsigned = VersionedTransaction.populate(tx.message, [Signature.default()])
        keys, loaded = message_keys(unsigned, [])
        limit, price = 0, 0
        for instruction in tx.message.instructions:
            data = bytes(instruction.data)
            if keys[instruction.program_id_index] == 'ComputeBudget111111111111111111111111111111':
                if data[0] == 2: limit = int.from_bytes(data[1:], 'little')
                if data[0] == 3: price = int.from_bytes(data[1:], 'little')
            if keys[instruction.program_id_index] == JUPITER:
                amount = int.from_bytes(data[8:16], 'little'); output = int.from_bytes(data[16:24], 'little'); buy = bool(data[35])
        fee = 5000+(limit*price+999999)//1000000
        pre = [self.accounts.get(key, {}).get('lamports', 0) for key in keys]; post = pre.copy()
        cash = -amount-fee if buy else output-fee; post[0] += cash
        post[keys.index(self.vault_a)] += amount if buy else -output
        ata = associated(self.wallet, USDC, TOKEN); index = keys.index(ata)
        def token(amount):
            return {'accountIndex': index, 'owner': self.wallet, 'mint': USDC, 'programId': TOKEN,
                    'uiTokenAmount': {'amount': str(amount), 'decimals': 6}}
        target = self.tokens+output if buy else self.tokens-amount
        if target < 0:raise StateError('isolated_bank_insufficient_position')
        return {'err': None, 'fee': fee, 'unitsConsumed': 30000, 'replacementBlockhash': None,
                'preBalances': pre, 'postBalances': post, 'preTokenBalances': [token(self.tokens)],
                'postTokenBalances': [token(target)], 'loadedAddresses': loaded}

    def rpc(self, method, params, deadline):
        if time.monotonic() >= deadline:raise StateError('isolated_rpc_deadline')
        if method == 'getMultipleAccounts':
            return {'context': {'slot': self.slot}, 'value': [copy.deepcopy(self.accounts.get(key)) for key in params[0]]}
        if method == 'getAccountInfo':return {'context': {'slot': self.slot}, 'value': copy.deepcopy(self.accounts.get(params[0]))}
        if method == 'getLatestBlockhash':return {'value': {'blockhash': str(Hash.new_unique()), 'lastValidBlockHeight': self.slot+150}}
        if method == 'getBlockHeight':return self.slot
        if method == 'simulateTransaction':
            tx = VersionedTransaction.from_bytes(base64.b64decode(params[0]))
            return {'context': {'slot': self.slot}, 'value': self.evaluate(tx)}
        if method == 'getFeeForMessage':
            from solders.message import from_bytes_versioned
            message = from_bytes_versioned(base64.b64decode(params[0])); limit, price = 0, 0
            for item in message.instructions:
                if str(message.account_keys[item.program_id_index]) == 'ComputeBudget111111111111111111111111111111':
                    data = bytes(item.data)
                    if data[0] == 2:limit = int.from_bytes(data[1:], 'little')
                    if data[0] == 3:price = int.from_bytes(data[1:], 'little')
            return {'value': 5000+(limit*price+999999)//1000000}
        raise StateError('isolated_rpc_method_blocked')

    def sign(self, event):
        unsigned = VersionedTransaction.from_bytes(base64.b64decode(event['unsigned_transaction']))
        return VersionedTransaction(unsigned.message, [self.key])

    def send(self, event):
        tx = VersionedTransaction.from_bytes(base64.b64decode(event['signed_transaction']))
        meta = self.evaluate(tx)
        self.balance = meta['postBalances'][0]; self.tokens = int(meta['postTokenBalances'][0]['uiTokenAmount']['amount'])
        self.accounts[self.wallet]['lamports'] = self.balance
        index = [str(key) for key in tx.message.account_keys].index(self.vault_a)
        self.accounts[self.vault_a]['lamports'] = meta['postBalances'][index]
        self.token_account(associated(self.wallet, USDC, TOKEN), USDC, self.wallet, self.tokens)
        self.slot += 1; self.sent.append(event['request_id'])
        self.receipts[event['order_id']] = {'version': 0, 'transaction': [event['signed_transaction'], 'base64'],
                                          'slot': self.slot, 'blockTime': int(time.time()), 'meta': meta}

    def observe_rpc(self, method, params):
        if method != 'getTransaction':raise StateError('isolated_receipt_read_only')
        return copy.deepcopy(self.receipts.get(params[0]))

    def snapshot(self, clock):
        return {'wallet': self.wallet, 'observed_at': clock(), 'chain_positions': {USDC: str(self.tokens)} if self.tokens else {},
                'balance_lamports': str(self.balance)}
