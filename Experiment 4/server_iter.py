#  我真诚地保证：
#  我自己独立地完成了整个程序从分析、设计到编码的所有工作。
#  如果在上述过程中，我遇到了什么困难而求教于人，那么，我将在程序实习报告中
#  详细地列举我所遇到的问题，以及别人给我的提示。
#  在此，我感谢 XXX, …, XXX对我的启发和帮助。下面的报告中，我还会具体地提到
#  他们在各个方法对我的帮助。
#  我的程序里中凡是引用到其他程序或文档之处，
#  例如教材、课堂笔记、网上的源代码以及其他参考书上的代码段,
#  我都已经在程序的注释里很清楚地注明了引用的出处。
#  我从未没抄袭过别人的程序，也没有盗用别人的程序，
#  不管是修改式的抄袭还是原封不动的抄袭。
#  我编写这个程序，从来没有想过要去破坏或妨碍其他计算机系统的正常运转。
#  <学生姓名>

"""
单线程迭代式 TCP 文件下载服务器。

主循环 accept() 到一个连接后，必须完整服务完当前客户端（读取请求、
校验路径、发送响应直到连接关闭）才会 accept 下一个连接，因此本阶段
不具备并发能力；这是阶段一有意为之，后续阶段再实现 thread/select/
asyncio 模型。
"""

import os
import socket
from pathlib import Path

import protocol

# 允许下载的唯一根目录。以本文件所在目录为基准解析，避免依赖启动时
# 的当前工作目录。
SHARED_DIR = Path(__file__).resolve().parent / 'shared_files'


def _try_send_error(conn, code, message):
    """在成功响应头发出之前尝试回送错误帧。

    对端可能已经断开或重置连接，此时 send_error 再失败也无法挽回，
    只需忽略该 OSError，保证当前连接随后被关闭、监听循环继续。
    """
    try:
        protocol.send_error(conn, code, message)
    except OSError as exc:
        print('错误响应未能发送（对端可能已关闭）：%s' % exc)


def _resolve_safe_path(remote_file):
    """唯一的路径校验入口，返回安全的候选 Path；非法时返回 None。

    校验集中在这一处，下游打开文件时不再重复判断。Windows 下需要
    同时堵死几类逃逸写法：
      - 绝对路径（含以 \\ 开头的 UNC 路径和以 \\ 或盘符开头的路径）；
      - 带盘符的相对路径，如 C:foo（它不是绝对路径但带有 drive）；
      - 任何父目录组件 '..'。
    之后再把候选路径真实解析（resolve 会展开符号链接），并确认解析
    结果仍位于共享目录之内，防止符号链接等方式跳出根目录。
    """
    rel = Path(remote_file)

    # is_absolute 覆盖 C:\\x、\\x、\\\\server\\share 等；drive 非空
    # 额外覆盖 C:foo 这类不带头反斜杠的盘符相对路径。
    if rel.is_absolute() or rel.drive != '' or '..' in rel.parts:
        return None

    try:
        candidate = (SHARED_DIR / remote_file).resolve()
        shared_root = SHARED_DIR.resolve()
        # Python 3.8 没有 Path.is_relative_to()，用 relative_to 捕获
        # ValueError 判定越界。
        candidate.relative_to(shared_root)
    except (OSError, ValueError):
        return None
    return candidate


def handle_client(conn):
    """服务一个已接受的连接，全过程不抛出可预期异常。"""
    # A. 读取请求帧。帧格式错误时尽量回状态码 4；对端已断开则
    # _try_send_error 内部会忽略发送失败。
    try:
        remote_file = protocol.recv_request(conn)
    except protocol.ProtocolError as exc:
        print('请求帧错误：%s' % exc)
        _try_send_error(conn, protocol.STATUS_BAD_REQUEST, '请求格式错误')
        return

    # B. 路径合法性校验（状态码 2 优先于存在性判断）。
    candidate = _resolve_safe_path(remote_file)
    if candidate is None:
        print('拒绝非法或越界路径：%r' % remote_file)
        _try_send_error(conn, protocol.STATUS_FORBIDDEN, '文件路径非法或越界')
        return

    # 不存在、是目录或其他非普通文件，统一状态码 1。
    if not candidate.is_file():
        print('文件不存在或不是普通文件：%r' % remote_file)
        _try_send_error(conn, protocol.STATUS_NOT_FOUND, '文件不存在或不是普通文件')
        return

    # C. 先成功打开文件，再考虑发成功头，避免“头已发出却打不开文件”。
    try:
        file_obj = open(candidate, 'rb')
    except OSError as exc:
        print('文件打开失败：%s' % exc)
        _try_send_error(conn, protocol.STATUS_IO_ERROR, '服务器文件打开失败')
        return

    # with 保证文件句柄最终关闭，即使传输中途异常。
    with file_obj:
        # 对已打开的描述符 fstat 取真实大小，避免先 getsize 再 open
        # 之间文件被替换造成的大小不一致。元数据读取发生在成功头
        # 发出之前，失败时仍可按状态码 3 回送错误帧。
        try:
            file_size = os.fstat(file_obj.fileno()).st_size
        except OSError as exc:
            print('文件元数据读取失败：%s' % exc)
            _try_send_error(conn, protocol.STATUS_IO_ERROR, '服务器文件读取失败')
            return

        # 成功头一旦发出，协议上就不再有改发错误帧的机会。
        try:
            protocol.send_success(conn, file_size)
        except OSError as exc:
            # 头都没发出去，对端多半已断开，直接结束本连接即可。
            print('成功响应头发送失败：%s' % exc)
            return

        sent = 0
        try:
            while sent < file_size:
                # 每块最多 64 KiB，且不超过声明大小剩余量，保证累计
                # 发送字节不超过 file_size。
                chunk = file_obj.read(
                    min(protocol.BUFFER_SIZE, file_size - sent))
                if not chunk:
                    # read 提前返回空：文件在传输期间被缩短，无法补齐
                    # 声明大小。此时不能再补发状态码，只能结束连接，
                    # 客户端会依据声明大小报“传输不完整”。
                    print('文件传输期间被缩短，已发送 %d/%d，结束连接'
                          % (sent, file_size))
                    return
                conn.sendall(chunk)
                sent += len(chunk)
        except OSError as exc:
            # 成功头之后的读取或网络发送失败：同样只记录诊断并结束，
            # 不破坏外层 accept 循环。
            print('文件数据传输失败（已发送 %d/%d）：%s'
                  % (sent, file_size, exc))
            return


def run(host, port):
    """创建监听套接字并迭代 accept，直到进程被中断。"""
    # with 保证监听 socket 在进程退出/中断时关闭。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listen_sock:
        # 允许服务器重启后快速重新绑定同一端口，避免 TIME_WAIT 滞留。
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind((host, port))
        listen_sock.listen()
        print('迭代服务器已启动，监听 %s:%d，共享目录 %s'
              % (host, port, SHARED_DIR))

        while True:
            conn, addr = listen_sock.accept()
            try:
                print('客户端接入：%s' % (addr,))
                handle_client(conn)
            except OSError as exc:
                # 兜底：当前连接上未预期的系统错误只影响该连接。
                print('当前连接处理异常：%s' % exc)
            finally:
                conn.close()
                print('连接已关闭：%s' % (addr,))
