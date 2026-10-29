"""Smoke test for the actual uv launch commands and default port.

The network suite uses temporary ports; this one explicitly checks the documented
5000 default, and skips if another local application already owns that port.
"""
from pathlib import Path
import select
import shutil
import signal
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UvEntryTests(unittest.TestCase):
    def test_uv_default_server_and_client_commands(self):
        uv = shutil.which('uv')
        interpreter = Path('/usr/local/bin/python3.13')
        if not uv or not interpreter.exists():
            self.skipTest('documented local uv/Python path unavailable on this machine')
        command = [uv, 'run', '--no-project', '--no-managed-python', '--no-python-downloads',
                   '--python', str(interpreter), 'python', '-B']
        server = subprocess.Popen(command + ['remote_bet_server.py'], cwd=ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            ready, _, _ = select.select([server.stdout], [], [], 5)
            self.assertTrue(ready, 'default server launch timed out')
            line = server.stdout.readline()
            if not line:
                server.wait(timeout=2)
                errors = server.stderr.read()
                if 'Address already in use' in errors:
                    self.skipTest('port 5000 belongs to another local application')
                self.fail(errors)
            self.assertIn('listening on 127.0.0.1:5000', line)
            client = subprocess.run(command + ['remote_bet_client.py', '127.0.0.1'],
                cwd=ROOT, input='ya tc 10 coin\nquit\n', capture_output=True,
                text=True, timeout=10)
            self.assertEqual((client.returncode, client.stderr), (0, ''))
            self.assertIn('实际骰子', client.stdout)
            self.assertIn('已退出', client.stdout)
            server.send_signal(signal.SIGINT)
            server.wait(timeout=3)
            self.assertEqual(server.returncode, 0)
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=3)
            server.stdout.close()
            server.stderr.close()
