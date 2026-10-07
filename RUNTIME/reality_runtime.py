"""Single bounded reality runner; all external trading operations are reads."""
import argparse
from contextlib import closing
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'CORE'))
from mykonos_meme_scout.reality_run import RealityBook, DB, digest
from mykonos_meme_scout.reality_accounting import size
from mykonos_meme_scout.execution_rules import number as D, price_exit
from mykonos_meme_scout.reality_transport import RealityTransport, authority_off, fx_quote
from mykonos_meme_scout.jupiter_read import public_wallet
from mykonos_meme_scout.jupiter_rpc_session import rpc_session
from mykonos_meme_scout import paper_forward as paper
from mykonos_meme_scout.config import Config
from mykonos_meme_scout.cli import scan_once
from execution_positions import POLICY

SOURCE = ROOT/'CORE/data/imc-main.db'


def frozen_policy(request_id):
    cfg = authority_off()
    return {'config': cfg, 'config_hash': digest(cfg), 'entry': paper.load_config(),
        'exit': dict(POLICY), 'position_fraction': str(min(D('.10'), D(cfg['position_fraction']))),
        'max_session_trades': cfg['max_session_trades'], 'reserve_eur': '0',
        'mode': 'REALITY_FORWARD_SHADOW_NO_BROADCAST', 'request_id': request_id,
        'cost_evidence': 'EXACT_UNSIGNED_SIMULATION_REQUIRED_NO_ALLOWANCE',
        'route_scope': 'EXISTING_CLOSED_SOL_USDC_POLICY_NO_MEME_PERMISSION', 'wallet': public_wallet()}


def guarded(run):
    cfg = authority_off()
    if digest(cfg) != run['policy']['config_hash']:
        raise ValueError('frozen_risk_config_changed')
    # Observe the existing canonical kill switch without initializing/writing its ledger.
    ledger = ROOT/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE/execution-ledger.db'
    with sqlite3.connect(Path(ledger).resolve().as_uri()+'?mode=ro', uri=True, timeout=2) as c:
        row = c.execute("SELECT value FROM meta WHERE key='kill_reason'").fetchone()
        if not row or json.loads(row[0]) is not None:
            raise ValueError('canonical_kill_switch_or_ledger_unknown')


def snapshot(mint):
    with closing(sqlite3.connect(SOURCE.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)) as c:
        row = c.execute('SELECT payload_json FROM snapshots WHERE address=? ORDER BY id DESC LIMIT 1', (mint,)).fetchone()
        if not row:
            raise ValueError('market_snapshot_missing')
        result = json.loads(row[0])
    if (result['address'] != mint or result.get('market_data_stale') is not False
            or not 0 <= time.time()-paper.epoch(result['captured_at']) <= 10):
        raise ValueError('market_stale')
    return result


def candidates(book, run):
    with closing(sqlite3.connect(SOURCE.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute('SELECT id,address,payload_json FROM snapshots WHERE id>? ORDER BY id LIMIT 200',
                         (run['checkpoint'],)).fetchall()
        for row in rows:
            current = json.loads(row['payload_json'])
            prior = c.execute('SELECT payload_json FROM snapshots WHERE address=? AND id<? ORDER BY id DESC LIMIT 1',
                              (row['address'], row['id'])).fetchone()
            reasons = paper.brain(current, json.loads(prior[0]) if prior else None,
                                 run['policy']['entry'], time.time())
            if current.get('address') != row['address']:
                reasons.append('snapshot_identity')
            with book.transaction():
                updated = book.get(run['run_id'])
                updated['checkpoint'] = row['id']
                if reasons:
                    updated['rejected'] += 1
                book.audit(updated, 'candidate_risk', {'snapshot_id': row['id'], 'mint': row['address'], 'failures': reasons})
                book.save(updated)
            if not reasons:
                return current, row['id']
    return None


def trade_step(book, run, transport):
    active = book.open_trade(run['run_id'])
    if active is None:
        if run['status'] != 'RUNNING' or run['stop_requested']:
            return
        used = sum(t['execution_state'] != 'REJECTED' for t in book.trades(run['run_id']))
        if used >= run['policy']['max_session_trades']:
            with book.transaction():
                updated=book.get(run['run_id']);updated['entry_stop_reason']='SESSION_TRADE_LIMIT';book.save(updated)
            return
        scan_once(Config(), SOURCE, 30, manage_open_positions=False, watcher_priority=True)
        decision = candidates(book, run)
        if decision is None:
            return
        candidate, snapshot_id = decision
        side, mint = 'BUY', candidate['address']
    else:
        if active['execution_state'] != 'POSITION_OPEN':
            raise ValueError('unknown_position_never_resend')
        side, mint = 'SELL', active['mint']
        candidate, snapshot_id = active['candidate'], active['candidate_id']
    claim = None
    try:
        guarded(run)
        fx = fx_quote()
        rate = D(fx['sol_eur_ask'])
        run = book.get(run['run_id'])
        reserve = D(run['policy']['config']['reserve_lamports'])/10**9*rate
        if side == 'BUY':
            stats=book.status(run['run_id'])['metrics']
            if D(stats['net_pnl_eur']) <= -D(run['policy']['config']['max_session_loss_lamports'])/10**9*rate:
                book.control('stop',run['run_id'])
                with book.transaction():
                    stopped=book.get(run['run_id']);stopped['stop_reason']='SESSION_LOSS_LIMIT';book.save(stopped)
                return
            stake = size(run['equity_eur'], run['cash_eur'], run['policy']['position_fraction'], reserve)
            amount = int(stake/rate*10**9)
        else:
            amount = int(active['remaining_raw'])
        # Initial quote is an observation only. After a real delay obtain a NEW
        # route/message and never backdate an entry to the better initial quote.
        with rpc_session():
            initial = transport.quote(side, mint, amount)
        time.sleep(run['policy']['entry']['execution_delay_seconds'])
        with rpc_session():
            settled = transport.quote(side, mint, amount)
            fresh = snapshot(mint)
            mid = D(fresh['price_usd'])/D(fx['usd_per_eur_mid'])
            if side == 'BUY' and time.time()-paper.epoch(candidate['captured_at']) > run['policy']['config']['signal_max_age_seconds']:
                raise ValueError('stale_signal_before_claim')
            if side == 'SELL':
                mark=transport.mark(active,settled,fx)
                value=D(mark['output_raw'])/10**9*D(fx['sol_eur_bid'])-D(mark['network_eur'])-D(mark['other_eur'])
                invested=D(active['stake_eur'])+D(active['entry_network_eur'])+D(active['entry_other_eur'])
                peak=max(value,D(active['peak_liquidation_eur']) if active['peak_liquidation_eur'] is not None else value)
                exit_policy=run['policy']['exit']
                reason = active['exit_reason'] or ('controlled_stop' if run['stop_requested'] else
                    'maximum_hold' if time.time()-active['entry_at'] >= exit_policy['maximum_hold_seconds'] else
                    price_exit(invested,value,peak,exit_policy))
                book.mark(run['run_id'],value,mid=mid,exit_reason=reason,evidence=mark)
                if not reason:
                    return
            trade, claim = book.claim(run['run_id'], candidate, snapshot_id, side=side,
                trade_id=active['trade_id'] if active else None, input_raw=str(amount), reserve_eur=reserve)
            evidence = transport.execute(claim, settled, fx,
                mid if side == 'BUY' else (D(fx['sol_eur_bid'])+rate)/2)
            evidence['initial_route_identity'] = initial['route_identity']
            evidence['initial_minimum_output_raw'] = initial['minimum_output_raw']
            evidence['quote_drift_raw'] = str(int(settled['minimum_output_raw'])-int(initial['minimum_output_raw']))
            guarded(book.get(run['run_id']))
            book.fill(claim, evidence, mid_eur=mid, output_eur=None if side == 'BUY' else
                      D(evidence['output_raw'])/10**9*D(fx['sol_eur_bid']))
    except Exception as error:
        if claim:
            book.failure(run['run_id'], str(error)[:200], trade_id=claim['trade_id'])
        elif side == 'SELL':
            book.mark(run['run_id'], None, mid=None)
            book.failure(run['run_id'], str(error)[:200])
        else:
            book.failure(run['run_id'], str(error)[:200])
            if book.get(run['run_id'])['unknown'] >= run['policy']['config']['max_api_failures']:
                with book.transaction():
                    failed=book.get(run['run_id'])
                    failed.update(status='BLOCKED',stop_reason='UNVERIFIED_ENTRY_LIMIT',stop_requested=True)
                    book.save(failed)


def run_loop(book, run_id):
    transport = RealityTransport()
    finishing_started = None
    while True:
        run = book.tick(run_id)
        if run['status'] == 'BLOCKED':
            break
        if run['status'] == 'FINISHING':
            finishing_started = finishing_started or time.monotonic()
            if book.open_trade(run_id) is None:
                break
            if time.monotonic()-finishing_started > 60:
                with book.transaction():
                    failed = book.get(run_id)
                    failed.update(status='BLOCKED', stop_reason='SHADOW_EXIT_EVIDENCE_TIMEOUT')
                    book.save(failed)
                break
        try:
            guarded(run)
            trade_step(book, run, transport)
        except Exception as error:
            with book.transaction():
                failed = book.get(run_id)
                failed['runtime_errors'] += 1
                failed.update(status='BLOCKED', stop_reason=str(error)[:200], stop_requested=True)
                book.save(failed)
                book.audit(failed, 'runtime_error', {'reason': str(error)[:200]})
            break
        time.sleep(.5)
    return book.finish(run_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('create', 'run', 'status', 'stop', 'cleanup', 'recover'))
    parser.add_argument('--duration')
    parser.add_argument('--run-id')
    parser.add_argument('--request-id')
    args = parser.parse_args()
    book = RealityBook()
    try:
        if args.command == 'create':
            run = book.start(args.duration, frozen_policy(args.request_id))
            if SOURCE.exists():
                with closing(sqlite3.connect(SOURCE.resolve().as_uri()+'?mode=ro',uri=True)) as c:
                    checkpoint=c.execute('SELECT COALESCE(MAX(id),0) FROM snapshots').fetchone()[0]
                with book.transaction():
                    run=book.get(run['run_id']);run['checkpoint']=checkpoint;book.save(run)
            print(run['run_id'])
        elif args.command == 'run':
            run_loop(book, args.run_id)
        elif args.command == 'stop':
            print(json.dumps(book.control('stop')))
        elif args.command == 'recover':
            print(json.dumps(book.recover(args.run_id)))
        elif args.command == 'cleanup':
            with book.transaction():
                run = book.get(args.run_id)
                run['cleanup_confirmed'] = True
                book.save(run)
                book.audit(run, 'native_cleanup_confirmed', {})
        else:
            print(json.dumps(book.status(), allow_nan=False))
    except Exception as error:
        if args.command=='run' and args.run_id:
            book.abort(args.run_id,str(error)[:200])
        raise
    finally:
        book.close()


if __name__ == '__main__':
    main()
