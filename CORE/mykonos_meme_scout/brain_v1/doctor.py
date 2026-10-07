"""Read-only release, DB and execution-authority verification."""
import hashlib
import json
from pathlib import Path
from ..config import Config
from . import VERSION
from .data import open_readonly
from .safety import assert_zero_authority


def doctor(source_root,runtime):
    package_root=Path(__file__).resolve().parents[2]
    source_root=Path(source_root).resolve();runtime=Path(runtime).resolve()
    checks={}
    checks['default_live_authority_zero']=not Config().live_trading_enabled
    try:
        Config(live_trading_enabled=True)
        checks['live_enable_rejected']=False
    except ValueError:
        checks['live_enable_rejected']=True
    manifest=json.loads((package_root/'execution_freeze_v1.0.json').read_text(encoding='utf-8'))
    mismatches=[p for p,h in manifest['sha256'].items() if hashlib.sha256((package_root/p).read_bytes()).hexdigest().upper()!=h]
    checks['legacy_execution_unchanged']=not mismatches
    databases={}
    for name in ('data/autonomous/mykonos-main.db','data/autonomous/temporal-training-pool.db','runtime/explorer-paper.db'):
        path=source_root/name
        if not path.exists():
            databases[name]={'status':'not_present_in_this_code_checkout'}
            continue
        try:
            with open_readonly(path,snapshot=(source_root/'BACKUP_MANIFEST.json').exists()) as conn:
                databases[name]={'quick_check':[r[0] for r in conn.execute('PRAGMA quick_check')]}
                checks['db:'+name]=databases[name]['quick_check']==['ok']
                if name.endswith('explorer-paper.db'):
                    databases[name]['max_id']=conn.execute('SELECT MAX(id) FROM explorer_positions').fetchone()[0]
        except Exception as exc:
            databases[name]={'error':str(exc)};checks['db:'+name]=False
    for path in (runtime/'brain.db',runtime/'governor'/'registry.db'):
        if path.exists():
            try:
                with open_readonly(path) as conn:
                    checks['db:'+path.name]=[r[0] for r in conn.execute('PRAGMA quick_check')]==['ok']
                    violations=[]
                    for table in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                        name=table[0]
                        cols={r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
                        for col in cols&{'authority_pct','execution_authority_pct'}:
                            if conn.execute(f'SELECT COUNT(*) FROM "{name}" WHERE "{col}"!=0').fetchone()[0]:violations.append(name)
                    checks['authority:'+path.name]=not violations
            except Exception as exc:
                checks['db:'+path.name]=False
    if (runtime/'active_challenger.json').exists():
        try:
            from .runtime import load_active
            info,model,cal=load_active(runtime)
            assert_zero_authority(model.as_dict())
            checks['frozen_model_load_and_authority']=True
        except Exception:
            checks['frozen_model_load_and_authority']=False
    else:
        checks['frozen_model_load_and_authority']=False
    return {'version':VERSION,'ok':all(checks.values()),'checks':checks,'databases':databases,
            'execution_mismatches':mismatches,'execution_authority_pct':0}
