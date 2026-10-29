"""Terminal client: render authoritative server messages, never decide outcomes."""

import argparse
import ipaddress
import socket
import sys
import time

from protocol import MAX_ID, RULESET, ProtocolError, ReaderState, read_message, send_message

NAMES = {'tc': '头彩', 'dc': '大彩', 'kp': '空盘', 'qx': '七星',
         'dd': '单对', 'sx': '散星', 'banker': '庄家'}
FACES = {
    1: ('     ', '  o  ', '     '),
    2: ('o    ', '     ', '    o'),
    3: ('o    ', '  o  ', '    o'),
    4: ('o   o', '     ', 'o   o'),
    5: ('o   o', '  o  ', 'o   o'),
    6: ('o   o', 'o   o', 'o   o'),
}


class ServerError(Exception):
    pass


def render_result(result: dict) -> str:
    a, b = result['actual_dice']
    lines = [f'实际骰子：({a}, {b})', '+-------+  +-------+']
    lines.extend(f'| {left} |  | {right} |' for left, right in zip(FACES[a], FACES[b]))
    lines.append('+-------+  +-------+')
    matched = '、'.join(NAMES[code] for code in result['matched_types']) or '无'
    eligible = '、'.join(NAMES[code] for code in result['eligible_types']) or '无'
    lines.extend([
        f'原始匹配：{matched}；最终点型：{NAMES[result["final_type"]]}；中奖资格：{eligible}',
        f'本注：{NAMES[result["play"]]} {result["amount"]} {result["currency"]}；'
        f'{"中奖" if result["outcome"] == "win" else "未中，庄家赢"}',
        f'总返还：{result["payout"]} {result["currency"]}；'
        f'净变化：{result["net_change"]:+d} {result["currency"]}',
    ])
    return '\n'.join(lines)


def _receive(sock, reader, deadline, expected, request_id=None):
    message = read_message(sock, reader, deadline)
    if message['type'] == 'error' and message['fatal']:
        if message['request_id'] not in (None, request_id):
            raise ProtocolError('INVALID_MESSAGE', '致命错误的请求编号不匹配')
        raise ServerError(f'{message["code"]}: {message["message"]}')
    if message['type'] not in expected:
        raise ProtocolError('UNEXPECTED_MESSAGE', '响应次序不正确')
    if request_id is not None and message['type'] != 'round_open' and message.get('request_id') != request_id:
        raise ProtocolError('INVALID_MESSAGE', '响应请求编号不匹配')
    return message


def _show_open(opened):
    a, b = opened['predicted_dice']
    print(f'\n第 {opened["round_id"]} 盘；预叫：({a}, {b})')
    print(opened['message'])


def run_client(host: str, port: int) -> int:
    pending_bet = False
    quit_pending = False
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.settimeout(10)
            reader = ReaderState()
            deadline = time.monotonic() + 10
            welcome = _receive(sock, reader, deadline, ('welcome',))
            if welcome['ruleset'] != RULESET:
                raise ProtocolError('INVALID_MESSAGE', '不支持服务端规则版本')
            print(welcome['message'])
            print('玩法：tc头彩35倍 / dc大彩17倍 / kp空盘5倍 / qx七星5倍 / dd单对3倍 / sx散星2倍')
            print('赔率为净赢倍数；单注1～1,000,000；三币不兑换，不设余额。')
            opened = _receive(sock, reader, deadline, ('round_open',))
            if opened['round_id'] != 1:
                raise ProtocolError('INVALID_MESSAGE', '首次盘号必须为1')
            request_id = 0
            while True:
                _show_open(opened)
                try:
                    command = input('下注或 quit > ')
                except (EOFError, KeyboardInterrupt):
                    print()
                    command = 'quit'
                if not command.strip():
                    continue
                request_id += 1
                if request_id > MAX_ID:
                    raise ProtocolError('INVALID_MESSAGE', '会话编号已达上限，请重新连接')
                if command.strip() == 'quit':
                    quit_pending = True
                    send_message(sock, dict(version=1, type='quit', request_id=request_id))
                    bye = _receive(sock, reader, time.monotonic() + 2, ('bye',), request_id)
                    print(bye['message'])
                    return 0
                pending_bet = True
                try:
                    send_message(sock, dict(version=1, type='bet', request_id=request_id,
                                            round_id=opened['round_id'], command=command))
                except ProtocolError:
                    # Encoding failed before sendall; this bet could not reach the server.
                    pending_bet = False
                    raise
                deadline = time.monotonic() + 10
                result = _receive(sock, reader, deadline, ('round_result', 'error'), request_id)
                if result['round_id'] != opened['round_id']:
                    raise ProtocolError('INVALID_MESSAGE', '响应盘号不匹配')
                if result['type'] == 'error':
                    pending_bet = False
                    print(f'{result["code"]}: {result["message"]}')
                    continue
                if result['predicted_dice'] != opened['predicted_dice']:
                    raise ProtocolError('INVALID_MESSAGE', '本盘预叫被改变')
                print(render_result(result))
                pending_bet = False
                new_round = _receive(sock, reader, deadline, ('round_open',), request_id)
                if new_round['round_id'] != opened['round_id'] + 1:
                    raise ProtocolError('INVALID_MESSAGE', '下一盘盘号不连续')
                opened = new_round
    except (OSError, EOFError, ProtocolError, ServerError, KeyboardInterrupt) as exc:
        label = '协议错误' if isinstance(exc, ProtocolError) else '连接或会话结束'
        print(f'{label}: {exc}', file=sys.stderr)
        if pending_bet:
            print('本盘结果可能未知；不会自动重发下注。', file=sys.stderr)
        if quit_pending:
            print('退出未获得服务端确认。', file=sys.stderr)
        return 1


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('端口必须为1～65535整数') from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('端口必须为1～65535整数')
    return port


def parse_endpoint(text: str) -> tuple[str, int]:
    """Parse the IPv4:port entered at the interactive connection prompt."""
    host, port_text = text.strip().split(':')
    address = ipaddress.IPv4Address(host)
    if not port_text.isascii() or not port_text.isdigit() or len(port_text) > 5:
        raise ValueError('端口须为1～65535的ASCII整数')
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError('端口须为1～65535的ASCII整数')
    return str(address), port


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='RemoteBet TCP 命令行客户端')
    parser.add_argument('host', nargs='?', help='服务端IP或主机名；省略时提示输入IPv4:端口')
    parser.add_argument('--port', type=_port, default=5000)
    args = parser.parse_args(argv)
    if args.host is None:
        while True:
            try:
                endpoint = input(f'服务端 IP:端口 [127.0.0.1:{args.port}]（quit退出）> ').strip()
            except (EOFError, KeyboardInterrupt):
                print('\n已取消连接。')
                return 0
            if endpoint == 'quit':
                return 0
            if not endpoint:
                args.host = '127.0.0.1'
                break
            try:
                args.host, args.port = parse_endpoint(endpoint)
                break
            except ValueError:
                print('地址格式不正确，请输入 IPv4:端口，例如 192.168.1.20:5000。')
    return run_client(args.host, args.port)


if __name__ == '__main__':
    raise SystemExit(main())
