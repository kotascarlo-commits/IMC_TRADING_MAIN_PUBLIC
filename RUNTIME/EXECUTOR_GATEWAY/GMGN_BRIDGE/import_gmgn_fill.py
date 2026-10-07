"""Import executor feedback; the gateway independently verifies chain fills."""
import argparse
import json
import os
import urllib.request
from pathlib import Path

from execution_state import StateError, load_config

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fill', type=Path, default=HERE / 'fills/latest_fill.json')
    args = parser.parse_args()
    cfg = load_config()
    token = os.environ.get('IMC_EXECUTION_TOKEN')
    if not token:
        raise SystemExit('IMC_EXECUTION_TOKEN_REQUIRED')
    if args.fill.stat().st_size > cfg['max_http_body_bytes']:
        raise SystemExit('FILL_TOO_LARGE')
    data = json.loads(args.fill.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or any(not isinstance(data.get(k), str) or not data[k]
                                       for k in ('feedback_id', 'event_id', 'trade_id', 'mint', 'side', 'status')):
        raise SystemExit('BAD_FILL_IDENTITY')
    req = urllib.request.Request(f"http://{cfg['host']}:{cfg['port']}/ack",
        data=json.dumps(data, allow_nan=False).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}, method='POST')
    with urllib.request.urlopen(req, timeout=cfg['http_timeout_seconds']) as response:
        body = json.loads(response.read(cfg['max_http_body_bytes']))
    print(json.dumps({'state': 'IMC_FEEDBACK_IMPORTED', 'response': body}))


if __name__ == '__main__':
    main()
