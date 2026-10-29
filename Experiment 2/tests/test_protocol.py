import json
import socket
import time
import unittest
from unittest.mock import patch

from protocol import (ProtocolError, ReaderState, decode_message, encode_message,
                      read_message, send_message)

WELCOME = {'version': 1, 'type': 'welcome', 'ruleset': 'A1', 'message': '欢迎\n下注'}
OPEN = {'version': 1, 'type': 'round_open', 'round_id': 1,
        'predicted_dice': [6, 1], 'message': '开盘'}
RESULT = {'version': 1, 'type': 'round_result', 'request_id': 1, 'round_id': 1,
          'predicted_dice': [6, 1], 'actual_dice': [6, 1],
          'matched_types': ['tc', 'dc', 'qx'], 'final_type': 'tc',
          'eligible_types': ['tc', 'dc'], 'play': 'tc', 'amount': 10,
          'currency': 'coin', 'outcome': 'win', 'multiplier': 35,
          'payout': 360, 'net_change': 350}


class ChunkSocket:
    """Deliberately chosen recv boundaries; real socket integration is separate."""
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def recv(self, size):
        return next(self.chunks, b'')


class ProtocolTests(unittest.TestCase):
    def assert_code(self, code, function, *args):
        with self.assertRaises(ProtocolError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)

    def test_round_trip_all_message_types(self):
        messages = [WELCOME, OPEN, RESULT,
                    {'version': 1, 'type': 'bet', 'request_id': 1, 'round_id': 1,
                     'command': 'ya tc 10 coin'},
                    {'version': 1, 'type': 'error', 'request_id': None, 'round_id': 1,
                     'code': 'INVALID_ENCODING', 'message': '编码错误', 'fatal': True},
                    {'version': 1, 'type': 'quit', 'request_id': 2},
                    {'version': 1, 'type': 'bye', 'request_id': 2, 'message': '再见'}]
        for message in messages:
            frame = encode_message(message)
            self.assertEqual(frame.count(b'\n'), 1)
            self.assertEqual(decode_message(frame[:-1]), message)
        long_command = {'version': 1, 'type': 'bet', 'request_id': 1, 'round_id': 1,
                        'command': 'x' * 257 + '\n'}
        self.assertEqual(decode_message(encode_message(long_command)[:-1]), long_command)

    def test_strict_json_and_encoding(self):
        for raw, code in [(b'\xff', 'INVALID_ENCODING'), (b'\xef\xbb\xbf{}', 'INVALID_ENCODING'),
                          (b'', 'INVALID_JSON'), (b'{}\r', 'INVALID_JSON'),
                          (b'[]', 'INVALID_JSON'), (b'{', 'INVALID_JSON'),
                          (b'{"version":1,"version":1}', 'INVALID_JSON'),
                          (b'{"x":NaN}', 'INVALID_JSON'), (b'{"x":Infinity}', 'INVALID_JSON'),
                          (b'{"x":1e999}', 'INVALID_JSON')]:
            with self.subTest(raw=raw):
                self.assert_code(code, decode_message, raw)

    def test_reject_wrong_fields_types_and_ranges(self):
        quit_message = {'version': 1, 'type': 'quit', 'request_id': 1}
        for changes in [{'request_id': True}, {'request_id': 1.0}, {'request_id': 0},
                        {'request_id': 2147483648}, {'extra': 1}, {'type': 'unknown'},
                        {'version': True}]:
            self.assert_code('INVALID_MESSAGE', encode_message, quit_message | changes)
        self.assert_code('UNSUPPORTED_VERSION', encode_message, quit_message | {'version': 2})
        self.assert_code('INVALID_MESSAGE', encode_message, {'version': 1, 'type': 'quit'})
        for changes in [{'predicted_dice': [True, 1]}, {'predicted_dice': [1, 7]},
                        {'predicted_dice': [1]}, {'message': '中' * 342}]:
            self.assert_code('INVALID_MESSAGE', encode_message, OPEN | changes)
        for changes in [{'amount': 0}, {'payout': -1}, {'outcome': 'draw'},
                        {'currency': 'usd'}, {'matched_types': ['tc', 'tc']},
                        {'actual_dice': [0, 1]}, {'net_change': True}]:
            self.assert_code('INVALID_MESSAGE', encode_message, RESULT | changes)

    @patch('protocol.select.select', side_effect=lambda read, *args: (read, [], []))
    def test_every_split_and_bytewise_utf8(self, ready):
        frame = encode_message(WELCOME)
        partitions = [[frame[:i], frame[i:]] for i in range(1, len(frame))]
        partitions.append([bytes([b]) for b in frame])
        for chunks in partitions:
            self.assertEqual(read_message(ChunkSocket(chunks), ReaderState()), WELCOME)

    @patch('protocol.select.select', side_effect=lambda read, *args: (read, [], []))
    def test_coalesced_messages_and_partial_tail(self, ready):
        first, second = encode_message(WELCOME), encode_message(OPEN)
        source = ChunkSocket([first + second[:8], second[8:]])
        state = ReaderState()
        self.assertEqual(read_message(source, state), WELCOME)
        self.assertEqual(read_message(source, state), OPEN)
        source = ChunkSocket([first * 100])
        state = ReaderState()
        for _ in range(100):
            self.assertEqual(read_message(source, state), WELCOME)
        self.assertEqual(ready.call_count, 3)

    @patch('protocol.select.select', side_effect=lambda read, *args: (read, [], []))
    def test_frame_length_and_eof(self, ready):
        raw = encode_message(WELCOME)[:-1]
        exact = raw + b' ' * (8191 - len(raw)) + b'\n'
        self.assertEqual(read_message(ChunkSocket([exact]), ReaderState()), WELCOME)
        for data in [exact[:-1] + b' \n', b'x' * 8192]:
            self.assert_code('FRAME_TOO_LARGE', read_message, ChunkSocket([data]), ReaderState())
        self.assert_code('INVALID_JSON', read_message, ChunkSocket([b'{', b'']), ReaderState())
        with self.assertRaises(EOFError):
            read_message(ChunkSocket([]), ReaderState())

    def test_deadline_and_slow_trickle_do_not_reset(self):
        source = ChunkSocket([b'{', b'"'])
        # Poll at t=0, first byte at t=0; poll/second byte at t=6; next poll t=10.
        with patch('protocol.time.monotonic', side_effect=[0, 0, 6, 6, 10]), \
             patch('protocol.select.select', side_effect=lambda read, *args: (read, [], [])):
            self.assert_code('TIMEOUT', read_message, source, ReaderState())
        with patch('protocol.select.select', return_value=([], [], [])) as ready:
            self.assert_code('TIMEOUT', read_message, source, ReaderState(), time.monotonic() + .1)
            self.assertLessEqual(ready.call_args.args[3], .1)

    def test_idle_has_no_deadline_and_real_socket_roundtrip(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        with patch('protocol.select.select', wraps=__import__('select').select) as ready:
            send_message(a, WELCOME)
            self.assertEqual(read_message(b, ReaderState()), WELCOME)
            self.assertIsNone(ready.call_args.args[3])

    def test_completed_frame_arriving_after_deadline_is_rejected(self):
        frame = encode_message(WELCOME)
        with patch('protocol.time.monotonic', side_effect=[0, 0, 9, 11]), \
             patch('protocol.select.select', side_effect=lambda read, *args: (read, [], [])):
            self.assert_code('TIMEOUT', read_message, ChunkSocket([frame[:1], frame[1:]]), ReaderState())
        with patch('protocol.time.monotonic', side_effect=[0, 11]), \
             patch('protocol.select.select', side_effect=lambda read, *args: (read, [], [])):
            self.assert_code('TIMEOUT', read_message, ChunkSocket([frame]), ReaderState(), 10)

    def test_buffered_complete_frames_keep_arrival_time(self):
        state = ReaderState()
        source = ChunkSocket([encode_message(WELCOME) + encode_message(OPEN)])
        with patch('protocol.time.monotonic', side_effect=[0, 1]), \
             patch('protocol.select.select', side_effect=lambda read, *args: (read, [], [])):
            self.assertEqual(read_message(source, state, 10), WELCOME)
        with patch('protocol.time.monotonic', return_value=99):
            self.assertEqual(read_message(source, state, 10), OPEN)
