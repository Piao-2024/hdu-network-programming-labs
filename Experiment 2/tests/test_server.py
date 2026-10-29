import socket
import threading
import time
import unittest

from protocol import ReaderState, encode_message, read_message, send_message
from remote_bet_server import serve_session


class ServerTests(unittest.TestCase):
    def start_session(self, values=None):
        client, server = socket.socketpair()
        client.settimeout(2)
        self.addCleanup(client.close)
        sequence = iter(values) if values is not None else None
        draw = (lambda low, high: next(sequence)) if sequence else (lambda low, high: 6)
        thread = threading.Thread(target=serve_session, args=(server,), kwargs={'draw_int': draw}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(client.close)
        return client, ReaderState(), thread

    def receive(self, sock, state):
        return read_message(sock, state, time.monotonic() + 2)

    def opened(self, sock, state):
        self.assertEqual(self.receive(sock, state)['type'], 'welcome')
        opened = self.receive(sock, state)
        self.assertEqual((opened['type'], opened['round_id']), ('round_open', 1))
        return opened

    def bet(self, request=1, round_id=1, command='ya tc 10 coin'):
        return dict(version=1, type='bet', request_id=request, round_id=round_id, command=command)

    def test_authoritative_result_then_next_round_and_quit(self):
        sock, state, thread = self.start_session([6, 1, 6, 1, 5, 2])
        self.assertEqual(self.opened(sock, state)['predicted_dice'], [6, 1])
        send_message(sock, self.bet())
        result = self.receive(sock, state)
        self.assertEqual((result['type'], result['request_id'], result['actual_dice'],
                          result['payout'], result['net_change']), ('round_result', 1, [6, 1], 360, 350))
        new_round = self.receive(sock, state)
        self.assertEqual((new_round['round_id'], new_round['predicted_dice']), (2, [5, 2]))
        send_message(sock, dict(version=1, type='quit', request_id=2))
        self.assertEqual(self.receive(sock, state)['type'], 'bye')
        self.assertEqual(sock.recv(1), b'')
        thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_errors_keep_round_and_consume_request_id_without_drawing(self):
        sock, state, _ = self.start_session([6, 1, 6, 1, 5, 2])
        self.opened(sock, state)
        for message, code in [(self.bet(command='ya tc 0 coin'), 'INVALID_AMOUNT'),
                              (self.bet(), 'DUPLICATE_REQUEST'),
                              (self.bet(2, 2), 'ROUND_MISMATCH'),
                              (self.bet(3, command='x' * 257), 'INVALID_COMMAND')]:
            send_message(sock, message)
            result = self.receive(sock, state)
            self.assertEqual((result['type'], result['code'], result['fatal'], result['round_id']),
                             ('error', code, False, 1))
        send_message(sock, self.bet(4))
        self.assertEqual(self.receive(sock, state)['actual_dice'], [6, 1])
        self.assertEqual(self.receive(sock, state)['predicted_dice'], [5, 2])

    def test_two_coalesced_bets_do_not_settle_same_round_twice(self):
        sock, state, _ = self.start_session()
        self.opened(sock, state)
        sock.sendall(encode_message(self.bet()) + encode_message(self.bet(2)))
        self.assertEqual(self.receive(sock, state)['type'], 'round_result')
        self.assertEqual(self.receive(sock, state)['type'], 'round_open')
        self.assertEqual(self.receive(sock, state)['code'], 'ROUND_MISMATCH')

    def test_bad_encoding_and_wrong_direction_close_only_this_session(self):
        for data, code in [(b'\xff\n', 'INVALID_ENCODING'),
                           (encode_message(dict(version=1, type='bye', request_id=1, message='x')),
                            'UNEXPECTED_MESSAGE')]:
            sock, state, thread = self.start_session()
            self.opened(sock, state)
            sock.sendall(data)
            result = self.receive(sock, state)
            self.assertEqual((result['code'], result['fatal']), (code, True))
            self.assertEqual(sock.recv(1), b'')
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_disconnect_midframe_and_after_bet_does_not_leak_worker(self):
        for data in [b'{', encode_message(self.bet())]:
            sock, state, thread = self.start_session()
            self.opened(sock, state)
            sock.sendall(data)
            sock.close()
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_exhausted_request_id_closes_even_after_invalid_bet(self):
        sock, state, thread = self.start_session()
        self.opened(sock, state)
        send_message(sock, self.bet(2147483647, command='ya tc 0 coin'))
        error = self.receive(sock, state)
        self.assertEqual((error['code'], error['fatal']), ('INTERNAL_ERROR', True))
        self.assertEqual(sock.recv(1), b'')
        thread.join(2)
        self.assertFalse(thread.is_alive())
