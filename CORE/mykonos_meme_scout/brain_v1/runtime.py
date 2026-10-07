"""Concurrent market observation, inference, paper exits and challenger learning.

Collector and training are separate processes. This process never submits orders;
paper positions are immutable events. Restart reconstructs the event state.
"""
from __future__ import annotations
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from .costs import CostPolicy, net_return
from . import VERSION
from .data import build_sequence, make_records, normalize_observation, open_readonly, split_records
from .governor import GovernorRegistry
from .ledger import BrainLedger, canonical, utc_now
from .model import SequenceBrain
from .pipeline import NetCalibrator, evaluate_records, policy_decision, research_policy_decision, save_json
from .validation import TradeEvidence


def child_args(source, runtime, command, *args):
    return [sys.executable,'-B','-u','-m','mykonos_meme_scout.brain_v1',
            '--source-root',str(source),'--runtime-dir',str(runtime),command,*map(str,args)]


def start_child(args, logfile):
    stream=Path(logfile).open('a',encoding='utf-8')
    try:
        process=subprocess.Popen(args,stdout=stream,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),cwd=Path(__file__).resolve().parents[2])
    finally:
        stream.close()
    return process


class MarketStore:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(self.path,timeout=10)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.executescript('''CREATE TABLE IF NOT EXISTS snapshots(
          id INTEGER PRIMARY KEY, captured_at TEXT NOT NULL, address TEXT NOT NULL,
          symbol TEXT NOT NULL, price_usd REAL NOT NULL, payload_json TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS market_coin_time ON snapshots(address,captured_at);
          CREATE TRIGGER IF NOT EXISTS market_no_update BEFORE UPDATE ON snapshots
          BEGIN SELECT RAISE(ABORT,'market observations are immutable'); END;
          CREATE TRIGGER IF NOT EXISTS market_no_delete BEFORE DELETE ON snapshots
          BEGIN SELECT RAISE(ABORT,'market observations are immutable'); END;''')
        self.conn.commit()

    def append(self, snapshot, health):
        payload=snapshot.as_dict()
        payload.update(feed_source=health.get('source','unknown'),feed_complete=bool(health.get('complete',False)),
            feed_latency_ms=health.get('latency_ms',0),source_domain='REAL')
        # Availability time is at receipt, after any cached security knowledge.
        from .data import timestamp
        payload['market_data_stale'] = bool(payload.get('market_data_stale')) or time.time()-timestamp(payload['captured_at'])>5
        payload['provider_captured_at']=payload['captured_at']
        payload['captured_at']=utc_now()
        self.conn.execute('INSERT INTO snapshots(captured_at,address,symbol,price_usd,payload_json) VALUES (?,?,?,?,?)',
            (payload['captured_at'],snapshot.address,snapshot.symbol,snapshot.price_usd,canonical(payload)))
        self.conn.commit()

    def close(self):
        self.conn.close()


def collect_market(runtime, *, runtime_seconds=0):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from ..providers import DexScreenerProvider, GeckoTerminalProvider, GoPlusProvider
    from ..shadow_feed import ShadowBatchWatchProvider, ShadowDiscoveryCoordinator
    runtime=Path(runtime)
    store=MarketStore(runtime/'market.db')
    gecko=GeckoTerminalProvider()
    discovery=ShadowDiscoveryCoordinator(gecko,DexScreenerProvider())
    watch=ShadowBatchWatchProvider(gecko)
    security=GoPlusProvider()
    pool=ThreadPoolExecutor(max_workers=2)
    discover_future=security_future=None
    active={}
    securities={}
    security_at={}
    next_discovery=next_security=0
    start=time.monotonic()
    ticks=0
    try:
        while not (runtime/'STOP').exists() and (not runtime_seconds or time.monotonic()-start<runtime_seconds):
            cycle=time.monotonic()
            if cycle>=next_discovery and discover_future is None:
                discover_future=pool.submit(discovery.discover,30,15,request_timeout=1.5,hard_deadline=2.5)
                next_discovery=cycle+10
            if discover_future is not None and discover_future.done():
                try:
                    found,health=discover_future.result()
                    for snap in found:
                        if snap.address not in active and len(active)<120:
                            active[snap.address]=(snap,cycle)
                except Exception as exc:
                    save_json(runtime/'collector_error.json',{'at':utc_now(),'error':str(exc),'execution_authority_pct':0})
                discover_future=None
            if security_future is not None and security_future.done():
                try:
                    for snap in security_future.result():
                        securities[snap.address]=snap.security
                        security_at[snap.address]=cycle
                except Exception:
                    pass
                security_future=None
            if cycle>=next_security and security_future is None and active:
                due=[snap for address,(snap,_) in active.items() if cycle-security_at.get(address,-1000)>60][:15]
                if due:
                    security_future=pool.submit(security.enrich,due,request_timeout=1.5,hard_deadline=2)
                next_security=cycle+10
            active={a:(s,t) for a,(s,t) in active.items() if cycle-t<1800}
            if active:
                try:
                    items,health=watch.fetch_watch_targets([(a,s.pair_address) for a,(s,_) in active.items()],
                        dex_timeout=.7,gecko_timeout=.5,hedge_delay=.2,hard_deadline=.9)
                    for snap in items:
                        # Security expires; missing refresh never becomes safe.
                        if snap.address in securities and cycle-security_at[snap.address]<=60:
                            snap=replace(snap,security=securities[snap.address])
                        store.append(snap,health)
                        ticks+=1
                except Exception as exc:
                    save_json(runtime/'collector_error.json',{'at':utc_now(),'error':str(exc),'execution_authority_pct':0})
            save_json(runtime/'collector_status.json',{'at':utc_now(),'ticks_written':ticks,'active_coins':len(active),
                'execution_authority_pct':0,'status':'OBSERVING_INCLUDING_REJECTED_COINS'})
            time.sleep(max(.05,1-(time.monotonic()-cycle)))
    finally:
        pool.shutdown(wait=False,cancel_futures=True)
        store.close()
    return {'ticks':ticks,'execution_authority_pct':0}


def load_active(runtime):
    info=json.loads((Path(runtime)/'active_challenger.json').read_text(encoding='utf-8'))
    model_path=Path(info['model_path'])
    if not model_path.is_absolute():model_path=Path(runtime)/model_path
    model=SequenceBrain.load(model_path)
    if model.model_sha256!=info['model_hash']:
        raise ValueError('Active artifact changed')
    calibration=model.calibration
    calibrator=NetCalibrator(calibration.get('weights') if calibration.get('fitted') else None)
    return info,model,calibrator


def online_learning(source, runtime):
    from .online import training_lock
    try:
        with training_lock(runtime):
            save_json(Path(runtime)/'learning_status.json',{'at':utc_now(),'status':'PREPARING','execution_authority_pct':0})
            try:
                result=_online_learning(source,runtime)
            except Exception as exc:
                save_json(Path(runtime)/'learning_status.json',{'at':utc_now(),'status':'ERROR','error':str(exc),'execution_authority_pct':0})
                raise
            save_json(Path(runtime)/'learning_status.json',{'at':utc_now(),'status':'TRAINED' if result.get('model_hash') else 'WAITING',**result})
            return result
    except BlockingIOError:
        return {'trained':False,'reason':'another_learner_running','execution_authority_pct':0}


def _online_learning(source, runtime):
    runtime=Path(runtime)
    if not (runtime/'market.db').exists():
        return {'trained':False,'reason':'no_new_market_database','execution_authority_pct':0}
    info,model,calibrator=load_active(runtime)
    ledger=BrainLedger(runtime/'brain.db')
    try:
        processed=set(ledger.metadata('historical_coin_ids',[]))
        for row in ledger.conn.execute("SELECT payload FROM brain_events WHERE kind='ONLINE_BATCH'"):
            processed.update(json.loads(row[0])['all_coin_ids'])
        groups=defaultdict(list)
        with open_readonly(runtime/'market.db') as conn:
            for raw in conn.execute('SELECT * FROM snapshots ORDER BY address,captured_at'):
                if raw['address'] not in processed:
                    groups[raw['address']].append(normalize_observation(raw,source_path=str(runtime/'market.db')))
        # A deterministic 20% reservation is never trained. A forward promotion
        # additionally requires unknown Explorer IDs and actual preregistration.
        from .online import prepare_online_batch
        batch=prepare_online_batch(groups)
        splits=batch['splits']
        if len({r['coin_id'] for r in splits['train']})<20 or len(splits['train'])<100:
            return {'trained':False,'reason':'waiting_for_20_new_purged_training_coins','new_coins':batch['new_coins'],
                'mature_coins':batch['mature_coins'],'training_coins':len({r['coin_id'] for r in splits['train']}),
                'training_records':len(splits['train']),'execution_authority_pct':0}
        save_json(runtime/'learning_status.json',{'at':utc_now(),'status':'TRAINING','execution_authority_pct':0})
        fit=model.fit(batch['training_records'],epochs=6,batch_size=64)
        run=runtime/'runs'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        run.mkdir(parents=True,exist_ok=False)
        path=run/'challenger.json'
        calibrator=NetCalibrator()
        calibration=calibrator.fit([(r,model.predict(r['sequence'])) for r in splits['validation']])
        model.calibration=calibration
        sha=model.save(path)
        save_json(run/'calibration.json',calibration,immutable=True)
        registry=GovernorRegistry(runtime/'governor')
        try:
            registry.register_challenger(path,training_coins=model.training_coins,historical_coins=processed|set(groups))
        finally:
            registry.close()
        report={'model_hash':sha,'parent_hash':info['model_hash'],'training':fit,
            'new_test':evaluate_records(model,splits['test'],calibrator),
            'new_validation':evaluate_records(model,splits['validation'],calibrator),
            'execution_authority_pct':0,'historical_test_reused_for_selection':False}
        save_json(run/'online_report.json',report,immutable=True)
        if load_active(runtime)[0]['model_hash']!=info['model_hash']:
            raise ValueError('Active parent changed during training; refusing publication')
        ledger.event('ONLINE_BATCH',{'model_hash':sha,'all_coin_ids':sorted(batch['consumed_coins']),'trained_coin_ids':sorted({r['coin_id'] for r in splits['train']})})
        save_json(runtime/'active_challenger.json',{'model_hash':sha,'model_path':str(path.relative_to(runtime)),
            'calibration_path':str((run/'calibration.json').relative_to(runtime)),'execution_authority_pct':0})
        return report
    finally:
        ledger.close()


def exit_decision(prediction, observation, position):
    gain=(observation['price_usd']/position['entry_price']-1)*100
    held=observation['ts']-position['entry_ts']
    if observation['hard_risk_seen']:
        return 'catastrophic_veto'
    if observation['liquidity_usd']<=0 or observation['market_data_stale'] or not observation['feed_complete']:
        return 'unpriceable_safety_exit'
    if observation['liquidity_usd']<position['entry_liquidity']*.5 or gain<=-12:
        return 'hard_risk_exit'
    if held>=600:
        return 'absolute_holding_safety_cap'
    targets=prediction['targets']
    if targets.get('exit_now') is not None and targets.get('continuation') is not None and targets['exit_now']>.6 and targets['continuation']<.5:
        return 'learned_continuation_decay'
    return None


def run_service(source, runtime, *, runtime_seconds=0, collect=False, training_interval_seconds=1800):
    runtime=Path(runtime)
    source=Path(source)
    runtime.mkdir(parents=True,exist_ok=True)
    # OS lock is released automatically on crash, unlike a stale PID file.
    lock=(runtime/'service.lock').open('a+b')
    lock.seek(0)
    if lock.read(1)==b'':
        lock.write(b'0');lock.flush()
    lock.seek(0)
    if os.name=='nt':
        import msvcrt
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            lock.close()
            raise RuntimeError('Brain service already running')
    ledger=BrainLedger(runtime/'brain.db')
    registry=GovernorRegistry(runtime/'governor')
    children=[]
    trainer=None
    simulator=None
    collector=None
    buffers=defaultdict(lambda:deque(maxlen=256))
    positions={}
    gains=[]
    for e in ledger.conn.execute("SELECT kind,payload FROM brain_events WHERE kind IN ('PAPER_OPEN','PAPER_CLOSE') ORDER BY id"):
        value=json.loads(e['payload'])
        if e['kind']=='PAPER_OPEN':
            positions[value['coin_id']]=value
        else:
            positions.pop(value['coin_id'],None)
            gains.append((value['closed_at'],value['net_return_pct']))
    watermark=0
    watermark_row=ledger.conn.execute("SELECT payload FROM brain_events WHERE kind='MARKET_WATERMARK' ORDER BY id DESC LIMIT 1").fetchone()
    if watermark_row:
        watermark=json.loads(watermark_row[0])['id']
    models={}
    for registration in registry.conn.execute('SELECT model_hash,artifact,cohort_end FROM registrations').fetchall():
        if registration['cohort_end']+121>time.time() or any(p['model_hash']==registration['model_hash'] for p in positions.values()):
            restored=SequenceBrain.load(registry.registration(registration['model_hash'])['artifact'])
            models[registration['model_hash']]=(restored,NetCalibrator(restored.calibration.get('weights') if restored.calibration.get('fitted') else None))
    if (runtime/'market.db').exists() and watermark:
        with open_readonly(runtime/'market.db') as conn:
            for raw in reversed(conn.execute('SELECT * FROM snapshots WHERE id<=? ORDER BY id DESC LIMIT 5000',(watermark,)).fetchall()):
                obs=normalize_observation(raw,source_path=str(runtime/'market.db'))
                buffers[obs['coin_id']].append(obs)
            for coin,pos in positions.items():
                peak=conn.execute('SELECT MAX(price_usd) FROM snapshots WHERE address=? AND id<=? AND captured_at>=?',
                    (coin,watermark,datetime.fromtimestamp(pos['entry_ts'],timezone.utc).isoformat())).fetchone()[0]
                if peak:pos['peak_price']=max(pos['peak_price'],peak)
    decisions_at={}
    governor_ids={}
    start=time.monotonic()
    next_train=start+5
    next_simulation=start+5
    next_governor=start+60
    errors=[]
    try:
        if collect:
            collector=start_child(child_args(source,runtime,'collect'),runtime/'collector.log')
            children.append(collector)
        while not (runtime/'STOP').exists() and (not runtime_seconds or time.monotonic()-start<runtime_seconds):
            try:
                info,active_model,calibrator=load_active(runtime)
                models.setdefault(info['model_hash'],(active_model,calibrator))
                # Keep frozen challengers alive for their full preregistered
                # cohort; the learner only publishes another artifact.
                if registry.champion and registry.champion not in models:
                    r=registry.registration(registry.champion)
                    models[registry.champion]=(SequenceBrain.load(r['artifact']),NetCalibrator())
                if (runtime/'market.db').exists():
                    with open_readonly(runtime/'market.db') as conn:
                        rows=conn.execute('SELECT * FROM snapshots WHERE id>? ORDER BY id LIMIT 1000',(watermark,)).fetchall()
                    explorer={}
                    ep=source/'runtime'/'explorer-paper.db'
                    if ep.exists():
                        with open_readonly(ep) as conn:
                            explorer={r[0]:r[1] for r in conn.execute('SELECT address,MIN(id) FROM explorer_positions GROUP BY address')}
                    pending_by_coin=defaultdict(list)
                    for pending_row in ledger.pending():
                        pending_by_coin[pending_row['coin_id']].append(pending_row)
                    for raw in rows:
                        observation=normalize_observation(raw,source_path=str(runtime/'market.db'))
                        coin=observation['coin_id']
                        if buffers[coin] and observation['pair_address']!=buffers[coin][-1]['pair_address']:
                            if coin in positions:
                                event={**positions.pop(coin),'closed_at':observation['captured_at'],'net_return_pct':-100.0,'reason':'unpriceable_pair_change'}
                                ledger.event('PAPER_CLOSE',event);gains.append((event['closed_at'],-100.0))
                            buffers[coin].clear()
                        if buffers[coin] and observation['ts']<=buffers[coin][-1]['ts']:
                            watermark=raw['id'];continue
                        buffers[coin].append(observation)
                        watermark=raw['id']
                        if len(buffers[coin])<16:
                            continue
                        sequence=build_sequence(list(buffers[coin])[-16:])
                        age=time.time()-observation['ts']
                        if age>5:
                            observation={**observation,'market_data_stale':True}
                        portfolio_count=len(positions)
                        coin_already_open=coin in positions
                        for sha,(model,cal) in list(models.items()):
                            prediction=model.predict(sequence)
                            position=positions.get(coin)
                            if position and position['model_hash']==sha:
                                position['peak_price']=max(position['peak_price'],observation['price_usd'])
                                context={'position_gain':(observation['price_usd']/position['entry_price']-1)*100,
                                    'position_peak_gain':(position['peak_price']/position['entry_price']-1)*100,
                                    'position_giveback':(position['peak_price']-observation['price_usd'])/position['entry_price']*100,
                                    'position_elapsed_seconds':observation['ts']-position['entry_ts']}
                                exit_prediction=model.predict(sequence,position_context=context)
                                reason=exit_decision(exit_prediction,observation,position)
                                if reason:
                                    gross=(observation['price_usd']/position['entry_price']-1)*100
                                    # Unknown liquidity cannot provide an optimistic exit fill.
                                    reward=-100.0 if reason=='unpriceable_safety_exit' else net_return(gross,position['cost_pct'])
                                    event={**position,'closed_at':observation['captured_at'],'net_return_pct':reward,'reason':reason}
                                    ledger.event('PAPER_CLOSE',event)
                                    gains.append((observation['captured_at'],reward))
                                    positions.pop(coin,None)
                            if observation['ts']-decisions_at.get((coin,sha),-1e20)<30:
                                continue
                            daily=-min(0,sum(r*.0025 for t,r in gains if t[:10]==utc_now()[:10]))
                            execution_decision=policy_decision(prediction,observation,open_positions=portfolio_count,
                                exposure_pct=.25*portfolio_count,daily_loss_pct=daily,kill_switch=(runtime/'KILL_SWITCH').exists())
                            decision=research_policy_decision(prediction,observation,open_positions=portfolio_count,
                                exposure_pct=.25*portfolio_count,daily_loss_pct=daily,kill_switch=(runtime/'KILL_SWITCH').exists())
                            if coin_already_open and decision['action']=='TRADE':
                                decision={**decision,'action':'ABSTAIN','reasons':['existing_paper_position']}
                            cost=CostPolicy().estimate(observation['liquidity_usd'])
                            record={'coin_id':coin,'observed_at':observation['captured_at'],'source_domain':'REAL',
                                'explorer_id':explorer.get(coin),'observation':observation,'cost_pct':cost,
                                'sequence':sequence,'decision':decision,'execution_decision':execution_decision,
                                'research_action':decision['action'],'execution_action':execution_decision['action'],
                                'prediction_probability':cal.predict(prediction)}
                            did=ledger.decide(record,prediction,decision['action'],sha)
                            decisions_at[coin,sha]=observation['ts']
                            if decision['action']=='TRADE' and sha==info['model_hash']:
                                pos={'coin_id':coin,'model_hash':sha,'entry_price':observation['price_usd'],
                                     'peak_price':observation['price_usd'],'entry_ts':observation['ts'],
                                     'entry_liquidity':observation['liquidity_usd'],'cost_pct':cost,'notional_pct':.25,
                                     'research_only':True,'execution_authority_pct':0,
                                     'execution_action_at_entry':execution_decision['action']}
                                positions[coin]=pos
                                ledger.event('PAPER_OPEN',pos)
                            try:
                                gov=registry.record_decision(TradeEvidence(coin,observation['ts'],None,None,cost or 0,
                                    explorer_id=explorer.get(coin),specialist=decision['specialist'],
                                    regime='positive_momentum' if observation['price_change_5m_pct']>5 else 'flat',
                                    predicted_win_probability=cal.predict(prediction),model_hash=sha,source='REAL',action=decision['action']))
                                governor_ids[did]=gov
                                if sha==registry.champion:
                                    for cohort in list(models):
                                        if cohort!=sha:
                                            try:
                                                registry.record_decision(TradeEvidence(coin,observation['ts'],None,None,cost or 0,explorer_id=explorer.get(coin),specialist=decision['specialist'],regime='positive_momentum' if observation['price_change_5m_pct']>5 else 'flat',predicted_win_probability=cal.predict(prediction),model_hash=sha,source='REAL',action=decision['action']),cohort_hash=cohort)
                                            except ValueError:
                                                pass
                            except ValueError:
                                if sha==registry.champion:
                                    for cohort in list(models):
                                        if cohort!=sha:
                                            try:
                                                registry.record_decision(TradeEvidence(coin,observation['ts'],None,None,cost or 0,explorer_id=explorer.get(coin),specialist=decision['specialist'],regime='positive_momentum' if observation['price_change_5m_pct']>5 else 'flat',predicted_win_probability=cal.predict(prediction),model_hash=sha,source='REAL',action=decision['action']),cohort_hash=cohort)
                                            except ValueError:
                                                pass
                        # Settle all actions, including abstentions and vetoes.
                        for pending in pending_by_coin[coin]:
                            payload=json.loads(pending['payload'])['record']
                            anchor=payload['observation']
                            lag=observation['ts']-anchor['ts']-120
                            if lag<0:
                                continue
                            complete=lag<=1 and not observation['market_data_stale'] and payload['cost_pct'] is not None and observation['pair_address']==anchor['pair_address']
                            gross=(observation['price_usd']/anchor['price_usd']-1)*100
                            outcome={'gross_return_pct':gross if complete else None,
                                'net_return_pct':net_return(gross,payload['cost_pct']) if complete else None,
                                'endpoint_lag_seconds':lag,'reason':None if complete else 'missing_endpoint_or_cost'}
                            ledger.settle(pending['decision_id'],observation['captured_at'],outcome,status='COMPLETE' if complete else 'CENSORED')
                            if complete:
                                gov=governor_ids.get(pending['decision_id'])
                                if gov is None:
                                    gov=hashlib.sha256(canonical([pending['model_hash'],pending['model_hash'],coin]).encode()).hexdigest()
                                try:
                                    for ref in registry.conn.execute('SELECT decision_id FROM decisions WHERE model_hash=? AND coin=? AND decision_ts=?',(pending['model_hash'],coin,anchor['ts'])).fetchall():
                                        registry.settle_decision(ref[0],outcome_ts=observation['ts'],gross_return_pct=gross)
                                except ValueError:
                                    pass
                    if rows:
                        ledger.event('MARKET_WATERMARK',{'id':watermark})
                # Explicit censoring for a vanished coin; no silent survivor filter.
                for pending in ledger.pending():
                    payload=json.loads(pending['payload'])['record']
                    if time.time()-payload['observation']['ts']>150:
                        ledger.settle(pending['decision_id'],utc_now(),{'net_return_pct':None,'reason':'provider_or_coin_disappeared'},status='CENSORED')
                for coin,position in list(positions.items()):
                    last_seen=buffers[coin][-1]['ts'] if buffers[coin] else position['entry_ts']
                    if time.time()-last_seen>10:
                        event={**position,'closed_at':utc_now(),'net_return_pct':-100.0,'reason':'unpriceable_disappeared_coin'}
                        ledger.event('PAPER_CLOSE',event);gains.append((event['closed_at'],-100.0));positions.pop(coin,None)
                for coin in list(buffers):
                    if coin not in positions and buffers[coin] and time.time()-buffers[coin][-1]['ts']>1800:
                        del buffers[coin]
                # Restart failed workers without blocking inference.
                if collect and collector.poll() is not None and not (runtime/'STOP').exists():
                    ledger.event('COLLECTOR_RESTART',{'exit_code':collector.returncode})
                    collector=start_child(child_args(source,runtime,'collect'),runtime/'collector.log')
                    children.append(collector)
                if time.monotonic()>=next_train and (trainer is None or trainer.poll() is not None):
                    trainer=start_child(child_args(source,runtime,'learn-online'),runtime/'learning.log')
                    children.append(trainer)
                    next_train=time.monotonic()+max(60,training_interval_seconds)
                if collect and time.monotonic()>=next_simulation and (simulator is None or simulator.poll() is not None):
                    simulator=start_child(child_args(source,runtime,'simulate-research'),runtime/'simulation.log')
                    children.append(simulator);next_simulation=time.monotonic()+1800
                if time.monotonic()>=next_governor:
                    for sha in list(models):
                        result=registry.promote(sha)
                        save_json(runtime/('governor_'+sha[:12]+'.json'),result)
                    for sha in list(models):
                        if sha not in (info['model_hash'],registry.champion) and not any(p['model_hash']==sha for p in positions.values()) and registry.registration(sha)['cohort_end']+121<time.time():
                            del models[sha]
                    next_governor=time.monotonic()+60
                drift=[]
                for rows in buffers.values():
                    if len(rows)>=16:
                        seq=build_sequence(list(rows)[-16:])
                        z=np_abs_standardized(seq[-1],active_model)
                        drift.append(z)
                save_json(runtime/'service_status.json',{'version':VERSION,'at':utc_now(),'pid':os.getpid(),
                    'status':'RUNNING','collector_enabled':collect,'collector_pid':collector.pid if collector else None,
                    'learner_running':trainer is not None and trainer.poll() is None,'active_models':list(models),
                    'open_paper_positions':len(positions),'champion':registry.champion,'ledger':ledger.status(),
                    'drift_max_standardized_feature':max(drift,default=None),'execution_authority_pct':0,'recent_errors':errors[-3:]})
            except Exception as exc:
                errors.append(str(exc))
                errors=errors[-10:]
                save_json(runtime/'service_status.json',{'status':'ERROR_RETRY','at':utc_now(),'error':str(exc),'execution_authority_pct':0})
            time.sleep(1)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
        registry.close()
        ledger.close()
        if os.name=='nt':
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
        lock.close()
    status={'status':'STOPPED','at':utc_now(),'execution_authority_pct':0,'errors':errors[-3:]}
    save_json(runtime/'service_status.json',status)
    return status


def np_abs_standardized(vector, model):
    import numpy as np
    return float(np.max(np.abs((np.array(vector)-model.means)/np.maximum(model.stds,1e-8))))
