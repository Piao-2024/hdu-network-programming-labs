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
基于 select() 的非阻塞 I/O 多路复用文件下载服务器。

单线程事件循环通过 select.select() 同时监控监听 socket 与多个客户端
socket 的就绪事件：某个客户端在慢速发送请求或慢速接收文件时，事件
循环仍可交错处理其他连接，因此不需要为每个连接创建线程。

重要边界：
  - Windows 的 select() 只能监控 socket，不能监控普通磁盘文件，
    文件仍用普通 open() 同步分块读取；
  - 非阻塞 recv()/send() 只代表“此刻能读/写多少算多少”，必须
    处理部分读、部分写和就绪后仍短暂 BlockingIOError 的情况。
"""

import os
import select
import socket
import struct

import protocol
import server_iter

# 请求接收阶段：先凑 4 字节长度头，再凑 N 字节路径
PHASE_RECV_HEADER = 'recv_header'
PHASE_RECV_PATH = 'recv_path'
# 响应阶段：成功头/错误帧与文件块都在待发送缓冲中顺序发出
PHASE_SEND = 'send'


class ConnectionState:
    """一个客户端连接的独立状态。

    各连接的缓冲区、文件句柄和阶段完全隔离，慢连接只影响自己的
    状态，不会拖住其他连接。
    """

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.phase = PHASE_RECV_HEADER

        # 已接收但尚未完成解析的请求字节
        self.recv_buf = b''
        # 长度头解析出的路径字节数 N
        self.path_len = 0

        # 待发送字节（成功头/错误帧，或一块文件数据），由写事件
        # 按实际 send() 返回值推进
        self.send_buf = b''
        # 已打开的文件句柄；失败响应时为 None
        self.file_obj = None
        # 文件中尚未入队发送的字节数（不含已在 send_buf 中的部分）
        self.file_remaining = 0

    def close(self):
        """唯一的连接资源释放入口：先关文件再关 socket。"""
        if self.file_obj is not None:
            self.file_obj.close()
            self.file_obj = None
        self.sock.close()


def _queue_error(st, code, message):
    """把失败响应帧编码到待发送缓冲，随后转入发送阶段。

    这里不能调用 protocol.send_error()（它内部是阻塞式 sendall），
    只复用 protocol 的常量、状态码和 struct 格式，自行编码字节。
    """
    message_bytes = message.encode('utf-8')
    st.send_buf = (struct.pack(protocol.FMT_STATUS, code)
                   + struct.pack(protocol.FMT_LENGTH, len(message_bytes))
                   + message_bytes)
    st.phase = PHASE_SEND


def _start_download(st, remote_file):
    """请求完整后做一次集中校验并排队响应，校验逻辑复用 server_iter。"""
    # 路径合法性（状态码 2）复用现有实现，避免复制第二套策略。
    candidate = server_iter._resolve_safe_path(remote_file)
    if candidate is None:
        _queue_error(st, protocol.STATUS_FORBIDDEN, '文件路径非法或越界')
        return

    # 不存在或不是普通文件：状态码 1。
    if not candidate.is_file():
        _queue_error(st, protocol.STATUS_NOT_FOUND, '文件不存在或不是普通文件')
        return

    # 成功头排队前，打开文件与读取元数据处于同一错误分支：任何
    # 一步失败都只影响当前连接，排队状态码 3，不能让 OSError 穿透
    # 事件回调导致整个事件循环终止。
    file_obj = None
    try:
        file_obj = open(candidate, 'rb')
        # 对已打开描述符 fstat 取真实大小，避免 getsize 与 open 之间
        # 文件被替换造成的大小不一致。
        file_size = os.fstat(file_obj.fileno()).st_size
    except OSError as exc:
        print('文件打开或元数据读取失败：%s' % exc)
        # open 已成功而 fstat 失败时，先关闭本次已打开的句柄；
        # 此时尚未赋值给 st.file_obj，清理责任在本分支。
        if file_obj is not None:
            file_obj.close()
        _queue_error(st, protocol.STATUS_IO_ERROR, '服务器文件打开或读取失败')
        return

    st.file_obj = file_obj
    st.file_remaining = file_size
    # 先只排队 9 字节成功头，文件块等头真正发完再按需加载。
    st.send_buf = (struct.pack(protocol.FMT_STATUS, protocol.STATUS_SUCCESS)
                   + struct.pack(protocol.FMT_FILE_SIZE, file_size))
    st.phase = PHASE_SEND


def _parse_request(st):
    """从 recv_buf 中按协议推进长度头和路径的解析。

    数据不足时保留现场返回，等下一次读事件；一连接只处理一个请求，
    路径取满后同次 recv 带来的多余字节直接忽略。
    """
    if st.phase == PHASE_RECV_HEADER:
        if len(st.recv_buf) < protocol.LENGTH_FIELD_SIZE:
            return
        (n,) = struct.unpack(
            protocol.FMT_LENGTH, st.recv_buf[:protocol.LENGTH_FIELD_SIZE])
        # 长度非法：不继续读请求体，直接排队状态码 4。
        if n == 0 or n > protocol.MAX_PATH_BYTES:
            print('请求路径长度非法：N=%d' % n)
            _queue_error(st, protocol.STATUS_BAD_REQUEST, '请求路径长度非法')
            return
        st.path_len = n
        st.recv_buf = st.recv_buf[protocol.LENGTH_FIELD_SIZE:]
        st.phase = PHASE_RECV_PATH

    if st.phase == PHASE_RECV_PATH:
        if len(st.recv_buf) < st.path_len:
            return
        path_bytes = st.recv_buf[:st.path_len]
        # 接收缓冲已完成使命，立即释放，避免无意义占用；多余字节
        # 按一连接一请求规则忽略。
        st.recv_buf = b''
        try:
            remote_file = path_bytes.decode('utf-8')
        except UnicodeDecodeError as exc:
            print('请求路径不是合法 UTF-8：%s' % exc)
            _queue_error(st, protocol.STATUS_BAD_REQUEST, '请求路径编码非法')
            return
        _start_download(st, remote_file)


def _on_readable(st, closing):
    """处理客户端读事件；需要关闭时把状态放入 closing 列表。"""
    try:
        data = st.sock.recv(protocol.BUFFER_SIZE)
    except BlockingIOError:
        # 非阻塞下的假就绪：状态不变，等下一轮事件。
        return
    except OSError as exc:
        print('连接 %s 读取失败：%s' % (st.addr, exc))
        closing.append(st)
        return

    if data == b'':
        # 对端关闭发送方向。
        if st.phase in (PHASE_RECV_HEADER, PHASE_RECV_PATH):
            # 请求不完整：尽量排队状态码 4，随后只等写事件回送；
            # 若对端已彻底断开导致写失败，写处理会负责清理。
            print('连接 %s 请求未完整即关闭' % (st.addr,))
            _queue_error(st, protocol.STATUS_BAD_REQUEST, '请求不完整或连接提前关闭')
        else:
            closing.append(st)
        return

    st.recv_buf += data
    _parse_request(st)


def _on_writable(st, closing):
    """处理客户端写事件，严格按 send() 实际字节数推进。"""
    try:
        sent = st.sock.send(st.send_buf)
    except BlockingIOError:
        # 可写事件后仍短暂不可写：保留缓冲等下一轮。
        return
    except OSError as exc:
        print('连接 %s 发送失败：%s' % (st.addr, exc))
        closing.append(st)
        return

    if sent == 0:
        print('连接 %s send() 返回 0，关闭连接' % (st.addr,))
        closing.append(st)
        return

    # 只移除已确认发出的前缀，剩余部分留到下次写就绪。
    st.send_buf = st.send_buf[sent:]
    if st.send_buf:
        return

    # 当前缓冲已清空。
    if st.file_obj is not None and st.file_remaining > 0:
        # 上一块（或成功头）发完才加载下一块，每次最多 64 KiB 且
        # 不超过声明大小的剩余量，保证累计输出不超过文件大小。
        try:
            chunk = st.file_obj.read(
                min(protocol.BUFFER_SIZE, st.file_remaining))
        except OSError as exc:
            # 成功头已发出，不能再补发状态码 3，只诊断并结束连接。
            print('文件读取失败，结束连接：%s' % exc)
            closing.append(st)
            return
        if not chunk:
            # 文件传输期间被缩短：无法补齐声明大小，同理只结束连接，
            # 客户端会判定“传输不完整”。
            print('文件传输期间被缩短，结束连接：%s' % (st.addr,))
            closing.append(st)
            return
        st.file_remaining -= len(chunk)
        st.send_buf = chunk
        return

    # 无文件或文件字节已全部发完：成功响应（或失败帧）完整送达，
    # 此时才允许关闭。
    closing.append(st)


def run(host, port):
    """创建非阻塞监听 socket 并运行 select 事件循环。"""
    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 所有 socket（含监听）都设为非阻塞。
    listen_sock.setblocking(False)
    # states 以 socket 为键保存每连接状态，便于事件回调定位。
    states = {}

    try:
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind((host, port))
        listen_sock.listen()
        print('select 多路复用服务器已启动，监听 %s:%d，共享目录 %s'
              % (host, port, server_iter.SHARED_DIR))

        while True:
            # 每轮按当前阶段重建监控集合：
            #   请求阶段只监控读；发送阶段只监控写。这样等请求的
            #   socket 不会进入 write_list 造成持续可写空转。
            read_set = [listen_sock]
            write_set = []
            for st in states.values():
                if st.phase in (PHASE_RECV_HEADER, PHASE_RECV_PATH):
                    read_set.append(st.sock)
                elif st.phase == PHASE_SEND:
                    write_set.append(st.sock)

            # 异常集合取读/写并集；select 阻塞直到有事件，Ctrl+C
            # 会中断它并抛出 KeyboardInterrupt。
            readable, writable, exceptional = select.select(
                read_set, write_set, list(set(read_set) | set(write_set)))

            # 本轮待关闭连接先收集，循环结束后统一处理，避免在遍历
            # 过程中修改 states。
            closing = []

            for s in readable:
                if s is listen_sock:
                    # 非阻塞 accept 循环取尽当前已完成握手的连接。
                    while True:
                        try:
                            conn, addr = listen_sock.accept()
                        except BlockingIOError:
                            break
                        conn.setblocking(False)
                        states[conn] = ConnectionState(conn, addr)
                        print('客户端接入：%s，纳入事件监控' % (addr,))
                elif s in states:
                    _on_readable(states[s], closing)

            for s in writable:
                if s in states:
                    _on_writable(states[s], closing)

            for s in exceptional:
                if s in states:
                    print('连接 %s 出现异常条件，关闭' % (states[s].addr,))
                    closing.append(states[s])

            # 统一释放并从状态表移除，资源关闭走唯一入口。
            for st in closing:
                if st.sock in states:
                    st.close()
                    del states[st.sock]
                    print('连接已关闭：%s' % (st.addr,))

    finally:
        # 停止（含 Ctrl+C）时释放全部客户端文件句柄、socket 以及
        # 监听 socket；不保证在途传输完整。
        for st in states.values():
            st.close()
        states.clear()
        listen_sock.close()
