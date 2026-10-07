"""One owned SDK child; private preflight pipe, no retry and no raw diagnostics."""
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

from execution_state import StateError, canonical
from execution_adapter import unique_pairs

ROOT = Path(__file__).resolve().parents[3]
HOST = ROOT / 'TOOLS/GMGN-ExecutionHost.mjs'
SDK = Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/gmgn-cli/dist'


class SDKContractTransport:
    def __init__(self, gate_path, *, host=HOST, sdk=SDK):
        self.gate_path, self.host, self.sdk = map(Path, (gate_path, host, sdk))
        self.node = shutil.which('node')
        self._lock = threading.Lock()
        self.last_send_started = False
        self._unconfirmed_child = self._unconfirmed_window = None

    def available(self):
        return bool(self.node and self.host.is_file() and
                    (self.sdk / 'client/OpenApiClient.js').is_file() and (self.sdk / 'config.js').is_file())

    def send_with_guard(self, contract, *, send_guard):
        if not self._lock.acquire(blocking=False):
            raise StateError('execution_transport_busy')
        process = reader = window = None
        self.last_send_started = False
        def close_window():
            nonlocal window
            active, window = window, None
            if active is not None:
                active.__exit__(None, None, None)
        try:
            fields = {'eventId', 'requestId', 'tradeId', 'payloadHash', 'payloadJson',
                      'issuedAtMs', 'expiresAtMs', 'responseUntilMs'}
            if set(contract) != fields or not self.node:
                raise StateError('execution_host_contract_invalid')
            body = canonical(dict(contract))
            remaining = (contract['responseUntilMs'] / 1000) - time.time()
            if len(body.encode()) > 65536 or not 0 < remaining <= 30:
                raise StateError('execution_host_deadline_invalid')
            deadline = time.monotonic() + remaining
            environment = dict(os.environ)
            environment.pop('GMGN_ALLOW_AUTOMATED_TRADES', None)
            environment.pop('GMGN_DEBUG', None)
            process = subprocess.Popen([self.node, str(self.host), str(self.gate_path), str(self.sdk)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            messages = queue.Queue(maxsize=5)
            def receive():
                try:
                    for _ in range(5):
                        line = process.stdout.readline(65538)
                        if not line:
                            messages.put_nowait(None)
                            return
                        if len(line.encode()) > 65536 or not line.endswith('\n'):
                            messages.put_nowait(None)
                            return
                        messages.put_nowait(line.rstrip('\r\n'))
                except (OSError, UnicodeError, queue.Full):
                    try: messages.put_nowait(None)
                    except queue.Full: pass
            reader = threading.Thread(target=receive, name='imc-sdk-reply', daemon=True)
            reader.start()
            process.stdin.write(body + '\n')
            process.stdin.flush()
            phase = 'CONFIG'
            while True:
                timeout = max(0, deadline-time.monotonic())
                if phase in ('SEND', 'STARTED'):
                    timeout = min(timeout, max(0, contract['expiresAtMs']/1000-time.time()))
                try: line = messages.get(timeout=timeout)
                except queue.Empty: raise StateError('execution_host_timeout') from None
                if line is None:
                    raise StateError('execution_host_eof')
                if line == phase and phase in ('CONFIG', 'SEND'):
                    window = send_guard()
                    event = window.__enter__()
                    permission = 'ALLOW_EXIT' if (isinstance(event, dict) and event.get('side') == 'SELL'
                        and event.get('close_only_authorized') is True) else 'ALLOW'
                    process.stdin.write(permission + '\n')
                    process.stdin.flush()
                    if phase == 'CONFIG':
                        close_window()
                        phase = 'SEND'
                    else:
                        phase = 'STARTED'
                    continue
                if line == 'STARTED' and phase == 'STARTED':
                    self.last_send_started = True
                    close_window()
                    phase = 'RESULT'
                    continue
                if not line.startswith('{'):
                    raise StateError('execution_host_protocol_invalid')
                result = json.loads(line, object_pairs_hook=unique_pairs)
                if not isinstance(result, dict) or result.get('state') == 'ACKNOWLEDGED' and phase != 'RESULT':
                    raise StateError('execution_host_protocol_invalid')
                process.stdin.close()
                process.wait(timeout=max(.01, deadline-time.monotonic()))
                if messages.get(timeout=max(.01, deadline-time.monotonic())) is not None:
                    raise StateError('execution_host_protocol_invalid')
                if process.returncode and result.get('state') == 'ACKNOWLEDGED':
                    raise StateError('execution_host_exit_unknown')
                return result
        finally:
            stopped = process is None
            try:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=2)
                    stopped = True
            except (OSError, subprocess.TimeoutExpired):
                # Fail-stop: do not release the risk window while this exact
                # child might still consume ALLOW. Retain both handles for
                # explicit recovery; this transport cannot accept another call.
                self._unconfirmed_child, self._unconfirmed_window = process, window
                window = None
                raise StateError('execution_host_stop_unconfirmed') from None
            finally:
                if stopped:
                    try:
                        close_window()
                    finally:
                        try:
                            if process is not None:
                                for stream in (process.stdin, process.stdout):
                                    if stream and not stream.closed: stream.close()
                            if reader is not None: reader.join(timeout=1)
                        finally:
                            self._lock.release()
