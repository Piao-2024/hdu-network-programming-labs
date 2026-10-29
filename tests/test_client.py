import contextlib
import io
import socket
import unittest
from unittest.mock import patch

from protocol import encode_message
from remote_bet_client import render_result, run_client
from test_protocol import WELCOME, OPEN, RESULT


class ClientTests(unittest.TestCase):
    def play(self, messages, commands):
        client, peer = socket.socketpair()
        self.addCleanup(peer.close)
        peer.sendall(b''.join(encode_message(m) for m in messages))
        peer.shutdown(socket.SHUT_WR)
        out, err = io.StringIO(), io.StringIO()
        with patch('remote_bet_client.socket.create_connection', return_value=client), \
             patch('builtins.input', side_effect=commands), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = run_client('127.0.0.1', 5000)
        peer.settimeout(1)
        sent = b''
        while data := peer.recv(4096):
            sent += data
        return status, out.getvalue(), err.getvalue(), sent

    def test_render_all_six_faces_and_authoritative_money(self):
        for face in range(1, 7):
            output = render_result(RESULT | {'actual_dice': [face, face]})
            self.assertEqual(sum(line.count('o') for line in output.splitlines() if line.startswith('|')), face * 2)
            self.assertIn('+350 coin', output)
            self.assertIn('360 coin', output)
            self.assertIn(f'({face}, {face})', output)
        # Presentation must use authoritative amounts, not recompute a bet.
        self.assertIn('+123 coin', render_result(RESULT | {'net_change': 123}))

    def test_result_new_round_and_quit(self):
        bye = dict(version=1, type='bye', request_id=2, message='再见')
        status, output, errors, sent = self.play(
            [WELCOME, OPEN, RESULT, OPEN | {'round_id': 2}, bye], ['ya tc 10 coin', 'quit'])
        self.assertEqual((status, errors), (0, ''))
        self.assertIn('+350 coin', output)
        self.assertIn('再见', output)
        self.assertIn(b'"type":"bet"', sent)
        self.assertIn(b'"type":"quit","request_id":2', sent)

    def test_business_error_keeps_open_and_blank_input_is_local(self):
        error = dict(version=1, type='error', request_id=1, round_id=1,
                     code='INVALID_AMOUNT', fatal=False, message='数量非法')
        bye = dict(version=1, type='bye', request_id=2, message='再见')
        status, output, errors, sent = self.play([WELCOME, OPEN, error, bye],
                                                ['', 'ya tc 0 coin', 'quit'])
        self.assertEqual(status, 0)
        self.assertIn('INVALID_AMOUNT', output)
        self.assertEqual(sent.count(b'"type":"bet"'), 1)

    def test_eof_and_interrupt_at_prompt_quit_cleanly(self):
        for interrupt in (EOFError, KeyboardInterrupt):
            status, _, _, sent = self.play([WELCOME, OPEN,
                dict(version=1, type='bye', request_id=1, message='再见')], [interrupt])
            self.assertEqual(status, 0)
            self.assertIn(b'"type":"quit"', sent)

    def test_wrong_rule_sequence_or_correlation_is_rejected(self):
        for messages, commands in [([WELCOME | {'ruleset': 'other'}], []),
                                   ([OPEN], []),
                                   ([WELCOME, OPEN, RESULT | {'request_id': 2}], ['ya tc 10 coin']),
                                   ([WELCOME, OPEN, RESULT, OPEN | {'round_id': 3}], ['ya tc 10 coin'])]:
            status, _, errors, _ = self.play(messages, commands)
            self.assertEqual(status, 1)
            self.assertIn('协议', errors)

    def test_fatal_null_id_at_any_receive_stage_is_shown(self):
        fatal = dict(version=1, type='error', request_id=None, round_id=None,
                     code='INTERNAL_ERROR', fatal=True, message='诊断标记')
        for prefix, commands in [([], []), ([WELCOME, OPEN], ['ya tc 10 coin']),
                                 ([WELCOME, OPEN, RESULT], ['ya tc 10 coin'])]:
            status, _, errors, _ = self.play(prefix + [fatal], commands)
            self.assertEqual(status, 1)
            self.assertIn('诊断标记', errors)

    def test_lost_result_is_unknown_and_never_retried(self):
        status, _, errors, sent = self.play([WELCOME, OPEN], ['ya tc 10 coin'])
        self.assertEqual(status, 1)
        self.assertIn('结果可能未知', errors)
        self.assertEqual(sent.count(b'"type":"bet"'), 1)

    def test_connection_error_is_readable(self):
        errors = io.StringIO()
        with patch('remote_bet_client.socket.create_connection', side_effect=ConnectionRefusedError('refused')), \
             contextlib.redirect_stderr(errors):
            self.assertEqual(run_client('127.0.0.1', 5000), 1)
        self.assertIn('refused', errors.getvalue())

    def test_unsent_oversized_input_is_not_an_unknown_result(self):
        status, _, errors, sent = self.play([WELCOME, OPEN], ['x' * 9000])
        self.assertEqual(status, 1)
        self.assertIn('FRAME_TOO_LARGE', errors)
        self.assertNotIn('结果可能未知', errors)
        self.assertEqual(sent, b'')
