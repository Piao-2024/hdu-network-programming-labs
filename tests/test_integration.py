"""Real localhost TCP and subprocess tests. No third-party dependencies."""
import contextlib
import io
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from protocol import ReaderState, encode_message, read_message, send_message
from remote_bet_client import run_client
from remote_bet_server import main as server_main

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        # The child owns the ephemeral binding for its entire lifetime, no port race.
        code = "from remote_bet_server import create_listener, run_server; run_server(create_listener('127.0.0.1', 0))"
        self.server = subprocess.Popen([sys.executable, '-B', '-u', '-c', code],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_server)
        ready, _, _ = select.select([self.server.stdout], [], [], 5)
        self.assertTrue(ready, 'server readiness timed out')
        line = self.server.stdout.readline()
        self.assertIn('listening on 127.0.0.1:', line)
        self.port = int(line.rsplit(':', 1)[1])

    def stop_server(self):
        if self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=3)
        self.server.stdout.close()
        self.server.stderr.close()

    def connect(self):
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=2)
        self.addCleanup(sock.close)
        return sock, ReaderState()

    def receive(self, sock, state):
        return read_message(sock, state, time.monotonic() + 2)

    def open(self, sock, state):
        self.assertEqual(self.receive(sock, state)['type'], 'welcome')
        return self.receive(sock, state)

    def test_real_tcp_100_rounds_errors_latency_and_new_session(self):
        sock, state = self.connect()
        current = self.open(sock, state)
        durations = []
        request_id = 0
        for index in range(100):
            request_id += 1
            send_message(sock, dict(version=1, type='bet', request_id=request_id,
                round_id=current['round_id'], command='ya tc 0 coin'))
            error = self.receive(sock, state)
            self.assertEqual(error['code'], 'INVALID_AMOUNT')
            self.assertEqual(error['round_id'], current['round_id'])
            request_id += 1
            command = f'ya {("tc", "dc", "kp", "qx", "dd", "sx")[index % 6]} 10 {("coin", "silver", "gold")[index % 3]}'
            before = time.monotonic()
            send_message(sock, dict(version=1, type='bet', request_id=request_id,
                round_id=current['round_id'], command=command))
            result = self.receive(sock, state)
            following = self.receive(sock, state)
            durations.append(time.monotonic() - before)
            self.assertEqual((result['type'], result['request_id'], result['round_id']),
                             ('round_result', request_id, index + 1))
            self.assertEqual(result['predicted_dice'], current['predicted_dice'])
            self.assertEqual(result['net_change'], result['payout'] - result['amount'])
            self.assertEqual(following['round_id'], index + 2)
            current = following
        self.assertLess(max(durations), 1, f'latency max={max(durations):.6f}s')
        print(f'100-round TCP latency: max={max(durations):.6f}s mean={sum(durations)/100:.6f}s')
        send_message(sock, dict(version=1, type='quit', request_id=request_id + 1))
        self.assertEqual(self.receive(sock, state)['type'], 'bye')
        sock.close()
        other, other_state = self.connect()
        self.assertEqual(self.open(other, other_state)['round_id'], 1)

    def test_two_process_cli_multiround_error_and_exit(self):
        result = subprocess.run([sys.executable, '-B', 'remote_bet_client.py', '127.0.0.1',
            '--port', str(self.port)], input='\nya tc 0 coin\nya tc 10 coin\nya qx 2 silver\nquit\n',
            text=True, capture_output=True, cwd=ROOT, timeout=10)
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        for expected in ('欢迎', 'INVALID_AMOUNT', '实际骰子', '净变化', '已退出', '第 3 盘'):
            self.assertIn(expected, result.stdout)
        self.assertEqual(result.stdout.count('实际骰子'), 2)

    def test_cli_interactive_endpoint_connects_and_plays(self):
        result = subprocess.run([sys.executable, '-B', 'remote_bet_client.py'],
            input=f'bad-address\n127.0.0.1:{self.port}\nya qx 10 coin\nquit\n',
            text=True, capture_output=True, cwd=ROOT, timeout=10)
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertIn('IP:端口', result.stdout)
        self.assertIn('地址格式', result.stdout)
        self.assertIn('实际骰子', result.stdout)
        self.assertIn('已退出', result.stdout)

    def test_real_tcp_bytewise_and_coalesced_requests(self):
        sock, state = self.connect()
        self.open(sock, state)
        frame = encode_message(dict(version=1, type='bet', request_id=1, round_id=1,
                                    command='ya tc 1 coin'))
        for byte in frame:
            sock.sendall(bytes([byte]))
        self.assertEqual(self.receive(sock, state)['type'], 'round_result')
        self.assertEqual(self.receive(sock, state)['round_id'], 2)
        sock.sendall(encode_message(dict(version=1, type='bet', request_id=2, round_id=1,
                                        command='ya tc 1 coin')) +
                    encode_message(dict(version=1, type='quit', request_id=3)))
        self.assertEqual(self.receive(sock, state)['code'], 'ROUND_MISMATCH')
        self.assertEqual(self.receive(sock, state)['type'], 'bye')

    def test_bad_session_does_not_break_accept_loop_and_second_client_waits(self):
        sock, state = self.connect()
        self.open(sock, state)
        second, second_state = self.connect()
        self.assertFalse(select.select([second], [], [], .05)[0])
        sock.sendall(b'\xff\n')
        self.assertEqual(self.receive(sock, state)['code'], 'INVALID_ENCODING')
        self.assertEqual(sock.recv(1), b'')
        self.assertEqual(self.open(second, second_state)['round_id'], 1)

    def test_occupied_port_and_cli_port_validation(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(server_main(['--port', str(self.port)]), 1)
        for script in ('remote_bet_client.py', 'remote_bet_server.py'):
            for port in ('0', '65536', 'abc'):
                result = subprocess.run([sys.executable, '-B', script, '--port', port],
                    text=True, capture_output=True, cwd=ROOT, timeout=3)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn('Traceback', result.stderr)

    def test_disconnect_and_signal_shutdown_are_readable(self):
        sock, state = self.connect()
        self.open(sock, state)
        self.server.send_signal(signal.SIGINT)
        self.server.wait(timeout=3)
        self.assertEqual(sock.recv(1), b'')
        # Connection refused against the now closed actual server port.
        result = subprocess.run([sys.executable, '-B', 'remote_bet_client.py', '127.0.0.1',
            '--port', str(self.port)], input='quit\n', text=True, capture_output=True,
            cwd=ROOT, timeout=7)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('Traceback', result.stderr)
