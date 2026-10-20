"""Linux lifecycle checks for the gateway shell entry point (stdlib only)."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
APP = Path(os.environ.get('APP_BIN', ROOT / '.venv/bin/hermes-http-gateway')).absolute()
PID_FILE = Path(os.environ.get('PID_FILE', ROOT / '.hermes-gateway.pid')).absolute()
LOG_FILE = Path(os.environ.get('LOG_FILE', ROOT / '.hermes-gateway.log')).absolute()
HOST = os.environ.get('HOST', '127.0.0.1')
PORT = int(os.environ.get('PORT', '8003'))
START_TIMEOUT = float(os.environ.get('START_TIMEOUT', '15'))
STOP_TIMEOUT = float(os.environ.get('STOP_TIMEOUT', '10'))


def alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except (OSError, IndexError):
        return False


def identity(pid):
    try:
        proc = Path(f'/proc/{pid}')
        args = proc.joinpath('cmdline').read_bytes().split(b'\0')
        return (proc.stat().st_uid == os.getuid()
                and proc.joinpath('cwd').resolve() == ROOT
                and os.fsencode(str(APP)) in args)
    except OSError:
        return False


def service_managed(pid):
    try:
        return any(line.rstrip().rsplit('/', 1)[-1].endswith('.service')
                   for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines())
    except OSError:
        return True


def listeners():
    # Conservatively treat any listener on this port as a conflict, including IPv6.
    inodes = set()
    for table in ('tcp', 'tcp6'):
        for line in Path(f'/proc/net/{table}').read_text().splitlines()[1:]:
            fields = line.split()
            if fields[3] == '0A' and int(fields[1].rsplit(':', 1)[1], 16) == PORT:
                inodes.add(fields[9])
    owners = set()
    if inodes:
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                for fd in proc.joinpath('fd').iterdir():
                    if os.readlink(fd) in {f'socket:[{i}]' for i in inodes}:
                        owners.add(int(proc.name))
                        break
            except OSError:
                continue
    return bool(inodes), owners


def conflict(owners):
    details = []
    for pid in sorted(owners):
        try:
            cmd = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
        except OSError:
            cmd = 'unreadable'
        details.append(f'pid {pid}: {cmd}')
    return f'port {PORT} is occupied; ' + ('; '.join(details) or 'owner unavailable (check ss -ltnp)')


def discover():
    pid = None
    if PID_FILE.exists():
        raw = PID_FILE.read_text().strip()
        if raw.isdecimal() and int(raw) > 1 and alive(int(raw)):
            pid = int(raw)
            if not identity(pid):
                raise RuntimeError(f'PID file points to an unrelated process: {pid}; refusing to signal it')
        else:
            PID_FILE.unlink()
            print('removed stale PID file')
    occupied, owners = listeners()
    if pid is None and occupied:
        if len(owners) != 1 or not identity(next(iter(owners))):
            raise RuntimeError(conflict(owners))
        pid = next(iter(owners))
        if service_managed(pid):
            raise RuntimeError(f'pid {pid} is service-managed; restart it through its service manager')
        PID_FILE.write_text(f'{pid}\n')
        print(f'recovered gateway PID from port {PORT}: {pid}')
    if pid is not None and service_managed(pid):
        raise RuntimeError(f'pid {pid} is service-managed; use its service manager')
    return pid


def healthy():
    host = '127.0.0.1' if HOST == '0.0.0.0' else '::1' if HOST == '::' else HOST
    host = f'[{host}]' if ':' in host else host
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://{host}:{PORT}/health', timeout=0.5) as response:
            return response.status == 200
    except Exception:
        return False


def stop(pid):
    if pid is None:
        print('hermes-gateway is not running')
        return
    if not identity(pid):
        raise RuntimeError('gateway identity changed; refusing to stop')
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if alive(pid):
        raise RuntimeError(f'stop timed out for pid {pid}; PID file retained; no SIGKILL sent')
    PID_FILE.unlink(missing_ok=True)
    occupied, owners = listeners()
    if occupied:
        raise RuntimeError('gateway exited but ' + conflict(owners))
    print('stopped hermes-gateway')


def start(pid):
    if pid is not None:
        occupied, owners = listeners()
        if occupied and owners == {pid} and healthy():
            print(f'hermes-gateway already running: {pid}')
            return
        raise RuntimeError(f'gateway pid {pid} exists but is not ready; use restart')
    occupied, owners = listeners()
    if occupied:
        raise RuntimeError(conflict(owners))
    if not os.access(APP, os.X_OK):
        raise RuntimeError(f'missing executable: {APP}; run uv sync first')
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open('ab') as log:
        offset = log.tell()
        process = subprocess.Popen([str(APP)], cwd=ROOT,
                                   env={**os.environ, 'HOST': HOST, 'PORT': str(PORT)},
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True, close_fds=True)
    PID_FILE.write_text(f'{process.pid}\n')
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        occupied, owners = listeners()
        if occupied and owners == {process.pid} and healthy() and process.poll() is None:
            print(f'started hermes-gateway on {HOST}:{PORT}\npid: {process.pid}\nlog: {LOG_FILE}')
            return
        time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f'start failed; pid {process.pid} did not stop; PID file retained')
    PID_FILE.unlink(missing_ok=True)
    with LOG_FILE.open('rb') as log:
        log.seek(max(offset, LOG_FILE.stat().st_size - 8000))
        tail = log.read(8000).decode(errors='replace')
    raise RuntimeError(f'gateway failed readiness checks (exit={process.returncode})\n{tail}')


def main():
    action = sys.argv[1] if len(sys.argv) == 2 else ''
    if action not in ('start', 'stop', 'restart', 'status', 'logs'):
        raise RuntimeError('Usage: scripts/hermes-gateway.sh {start|stop|restart|status|logs}')
    if not 0 < PORT < 65536 or START_TIMEOUT <= 0 or STOP_TIMEOUT <= 0:
        raise RuntimeError('invalid port or timeout')
    if action == 'logs':
        LOG_FILE.touch(exist_ok=True)
        os.execvp('tail', ['tail', '-f', str(LOG_FILE)])
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(PID_FILE) + '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another gateway operation is in progress')
        pid = discover()
        if action == 'status':
            occupied, owners = listeners()
            if pid is not None and occupied and owners == {pid} and healthy():
                print(f'running: pid {pid}\nlog: {LOG_FILE}')
            else:
                raise RuntimeError('not running' if pid is None else f'pid {pid} exists but is not ready')
        elif action == 'stop':
            stop(pid)
        elif action == 'restart':
            stop(pid)
            start(None)
        else:
            start(pid)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
