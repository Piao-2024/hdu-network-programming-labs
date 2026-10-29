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
基于 asyncio Streams API 的协程式 TCP 文件下载服务器。

事件循环在 await 处挂起当前协程并转去执行其他已就绪的协程：
客户端 A 慢速发送请求或慢速接收文件时，处理 A 的协程被挂起，
事件循环仍可调度客户端 B 的协程，因此单线程即可交错处理多个
连接，且不需要像 select 那样手写显式连接状态机——协程的局部
变量在挂起期间自动保留了每连接状态。

边界：网络 I/O 由协程异步调度；普通磁盘文件的 open()/read()
仍是同步调用，可能短暂占用事件循环，本阶段不引入线程池或
Python 3.9 才有的 asyncio.to_thread()。
"""

import asyncio
import os
import struct

import protocol
import server_iter


async def _write_error(writer, code, message):
    """把失败响应帧写入流并等待一次 drain。

    不能调用 protocol.send_error()（它内部是阻塞式 sendall），只
    复用 protocol 的常量与格式自行编码。若对端已断开，drain 会
    抛出 OSError，由外层统一处理，本函数不吞掉该错误。
    """
    message_bytes = message.encode('utf-8')
    writer.write(struct.pack(protocol.FMT_STATUS, code)
                 + struct.pack(protocol.FMT_LENGTH, len(message_bytes))
                 + message_bytes)
    # write 只是把字节放入传输层缓冲，drain 才是背压协调点。
    await writer.drain()


async def handle_client(reader, writer):
    """处理一个连接的完整下载流程；每连接一个协程。"""
    addr = writer.get_extra_info('peername')
    print('客户端接入：%s' % (addr,))
    # file_obj 在 finally 中统一关闭，初始为 None 便于判断。
    file_obj = None

    try:
        # 1. 读取 4 字节长度头。readexactly 会在数据不足时挂起，
        # 凑满后才返回；对端提前关闭则抛 IncompleteReadError。
        try:
            header = await reader.readexactly(protocol.LENGTH_FIELD_SIZE)
        except asyncio.IncompleteReadError:
            print('请求长度头不完整：%s' % (addr,))
            await _write_error(writer, protocol.STATUS_BAD_REQUEST,
                               '请求长度头不完整')
            return

        (n,) = struct.unpack(protocol.FMT_LENGTH, header)
        # 长度非法：不继续读请求体，直接状态码 4。
        if n == 0 or n > protocol.MAX_PATH_BYTES:
            print('请求路径长度非法 N=%d：%s' % (n, addr))
            await _write_error(writer, protocol.STATUS_BAD_REQUEST,
                               '请求路径长度非法')
            return

        # 2. 读取 N 字节路径并严格 UTF-8 解码。
        try:
            body = await reader.readexactly(n)
        except asyncio.IncompleteReadError:
            print('请求路径不完整：%s' % (addr,))
            await _write_error(writer, protocol.STATUS_BAD_REQUEST,
                               '请求路径不完整')
            return

        try:
            remote_file = body.decode('utf-8')
        except UnicodeDecodeError as exc:
            print('请求路径不是合法 UTF-8：%s' % exc)
            await _write_error(writer, protocol.STATUS_BAD_REQUEST,
                               '请求路径编码非法')
            return

        # 3. 路径安全校验复用现有集中实现（状态码 2）。
        candidate = server_iter._resolve_safe_path(remote_file)
        if candidate is None:
            print('拒绝非法或越界路径：%r' % remote_file)
            await _write_error(writer, protocol.STATUS_FORBIDDEN,
                               '文件路径非法或越界')
            return

        # 不存在或不是普通文件：状态码 1。
        if not candidate.is_file():
            print('文件不存在或不是普通文件：%r' % remote_file)
            await _write_error(writer, protocol.STATUS_NOT_FOUND,
                               '文件不存在或不是普通文件')
            return

        # 4. 成功头准备前，打开文件与读取元数据处于同一错误分支，
        # 失败按状态码 3 回送；open 已成功而 fstat 失败时先关闭句柄。
        opened = None
        try:
            opened = open(candidate, 'rb')
            # 对已打开描述符 fstat 取真实大小，避免 getsize 与 open
            # 之间文件被替换造成的大小不一致。
            file_size = os.fstat(opened.fileno()).st_size
        except OSError as exc:
            print('文件打开或元数据读取失败：%s' % exc)
            if opened is not None:
                opened.close()
            await _write_error(writer, protocol.STATUS_IO_ERROR,
                               '服务器文件打开或读取失败')
            return
        file_obj = opened

        # 5. 发送 9 字节成功头；成功响应一旦开始，后续就不能再
        # 插入任何失败状态码。
        writer.write(struct.pack(protocol.FMT_STATUS, protocol.STATUS_SUCCESS)
                     + struct.pack(protocol.FMT_FILE_SIZE, file_size))
        await writer.drain()

        # 6. 分块读取并发送文件，每块最多 64 KiB，累计严格等于
        # 声明大小，不一次性读入整个文件。
        remaining = file_size
        while remaining > 0:
            chunk = file_obj.read(
                min(protocol.BUFFER_SIZE, remaining))
            if not chunk:
                # 文件传输期间被缩短：无法补齐声明大小，不能补零、
                # 也不能再发错误帧，只结束连接，客户端判定不完整。
                print('文件传输期间被缩短，已处理 %d/%d，结束连接'
                      % (file_size - remaining, file_size))
                return
            writer.write(chunk)
            # drain 感知对端接收速度：对端停读导致缓冲积压时，
            # 这里挂起而事件循环继续服务其他客户端。
            await writer.drain()
            remaining -= len(chunk)

    except OSError as exc:
        # 对端断开/重置、成功头之后的写入或文件读取 OSError 都只
        # 影响当前协程；不捕获 CancelledError（它继承 BaseException）。
        print('连接 %s 处理失败，仅关闭本连接：%s' % (addr, exc))
    finally:
        # 文件句柄与连接都由本协程负责释放。
        if file_obj is not None:
            file_obj.close()
        writer.close()
        # Python 3.8 支持 wait_closed()；对端已断开时若抛 OSError
        # 忽略即可，不掩盖流程。
        try:
            await writer.wait_closed()
        except OSError:
            pass
        print('连接已关闭：%s' % (addr,))


async def _serve(host, port):
    """异步入口：创建服务器并永久服务，直到被 Ctrl+C 中断。"""
    # start_server 每接受一个连接就自动调度一个 handle_client 协程，
    # 无需手动创建线程或 select 循环。
    server = await asyncio.start_server(handle_client, host, port)
    # 从实际监听 socket 取绑定地址，便于日志核对。
    bound = server.sockets[0].getsockname()
    print('asyncio 协程服务器已启动，监听 %s，共享目录 %s'
          % (bound, server_iter.SHARED_DIR))
    # async with 保证退出时关闭服务器；serve_forever 持续运行。
    async with server:
        await server.serve_forever()


def run(host, port):
    """同步入口：asyncio.run 负责创建和关闭事件循环。"""
    asyncio.run(_serve(host, port))
