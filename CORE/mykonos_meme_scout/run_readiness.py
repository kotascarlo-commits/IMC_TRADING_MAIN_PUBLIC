from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Callable

from .realtime_temporal import EXECUTION_AUTHORITY_PCT, RealTemporalStore
from .temporal_intelligence import TemporalShadowBundle


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def run_preflight(project_root: Path, *, database: Path, model: Path,
                  network_probe: Callable[[], dict] | None = None) -> dict:
    project_root = Path(project_root).resolve()
    database = Path(database).resolve()
    model = Path(model).resolve()
    checks: dict[str, dict] = {}

    checks["execution_authority"] = {"ok": EXECUTION_AUTHORITY_PCT == 0, "value_pct": EXECUTION_AUTHORITY_PCT}
    checks["model_exists"] = {"ok": model.is_file(), "path": str(model)}
    if model.is_file():
        try:
            bundle = TemporalShadowBundle.load(model)
            raw = bundle.as_dict()
            checks["model_load"] = {
                "ok": bool(raw.get("shadow_only") and not raw.get("runtime_integration_enabled") and not raw.get("eligible_for_real_champion") and float(raw.get("execution_authority_pct", 0)) == 0.0),
                "shadow_only": bool(raw.get("shadow_only")),
                "runtime_integration_enabled": bool(raw.get("runtime_integration_enabled")),
                "eligible_for_real_champion": bool(raw.get("eligible_for_real_champion")),
                "sha256": file_sha256(model),
            }
        except Exception as exc:
            checks["model_load"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    else:
        checks["model_load"] = {"ok": False, "error": "model_missing"}

    try:
        store = RealTemporalStore(database)
        try:
            status = store.status()
            checks["database"] = {
                "ok": status.get("execution_authority_pct") == 0 and not status.get("runtime_decision_input"),
                "path": str(database), "status": status,
            }
        finally:
            store.close()
        conn = sqlite3.connect(database)
        try:
            integrity = [r[0] for r in conn.execute("PRAGMA quick_check").fetchall()]
        finally:
            conn.close()
        checks["database_integrity"] = {"ok": integrity == ["ok"], "result": integrity}
    except Exception as exc:
        checks["database"] = {"ok": False, "path": str(database), "error": f"{type(exc).__name__}: {exc}"}
        checks["database_integrity"] = {"ok": False}

    freeze_manifest = project_root / "execution_freeze_v1.0.json"
    if not freeze_manifest.is_file():
        freeze_manifest = project_root / "execution_freeze_v0.4.1.json"
    try:
        import json
        manifest = json.loads(freeze_manifest.read_text(encoding="utf-8"))
        mismatches = [p for p, expected in manifest["sha256"].items() if file_sha256(project_root / p) != expected]
        checks["execution_freeze_manifest"] = {"ok": not mismatches, "path": str(freeze_manifest), "mismatches": mismatches}
    except Exception as exc:
        checks["execution_freeze_manifest"] = {"ok": False, "error": str(exc)}

    if network_probe is not None:
        try:
            result = network_probe()
            checks["network_discovery"] = {"ok": bool(result.get("ok")), **result}
        except Exception as exc:
            checks["network_discovery"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    required = ["execution_authority", "model_exists", "model_load", "database", "database_integrity", "execution_freeze_manifest"]
    if network_probe is not None:
        required.append("network_discovery")
    ok = all(bool(checks[name].get("ok")) for name in required)
    return {"version": "0.8.14", "ready": ok, "checks": checks}
