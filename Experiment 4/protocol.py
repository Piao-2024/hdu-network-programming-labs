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
应用层协议定义与阻塞式 socket 辅助函数。

本模块只放跨实现稳定的协议常量和帧编解码逻辑：TCP 本身是没有消息
边界的字节流，一次 recv() 可能只返回半帧，也可能一次返回多帧，因此
凡是需要“恰好读取 N 字节”的地方都通过 recv_exact() 循环接收。

注意：这里的辅助函数按阻塞式 socket 编写，只适用于阶段一的迭代
服务器；后续 select/asyncio 等非阻塞模型不能直接复用 recv_exact()。
"""

import struct

# 默认监听/连接端口
DEFAULT_PORT = 9000

# 路径 UTF-8 编码后的最大字节数，同时也是请求体长度上限
MAX_PATH_BYTES = 4096

# 失败响应中错误信息 UTF-8 编码后的最大字节数
MAX_ERROR_BYTES = 4096

# 文件数据分块大小：64 KiB，发送和接收都按此粒度循环，避免一次性
# 把整个文件读入内存
BUFFER_SIZE = 64 * 1024

# 状态码：成功响应只携带状态码 0 和文件大小；失败响应携带非零
# 状态码和 UTF-8 错误信息
STATUS_SUCCESS = 0       # 成功
STATUS_NOT_FOUND = 1     # 文件不存在，或目标不是普通文件
STATUS_FORBIDDEN = 2     # 文件路径非法、越界或禁止访问
STATUS_IO_ERROR = 3      # 成功响应发出前识别到的服务器文件操作失败
STATUS_BAD_REQUEST = 4   # 请求帧错误：长度、编码等格式问题

# 协议中出现的全部非零状态码，recv_response() 用它识别未知状态码
VALID_ERROR_CODES = (
    STATUS_NOT_FOUND,
    STATUS_FORBIDDEN,
    STATUS_IO_ERROR,
    STATUS_BAD_REQUEST,
)

# struct 格式串，均带网络字节序（大端序）前缀 '!'：
#   !I  无符号 32 位整数，4 字节，用于路径长度/错误消息长度
#   !B  无符号 8 位整数，1 字节，用于状态码
#   !Q  无符号 64 位整数，8 字节，用于文件大小
FMT_LENGTH = '!I'
FMT_STATUS = '!B'
FMT_FILE_SIZE = '!Q'

LENGTH_FIELD_SIZE = struct.calcsize(FMT_LENGTH)     # 4
STATUS_FIELD_SIZE = struct.calcsize(FMT_STATUS)     # 1
FILE_SIZE_FIELD_SIZE = struct.calcsize(FMT_FILE_SIZE)  # 8


class ProtocolError(Exception):
    """协议帧错误。

    用于请求或响应帧格式不合法（长度字段越界、未知状态码等）、
    连接在收满一帧之前被对端关闭，以及严格 UTF-8 解码失败等情况。
    """


def recv_exact(sock, n):
    """从阻塞式 socket 恰好接收 n 字节，不足则循环 recv()。

    TCP 字节流不保留应用层消息边界，recv() 返回的字节数可能少于
    请求的数量，因此需要维护已收缓冲区反复读取，直到凑满 n 字节。
    若 recv() 返回 b'' 表示对端已正常关闭连接，此时帧尚未收满，
    抛出 ProtocolError 并注明尚缺字节数，便于定位不完整传输。
    """
    chunks = []
    received = 0
    while received < n:
        chunk = sock.recv(n - received)
        if chunk == b'':
            raise ProtocolError(
                '连接提前结束，尚缺 %d 字节（期望 %d，已收 %d）'
                % (n - received, n, received)
            )
        chunks.append(chunk)
        received += len(chunk)
    return b''.join(chunks)


def send_request(sock, file_path):
    """编码并发送一次下载请求：[4 字节路径长度 N][N 字节 UTF-8 路径]。

    路径先按 UTF-8 严格编码，长度必须落在 1～MAX_PATH_BYTES 之间；
    N=0 或超长属于客户端调用前就应拒绝的帧错误，这里直接抛出
    ProtocolError，不向服务器发送任何字节。sendall() 会阻塞到全部
    数据交给内核为止，避免短写造成的半包。
    """
    path_bytes = file_path.encode('utf-8')
    n = len(path_bytes)
    if n == 0 or n > MAX_PATH_BYTES:
        raise ProtocolError('路径长度必须为 1～%d 字节，实际为 %d'
                            % (MAX_PATH_BYTES, n))
    sock.sendall(struct.pack(FMT_LENGTH, n) + path_bytes)


def recv_request(sock):
    """读取并解码一次下载请求，返回文件路径字符串。

    先读 4 字节长度字段 N；N=0 或 N>MAX_PATH_BYTES 属于请求帧
    错误，此时直接抛出异常，不再读取请求体，避免对端宣称一个巨大
    的长度后迫使服务器分配/等待大量数据。长度合法时再读 N 字节并
    严格 UTF-8 解码，非法字节序列同样抛出 ProtocolError。
    """
    header = recv_exact(sock, LENGTH_FIELD_SIZE)
    (n,) = struct.unpack(FMT_LENGTH, header)
    if n == 0 or n > MAX_PATH_BYTES:
        raise ProtocolError('请求路径长度非法：N=%d' % n)
    body = recv_exact(sock, n)
    try:
        return body.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ProtocolError('请求路径不是合法 UTF-8：%s' % exc)


def send_success(sock, file_size):
    """发送成功响应头部：[1 字节状态码 0][8 字节文件大小]。

    文件数据不在本函数发送。调用方必须在已经成功打开文件并取得
    真实大小之后才能调用：一旦该头发送出去，后续传输再失败就无法
    改发错误状态码，客户端只能依据声明大小识别不完整传输。
    """
    sock.sendall(struct.pack(FMT_STATUS, STATUS_SUCCESS)
                 + struct.pack(FMT_FILE_SIZE, file_size))


def send_error(sock, code, message):
    """发送失败响应：[1 字节非零状态码][4 字节 M][M 字节 UTF-8 消息]。

    消息长度同样受 MAX_ERROR_BYTES 约束，收发双方执行同一限制，
    防止错误帧本身宣称过大的长度。M=0（空消息）是协议允许的。
    调用方传入的状态码必须是协议定义的非零状态码，消息文本应保持
    简短；若编码后超长，说明是服务端自身的构造缺陷，抛出
    ValueError 而不是悄悄截断出非法 UTF-8。
    """
    if code not in VALID_ERROR_CODES:
        raise ValueError('未知的错误状态码：%r' % code)
    message_bytes = message.encode('utf-8')
    if len(message_bytes) > MAX_ERROR_BYTES:
        raise ValueError('错误信息超过 %d 字节上限' % MAX_ERROR_BYTES)
    sock.sendall(struct.pack(FMT_STATUS, code)
                 + struct.pack(FMT_LENGTH, len(message_bytes))
                 + message_bytes)


def recv_response(sock):
    """读取服务器响应，返回三元组 (状态码, 文件大小或 None, 消息或 None)。

    状态码 0：再读 8 字节文件大小，返回 (0, size, None)，文件数据
    由调用方按声明大小自行分块接收。
    非零且为协议定义的状态码：读 4 字节消息长度 M，校验 M 不超过
    MAX_ERROR_BYTES（M=0 允许），再读 M 字节并严格 UTF-8 解码，
    返回 (code, None, message)。
    未知状态码、超长错误消息、非法 UTF-8 均属于响应帧错误，抛出
    ProtocolError。
    """
    status_raw = recv_exact(sock, STATUS_FIELD_SIZE)
    (code,) = struct.unpack(FMT_STATUS, status_raw)

    if code == STATUS_SUCCESS:
        size_raw = recv_exact(sock, FILE_SIZE_FIELD_SIZE)
        (file_size,) = struct.unpack(FMT_FILE_SIZE, size_raw)
        return STATUS_SUCCESS, file_size, None

    if code not in VALID_ERROR_CODES:
        raise ProtocolError('收到未知状态码：%d' % code)

    length_raw = recv_exact(sock, LENGTH_FIELD_SIZE)
    (m,) = struct.unpack(FMT_LENGTH, length_raw)
    if m > MAX_ERROR_BYTES:
        raise ProtocolError('错误消息长度超过上限：M=%d' % m)
    message_raw = recv_exact(sock, m)
    try:
        message = message_raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ProtocolError('错误信息不是合法 UTF-8：%s' % exc)
    return code, None, message
