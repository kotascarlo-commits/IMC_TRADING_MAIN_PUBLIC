"""Closed route policy for controlled SOL/USDC validation via Whirlpool.

Unknown routes fail closed. Jupiter V2 bytes, pool, vaults, ticks, oracle and
legacy mint restrictions are checked against finalized account evidence.
Sources: Jupiter program-owned Anchor IDL; orca-so/whirlpools swap.rs and
state/whirlpool.rs. This deliberately grants no arbitrary meme-token route.
"""
import base64
from decimal import Decimal
import hashlib
import json

from solders.pubkey import Pubkey
from .jupiter_prepare import build_instructions, assemble, USDC, JUPITER
from .jupiter_instructions import validate_auxiliary, encoded
from .jupiter_simulation import SOL, TOKEN, associated, raw

WHIRLPOOL = 'whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc'
RAYDIUM = 'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK'
USDC_AUTHORITIES = ('BJE5MMbqXjVwjAF7oxwPYXnTXDyspzZyt4vwenNw5ruG',
                    '7dGbd2QZcCKcTndnHcTL8q7SMVXAkp688NTQYwrRCrar')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def account(reader, key, owner, *, length=None, discriminator=None):
    value = reader(key)
    if (not isinstance(value, dict) or value.get('owner') != owner
            or value.get('executable') is not False):
        raise ValueError('route_account_owner_unknown')
    body = base64.b64decode(value['data'][0], validate=True)
    if length is not None and len(body) != length:
        raise ValueError('route_account_layout_unknown')
    if discriminator and body[:8] != hashlib.sha256(('account:' + discriminator).encode()).digest()[:8]:
        raise ValueError('route_account_discriminator_unknown')
    return body


def verify(build, query, reader):
    build_instructions(build, query)
    auxiliary = validate_auxiliary(build, query)
    if set((query['inputMint'], query['outputMint'])) != {SOL, USDC}:
        raise ValueError('route_pair_unsupported')
    plan = build['routePlan']
    if len(plan) != 1:
        raise ValueError('route_multihop_or_split_unsupported')
    step = plan[0]; swap = step['swapInfo']
    if (step.get('bps') != 10000 or step.get('percent') != 100 or swap.get('label') not in ('Whirlpool', 'Raydium CLMM')
            or query.get('dexes',swap.get('label'))!=swap.get('label')
            or swap.get('inputMint') != query['inputMint'] or swap.get('outputMint') != query['outputMint']
            or raw(swap['inAmount']) != raw(query['amount']) or swap['outAmount'] != build['outAmount']):
        raise ValueError('route_quote_plan_mismatch')
    if swap['label'] == 'Raydium CLMM':
        return verify_raydium(build, query, reader, auxiliary)
    ix = build['swapInstruction']; data = encoded(ix)
    direct = hashlib.sha256(b'global:route_v2').digest()[:8]
    # Fixed V2 header + one fully decoded Swap::Whirlpool(bool), bps, indexes.
    if (len(data) != 40 or data[:8] != direct or int.from_bytes(data[30:34], 'little') != 1
            or data[34] != 17 or data[35] not in (0, 1) or data[36:] != b'\x10\x27\x00\x01'):
        raise ValueError('route_encoded_plan_unknown_or_mismatch')
    a_to_b = bool(data[35]); wallet = query['taker']; pool_key = swap['ammKey']
    pool = account(reader, pool_key, WHIRLPOOL, length=653, discriminator='Whirlpool')
    key = lambda lo: str(Pubkey.from_bytes(pool[lo:lo+32]))
    mint_a, vault_a, mint_b, vault_b = key(101), key(133), key(181), key(213)
    if ((mint_a, mint_b) if a_to_b else (mint_b, mint_a)) != (query['inputMint'], query['outputMint']):
        raise ValueError('route_pool_mints_or_direction_mismatch')
    if int.from_bytes(pool[49:65], 'little') == 0 or int.from_bytes(pool[65:81], 'little') == 0:
        raise ValueError('route_pool_no_active_liquidity')
    fee_rate = int.from_bytes(pool[45:47], 'little')
    if fee_rate > 10000:
        raise ValueError('route_pool_fee_exceeded')
    mints = {}
    for mint in (mint_a, mint_b):
        body = account(reader, mint, TOKEN, length=82)
        expected_authorities = USDC_AUTHORITIES if mint == USDC else ('11111111111111111111111111111111',)*2
        expected_option = 1 if mint == USDC else 0
        if (body[45] != 1 or body[44] != (6 if mint == USDC else 9)
                or int.from_bytes(body[:4], 'little') != expected_option
                or int.from_bytes(body[46:50], 'little') != expected_option
                or (str(Pubkey.from_bytes(body[4:36])), str(Pubkey.from_bytes(body[50:82]))) != expected_authorities):
            raise ValueError('route_mint_authority_or_initialization_unknown')
        mints[mint] = body[44]
    reserves = {}
    for vault, mint in ((vault_a, mint_a), (vault_b, mint_b)):
        body = account(reader, vault, TOKEN, length=165)
        if (str(Pubkey.from_bytes(body[:32])) != mint or str(Pubkey.from_bytes(body[32:64])) != pool_key
                or body[108] != 1 or int.from_bytes(body[72:76], 'little') != 0
                or int.from_bytes(body[129:133], 'little') != 0):
            raise ValueError('route_vault_identity_or_authority_unknown')
        reserves[mint] = int.from_bytes(body[64:72], 'little')
    accounts = ix['accounts']
    if len(accounts) != 22:
        raise ValueError('route_remaining_accounts_unknown')
    oracle = str(Pubkey.find_program_address([b'oracle', bytes(Pubkey.from_string(pool_key))],
                                             Pubkey.from_string(WHIRLPOOL))[0])
    expected = [WHIRLPOOL, TOKEN, wallet, pool_key, associated(wallet, mint_a, TOKEN), vault_a,
                associated(wallet, mint_b, TOKEN), vault_b]
    if [a['pubkey'] for a in accounts[10:18]] != expected or accounts[21]['pubkey'] != oracle:
        raise ValueError('route_dex_account_set_mismatch')
    if any(a['isSigner'] for a in accounts[10:]) or any(
            a['isWritable'] != (13 <= i <= 20) for i, a in enumerate(accounts) if i >= 10):
        raise ValueError('route_dex_account_privileges_mismatch')
    spacing = int.from_bytes(pool[41:43], 'little')
    if not spacing:
        raise ValueError('route_tick_spacing_unknown')
    width = spacing * 88
    tick = int.from_bytes(pool[81:85], 'little', signed=True)
    start = ((tick if a_to_b else tick + spacing) // width) * width
    for i, item in enumerate(accounts[18:21]):
        body = account(reader, item['pubkey'], WHIRLPOOL, length=9988, discriminator='TickArray')
        actual_start = int.from_bytes(body[8:12], 'little', signed=True)
        expected_start = start + i * width * (-1 if a_to_b else 1)
        pda = str(Pubkey.find_program_address([b'tick_array', bytes(Pubkey.from_string(pool_key)),
                str(actual_start).encode()], Pubkey.from_string(WHIRLPOOL))[0])
        if (str(Pubkey.from_bytes(body[-32:])) != pool_key or item['pubkey'] != pda
                or actual_start != expected_start or not -443636-width < actual_start <= 443636):
            raise ValueError('route_tick_pool_pda_or_sequence_mismatch')
    for mint in (query['inputMint'], query['outputMint']):
        value = reader(associated(wallet, mint, TOKEN))
        if value is None:
            if mint == query['inputMint'] and mint != SOL:
                raise ValueError('route_wallet_input_account_missing')
            continue
        body = account(reader, associated(wallet, mint, TOKEN), TOKEN, length=165)
        if (str(Pubkey.from_bytes(body[:32])) != mint or str(Pubkey.from_bytes(body[32:64])) != wallet
                or body[108] != 1 or int.from_bytes(body[72:76], 'little') != 0
                or int.from_bytes(body[129:133], 'little') != 0
                or mint == SOL and int.from_bytes(body[64:72], 'little') != 0):
            raise ValueError('route_wallet_token_state_unknown')
        if mint == query['inputMint'] and mint != SOL and int.from_bytes(body[64:72], 'little') < raw(query['amount']):
            raise ValueError('route_wallet_token_balance_insufficient')
    # Oracle can be absent for the legacy static-fee instruction; it is still
    # bound to the canonical pool PDA, never an arbitrary recipient.
    price_raw = (Decimal(int.from_bytes(pool[65:81], 'little')) / (2**64)) ** 2
    expected_out = Decimal(raw(query['amount'])) * (price_raw if a_to_b else 1 / price_raw)
    deviation = abs(Decimal(raw(build['outAmount'])) / expected_out - 1) * 10000
    # Conservative vault inventory floor, in USDC units; no oracle price guess.
    sol_price_raw = price_raw if mint_a == SOL else 1 / price_raw
    liquidity = (Decimal(reserves[USDC]) + Decimal(reserves[SOL])*sol_price_raw) / (10 ** mints[USDC])
    if liquidity < 100000 or deviation > 150 or raw(build['outAmount']) > reserves[query['outputMint']]:
        raise ValueError('route_liquidity_or_price_deviation_exceeded')
    return {'route_identity': digest({'query': query, 'build': build}),
            'route_plan': plan, 'pool': pool_key, 'liquidity_usd': str(liquidity),
            'price_deviation_bps': str(deviation), 'pool_fee_rate_millionths': fee_rate,
            'mint_decimals': mints, **auxiliary, 'full_route_semantics_verified': True}


def verify_raydium(build, query, reader, auxiliary):
    """Legacy Enum26 only; reject new seeds, fee modes and dynamic fees.

    Layouts independently checked against finalized deployed accounts and
    raydium-io/raydium-clmm states/{pool,config,tick_array}.rs.
    """
    ix = build['swapInstruction']; data = encoded(ix); accounts = ix['accounts']
    if (len(data) != 39 or data[:8] != hashlib.sha256(b'global:route_v2').digest()[:8]
            or data[30:] != b'\x01\x00\x00\x00\x1a\x10\x27\x00\x01' or len(accounts) != 25):
        raise ValueError('route_raydium_encoded_plan_unknown')
    wallet = query['taker']; pool_key = build['routePlan'][0]['swapInfo']['ammKey']
    pool = account(reader, pool_key, RAYDIUM, length=1544, discriminator='PoolState')
    key = lambda lo: str(Pubkey.from_bytes(pool[lo:lo+32]))
    config_key, mint_a, mint_b, vault_a, vault_b, observation = (key(lo) for lo in (9,73,105,137,169,201))
    spacing = int.from_bytes(pool[235:237], 'little'); tick = int.from_bytes(pool[269:273], 'little', signed=True)
    if (not spacing or not 0 < int.from_bytes(pool[237:253], 'little')
            or pool[389] & 16 or any(pool[390:393]) or any(pool[1096:1176])
            or set((mint_a,mint_b)) != {SOL,USDC} or bytes(Pubkey.from_string(mint_a)) >= bytes(Pubkey.from_string(mint_b))):
        raise ValueError('route_raydium_pool_version_status_or_liquidity_unknown')
    pda = lambda seeds: str(Pubkey.find_program_address(seeds, Pubkey.from_string(RAYDIUM))[0])
    if pool_key != pda([b'pool',bytes(Pubkey.from_string(config_key)),bytes(Pubkey.from_string(mint_a)),bytes(Pubkey.from_string(mint_b))]):
        raise ValueError('route_raydium_pool_pda_mismatch')
    config = account(reader, config_key, RAYDIUM, length=117, discriminator='AmmConfig')
    if (config_key != pda([b'amm_config', int.from_bytes(config[9:11],'little').to_bytes(2,'big')])
            or int.from_bytes(config[51:53],'little') != spacing):
        raise ValueError('route_raydium_config_pda_or_spacing_mismatch')
    fee = int.from_bytes(config[47:51],'little')
    if fee > 10000 or int.from_bytes(config[43:47],'little') + int.from_bytes(config[53:57],'little') > 1000000:
        raise ValueError('route_raydium_fee_unknown')
    a_to_b = query['inputMint'] == mint_a
    source, dest = (associated(wallet, query[m], TOKEN) for m in ('inputMint','outputMint'))
    expected = [RAYDIUM,wallet,config_key,pool_key,source,dest,
                vault_a if a_to_b else vault_b,vault_b if a_to_b else vault_a,observation,TOKEN]
    if ([a['pubkey'] for a in accounts[10:20]] != expected or accounts[24]['pubkey'] != JUPITER
            or any(a['isSigner'] for a in accounts[10:]) or any(
                a['isWritable'] != (13<=i<=18 or 20<=i<=23) for i,a in enumerate(accounts) if i>=10)):
        raise ValueError('route_raydium_accounts_or_privileges_mismatch')
    obs = account(reader, observation, RAYDIUM, length=4483, discriminator='ObservationState')
    # Legacy observation accounts can be keypairs. SwapSingle constrains the
    # stored pool observation_key, not seeds; both account directions bind it.
    if str(Pubkey.from_bytes(obs[19:51])) != pool_key:
        raise ValueError('route_raydium_observation_mismatch')
    bitmap_key = accounts[21]['pubkey']
    extension = account(reader, bitmap_key, RAYDIUM, length=1832, discriminator='TickArrayBitmapExtension')
    if (bitmap_key != pda([b'pool_tick_array_bitmap_extension',bytes(Pubkey.from_string(pool_key))])
            or str(Pubkey.from_bytes(extension[8:40])) != pool_key):
        raise ValueError('route_raydium_bitmap_mismatch')
    bitmap = int.from_bytes(pool[904:1032],'little'); width=spacing*60
    index = tick//width + 512
    for item in (accounts[20],accounts[22],accounts[23]):
        while 0<=index<1024 and not (bitmap>>index)&1:index += -1 if a_to_b else 1
        if not 0<=index<1024:
            raise ValueError('route_raydium_extension_ticks_unsupported')
        start=(index-512)*width
        body=account(reader,item['pubkey'],RAYDIUM,length=10240,discriminator='TickArrayState')
        if (str(Pubkey.from_bytes(body[8:40])) != pool_key or int.from_bytes(body[40:44],'little',signed=True)!=start
                or item['pubkey']!=pda([b'tick_array',bytes(Pubkey.from_string(pool_key)),start.to_bytes(4,'big',signed=True)])):
            raise ValueError('route_raydium_tick_pda_sequence_mismatch')
        index += -1 if a_to_b else 1
    reserves={}; mints={}
    for mint in (mint_a,mint_b):
        body=account(reader,mint,TOKEN,length=82); option=1 if mint==USDC else 0
        authorities=USDC_AUTHORITIES if mint==USDC else ('11111111111111111111111111111111',)*2
        if (body[45]!=1 or body[44]!=(6 if mint==USDC else 9) or int.from_bytes(body[:4],'little')!=option
                or int.from_bytes(body[46:50],'little')!=option
                or (str(Pubkey.from_bytes(body[4:36])),str(Pubkey.from_bytes(body[50:82])))!=authorities):
            raise ValueError('route_raydium_mint_authority_mismatch')
        mints[mint]=body[44]
    for vault,mint in ((vault_a,mint_a),(vault_b,mint_b)):
        body=account(reader,vault,TOKEN,length=165)
        if (str(Pubkey.from_bytes(body[:32]))!=mint or str(Pubkey.from_bytes(body[32:64]))!=pool_key or body[108]!=1
                or int.from_bytes(body[72:76],'little') or int.from_bytes(body[129:133],'little')
                or vault!=pda([b'pool_vault',bytes(Pubkey.from_string(pool_key)),bytes(Pubkey.from_string(mint))])):
            raise ValueError('route_raydium_vault_mismatch')
        reserves[mint]=int.from_bytes(body[64:72],'little')
    for mint in (query['inputMint'],query['outputMint']):
        value=reader(associated(wallet,mint,TOKEN))
        if value is None:
            if mint==query['inputMint'] and mint!=SOL:raise ValueError('route_wallet_input_account_missing')
            continue
        body=account(reader,associated(wallet,mint,TOKEN),TOKEN,length=165)
        if (str(Pubkey.from_bytes(body[:32]))!=mint or str(Pubkey.from_bytes(body[32:64]))!=wallet or body[108]!=1
                or int.from_bytes(body[72:76],'little') or int.from_bytes(body[129:133],'little')
                or mint==SOL and int.from_bytes(body[64:72],'little')):
            raise ValueError('route_wallet_token_state_unknown')
        if mint==query['inputMint'] and mint!=SOL and int.from_bytes(body[64:72],'little')<raw(query['amount']):
            raise ValueError('route_wallet_token_balance_insufficient')
    price=(Decimal(int.from_bytes(pool[253:269],'little'))/2**64)**2
    if price<=0:raise ValueError('route_raydium_price_unknown')
    expected_out=Decimal(raw(query['amount']))*(price if a_to_b else 1/price)
    deviation=abs(Decimal(raw(build['outAmount']))/expected_out-1)*10000
    sol_price=price if mint_a==SOL else 1/price
    liquidity=(Decimal(reserves[USDC])+Decimal(reserves[SOL])*sol_price)/10**6
    if liquidity<100000 or deviation>150 or raw(build['outAmount'])>reserves[query['outputMint']]:
        raise ValueError('route_raydium_liquidity_or_price_exceeded')
    return {'route_identity':digest({'query':query,'build':build}),'route_plan':build['routePlan'],'pool':pool_key,
            'liquidity_usd':str(liquidity),'price_deviation_bps':str(deviation),'pool_fee_rate_millionths':fee,
            'mint_decimals':mints,**auxiliary,'full_route_semantics_verified':True}


def verify_transaction(build, query, tables, transaction, limit):
    rebuilt = assemble(build, query, tables, str(transaction.message.recent_blockhash), compute_unit_limit=limit)
    if bytes(rebuilt) != bytes(transaction):
        raise ValueError('route_transaction_message_mismatch')
    return True
