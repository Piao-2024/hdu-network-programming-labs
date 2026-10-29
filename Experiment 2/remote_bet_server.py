"""Single active client TCP server for RemoteBet."""

import argparse
from dataclasses import asdict
import logging
import socket

from game_logic import BetValidationError, classify_roll, generate_dice, parse_bet, settle_bet
from protocol import (MAX_ID, RULESET, ProtocolError, ReaderState, read_message, send_message)

LOG = logging.getLogger(__name__)


def _message(kind, **fields):
    return dict(version=1, type=kind, **fields)


def _error(sock, request_id, round_id, code, message, fatal=False):
    send_message(sock, _message('error', request_id=request_id, round_id=round_id,
                               code=code, message=message, fatal=fatal))


def serve_session(sock: socket.socket, *, draw_int=None) -> None:
    """Own and close this connection. Random source is independent of game decisions."""
    round_id = None
    request_id = None
    try:
        # Bound writes without imposing an idle deadline: reads use select first.
        sock.settimeout(10)
        reader = ReaderState()
        last_request_id = 0
        round_id = 1
        predicted = generate_dice(draw_int)
        send_message(sock, _message('welcome', ruleset=RULESET,
            message='欢迎来到 RemoteBet！虚拟单盘游戏，无余额；输入 quit 退出。'))
        while True:
            send_message(sock, _message('round_open', round_id=round_id,
                predicted_dice=list(predicted),
                message='庄家开盘，请下注：ya <玩法代码> <数量> <coin|silver|gold>'))
            while True:
                request_id = None
                request = read_message(sock, reader)
                if request['type'] not in ('bet', 'quit'):
                    raise ProtocolError('UNEXPECTED_MESSAGE')
                request_id = request['request_id']
                if request_id <= last_request_id:
                    _error(sock, request_id, round_id, 'DUPLICATE_REQUEST', '请求编号重复或倒退')
                    continue
                last_request_id = request_id
                if request['type'] == 'quit':
                    send_message(sock, _message('bye', request_id=request_id, message='已退出 RemoteBet，再见！'))
                    return
                if last_request_id == MAX_ID:
                    raise ProtocolError('INTERNAL_ERROR', '会话编号已达上限，本注未处理，请重新连接')
                if request['round_id'] != round_id:
                    _error(sock, request_id, round_id, 'ROUND_MISMATCH', '盘号已过期或不正确')
                    continue
                try:
                    bet = parse_bet(request['command'])
                except BetValidationError as exc:
                    _error(sock, request_id, round_id, exc.code, str(exc))
                    continue
                actual = generate_dice(draw_int)
                classification = classify_roll(predicted, actual)
                settlement = settle_bet(bet, classification)
                send_message(sock, _message('round_result', request_id=request_id,
                    round_id=round_id, predicted_dice=list(predicted), actual_dice=list(actual),
                    matched_types=list(classification.matched_types),
                    final_type=classification.final_type,
                    eligible_types=list(classification.eligible_types),
                    **asdict(bet), **asdict(settlement)))
                if round_id == MAX_ID:
                    raise ProtocolError('INTERNAL_ERROR', '会话编号已达上限，请重新连接')
                round_id += 1
                predicted = generate_dice(draw_int)
                break
    except ProtocolError as exc:
        try:
            _error(sock, request_id, round_id, exc.code, str(exc), fatal=True)
        except OSError:
            pass
    except (EOFError, OSError):
        LOG.info('会话连接已结束')
    except Exception:
        LOG.exception('会话处理失败')
        try:
            _error(sock, request_id, round_id, 'INTERNAL_ERROR', '服务端内部错误', fatal=True)
        except OSError:
            pass
    finally:
        sock.close()


def create_listener(host: str, port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(5)
        return listener
    except BaseException:
        listener.close()
        raise


def run_server(listener: socket.socket) -> None:
    with listener:
        print(f'RemoteBet listening on {listener.getsockname()[0]}:{listener.getsockname()[1]}', flush=True)
        while True:
            sock, address = listener.accept()
            LOG.info('客户端连接: %s', address)
            serve_session(sock)


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('端口必须为1～65535整数') from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('端口必须为1～65535整数')
    return port


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='RemoteBet TCP 服务端（单活动客户端）')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=_port, default=5000)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    try:
        run_server(create_listener(args.host, args.port))
    except KeyboardInterrupt:
        print('\n服务端已停止', flush=True)
        return 0
    except OSError as exc:
        LOG.error('无法运行服务端: %s', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
