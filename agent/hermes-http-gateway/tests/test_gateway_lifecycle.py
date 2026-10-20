"""Exercise the actual launcher against disposable local HTTP services."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts/hermes-gateway.sh'


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = self.root / 'fake-gateway'
        self.app.write_text('''#!/usr/bin/env python3
import http.server, os, signal, time
if os.environ.get('TEST_EXIT'): raise SystemExit(7)
if os.environ.get('TEST_DELAY'): time.sleep(1)
if os.environ.get('TEST_IGNORE_TERM'): signal.signal(signal.SIGTERM, signal.SIG_IGN)
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
http.server.HTTPServer((os.environ['HOST'], int(os.environ['PORT'])), Handler).serve_forever()
''')
        self.app.chmod(0o755)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.pidfile = self.root / 'gateway.pid'
        self.env = {**os.environ, 'APP_BIN': str(self.app), 'PID_FILE': str(self.pidfile),
                    'LOG_FILE': str(self.root / 'gateway.log'), 'HOST': '127.0.0.1',
                    'PORT': str(port), 'START_TIMEOUT': '4', 'STOP_TIMEOUT': '0.5'}
        self.addCleanup(self.cleanup_process)

    def cleanup_process(self):
        if self.pidfile.exists():
            try:
                pid = int(self.pidfile.read_text())
                if str(self.app).encode() in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'):
                    os.kill(pid, signal.SIGKILL)
            except (OSError, ValueError):
                pass

    def run_script(self, action, ok=True, **env):
        result = subprocess.run([str(SCRIPT), action], env={**self.env, **env},
                                text=True, capture_output=True, timeout=12)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def test_start_restart_status_stop_and_pid_recovery(self):
        self.run_script('start')
        first = self.pidfile.read_text()
        self.run_script('status')
        self.run_script('start')
        self.assertEqual(self.pidfile.read_text(), first)
        self.pidfile.unlink()
        self.assertIn('recovered', self.run_script('restart').stdout)
        self.assertNotEqual(self.pidfile.read_text(), first)
        self.run_script('stop')
        self.assertFalse(self.pidfile.exists())
        self.run_script('status', ok=False)

    def test_stale_pid_and_immediate_exit(self):
        self.pidfile.write_text('invalid\n')
        result = self.run_script('start', ok=False, TEST_EXIT='1')
        self.assertIn('failed readiness', result.stderr)
        self.assertNotIn('started hermes-gateway', result.stdout)
        self.assertFalse(self.pidfile.exists())

    def test_unrelated_pid_is_never_signalled(self):
        self.pidfile.write_text(str(os.getpid()))
        self.assertIn('unrelated', self.run_script('restart', ok=False).stderr)

    def test_port_conflict(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', int(self.env['PORT'])))
            sock.listen()
            self.assertIn('occupied', self.run_script('start', ok=False).stderr)
        self.assertFalse(self.pidfile.exists())

    def test_stop_timeout_retains_pid(self):
        self.run_script('start', TEST_IGNORE_TERM='1')
        pid = self.pidfile.read_text()
        self.assertIn('stop timed out', self.run_script('restart', ok=False).stderr)
        self.assertEqual(self.pidfile.read_text(), pid)

    def test_start_timeout_cleans_process(self):
        self.run_script('start', ok=False, START_TIMEOUT='0.2', TEST_DELAY='1')
        self.assertFalse(self.pidfile.exists())

    def test_concurrent_operations_are_rejected(self):
        process = subprocess.Popen([str(SCRIPT), 'start'], env={**self.env, 'TEST_DELAY': '1'},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 3
            while not self.pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(self.pidfile.exists())
            self.assertIn('operation is in progress', self.run_script('start', ok=False).stderr)
            out, err = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 0, out + err)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
