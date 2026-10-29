"""Shared v1 JSON Lines framing and message structure; no game decisions."""

from dataclasses import dataclass, field
import json
import math
import select
import socket
import time

VERSION = 1
RULESET = 'A1'
MAX_ID = 2147483647
MAX_FRAME = 8192
PLAYS = ('tc', 'dc', 'kp', 'qx', 'dd', 'sx')
CURRENCIES = ('coin', 'silver', 'gold')
FATAL_CODES = frozenset(('FRAME_TOO_LARGE', 'INVALID_ENCODING', 'INVALID_JSON',
    'UNSUPPORTED_VERSION', 'INVALID_MESSAGE', 'UNEXPECTED_MESSAGE', 'TIMEOUT', 'INTERNAL_ERROR'))
BUSINESS_CODES = frozenset(('DUPLICATE_REQUEST', 'ROUND_MISMATCH', 'INVALID_COMMAND',
    'INVALID_PLAY', 'INVALID_AMOUNT', 'INVALID_CURRENCY'))
FIELDS = {
    'welcome': ('ruleset', 'message'),
    'round_open': ('round_id', 'predicted_dice', 'message'),
    'bet': ('request_id', 'round_id', 'command'),
    'round_result': ('request_id', 'round_id', 'predicted_dice', 'actual_dice',
        'matched_types', 'final_type', 'eligible_types', 'play', 'amount',
        'currency', 'outcome', 'multiplier', 'payout', 'net_change'),
    'error': ('request_id', 'round_id', 'code', 'message', 'fatal'),
    'quit': ('request_id',),
    'bye': ('request_id', 'message'),
}


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str = ''):
        super().__init__(message or code)
        self.code = code


@dataclass
class ReaderState:
    buffer: bytearray = field(default_factory=bytearray)
    partial_since: float | None = None
    last_received: float | None = None


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def validate_message(message: dict) -> None:
    def require(condition):
        if not condition:
            raise ProtocolError('INVALID_MESSAGE')

    require(type(message) is dict and type(message.get('version')) is int)
    if message['version'] != VERSION:
        raise ProtocolError('UNSUPPORTED_VERSION')
    kind = message.get('type')
    require(type(kind) is str and kind in FIELDS)
    require(set(message) == {'version', 'type', *FIELDS[kind]})
    for key in FIELDS[kind]:
        value = message[key]
        if key in ('request_id', 'round_id'):
            require((kind == 'error' and value is None) or _integer(value, 1, MAX_ID))
        elif key in ('predicted_dice', 'actual_dice'):
            require(type(value) is list and len(value) == 2
                    and all(_integer(d, 1, 6) for d in value))
        elif key in ('matched_types', 'eligible_types'):
            require(type(value) is list and all(type(v) is str and v in PLAYS for v in value))
            require(len(value) == len(set(value)))
            require(value == sorted(value, key=PLAYS.index))
        elif key in ('message', 'ruleset', 'command'):
            require(type(value) is str)
            try:
                size = len(value.encode('utf-8'))
            except UnicodeEncodeError as exc:
                raise ProtocolError('INVALID_MESSAGE') from exc
            if key == 'message':
                require(size <= 1024)
        elif key == 'play':
            require(type(value) is str and value in PLAYS)
        elif key == 'final_type':
            require(type(value) is str and value in (*PLAYS, 'banker'))
        elif key == 'currency':
            require(type(value) is str and value in CURRENCIES)
        elif key == 'outcome':
            require(type(value) is str and value in ('win', 'lose'))
        elif key == 'amount':
            require(_integer(value, 1, 1000000))
        elif key == 'multiplier':
            require(_integer(value, 1, 35))
        elif key == 'payout':
            require(_integer(value, 0, 36000000))
        elif key == 'net_change':
            require(_integer(value, -35000000, 35000000))
        elif key == 'fatal':
            require(type(value) is bool)
        elif key == 'code':
            require(type(value) is str and value in FATAL_CODES | BUSINESS_CODES)
    if kind == 'error':
        require(message['fatal'] == (message['code'] in FATAL_CODES))


def encode_message(message: dict) -> bytes:
    validate_message(message)
    try:
        frame = json.dumps(message, ensure_ascii=False, allow_nan=False,
                           separators=(',', ':')).encode('utf-8') + b'\n'
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError('INVALID_MESSAGE') from exc
    if len(frame) > MAX_FRAME:
        raise ProtocolError('FRAME_TOO_LARGE')
    return frame


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('non-finite JSON number')


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('non-finite JSON number')
    return number


def decode_message(frame_bytes: bytes) -> dict:
    if len(frame_bytes) >= MAX_FRAME:
        raise ProtocolError('FRAME_TOO_LARGE')
    try:
        text = frame_bytes.decode('utf-8', errors='strict')
    except UnicodeDecodeError as exc:
        raise ProtocolError('INVALID_ENCODING') from exc
    if text.startswith('\ufeff'):
        raise ProtocolError('INVALID_ENCODING')
    if text.endswith('\r') or '\n' in text:
        raise ProtocolError('INVALID_JSON')
    try:
        message = json.loads(text, object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant, parse_float=_finite_float)
        if type(message) is not dict:
            raise ValueError('top level must be an object')
    except (ValueError, RecursionError) as exc:
        raise ProtocolError('INVALID_JSON') from exc
    validate_message(message)
    return message


def read_message(sock: socket.socket, reader_state: ReaderState,
                 deadline: float | None = None) -> dict:
    """Return one message, preserving surplus bytes. deadline is monotonic absolute time.

    No deadline while waiting for the first byte unless supplied by the caller;
    an incomplete frame has a fixed ten-second limit from its first byte.
    """
    state = reader_state
    while True:
        end = state.buffer.find(b'\n')
        if end >= 0:
            # Buffered frames retain their receive time, not the time of this call.
            if deadline is not None and state.last_received is not None and state.last_received >= deadline:
                raise ProtocolError('TIMEOUT')
            if end + 1 > MAX_FRAME:
                raise ProtocolError('FRAME_TOO_LARGE')
            frame = bytes(state.buffer[:end])
            del state.buffer[:end + 1]
            state.partial_since = state.last_received if state.buffer else None
            return decode_message(frame)
        if len(state.buffer) >= MAX_FRAME:
            raise ProtocolError('FRAME_TOO_LARGE')
        now = time.monotonic()
        limits = [limit for limit in (deadline,
                  None if state.partial_since is None else state.partial_since + 10) if limit is not None]
        timeout = min(limits) - now if limits else None
        if timeout is not None and timeout <= 0:
            raise ProtocolError('TIMEOUT')
        ready, _, _ = select.select([sock], [], [], timeout)
        if not ready:
            raise ProtocolError('TIMEOUT')
        data = sock.recv(4096)
        if not data:
            if state.buffer:
                raise ProtocolError('INVALID_JSON', '连接在完整帧之前中断')
            raise EOFError('连接已关闭')
        state.last_received = time.monotonic()
        # select readiness is not a guarantee that recv completes before a deadline.
        if limits and state.last_received >= min(limits):
            raise ProtocolError('TIMEOUT')
        if not state.buffer:
            state.partial_since = state.last_received
        state.buffer.extend(data)


def send_message(sock: socket.socket, message: dict) -> None:
    sock.sendall(encode_message(message))
