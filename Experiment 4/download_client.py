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
TCP 文件下载客户端，四种服务器模型共用。

用法：
    python download_client.py [-p PORT] host remote_file local_file

下载策略是“先写同目录临时文件，完整收满后再原子替换”：任何网络、
协议或本地写入失败都只会清理本次产生的临时文件，不会截断用户原有
的目标文件。
"""

import argparse
import os
import socket
import sys
import tempfile

import protocol


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='基于 TCP 的文件下载客户端')
    parser.add_argument('host', help='服务器主机名或 IP 地址')
    parser.add_argument('remote_file', help='服务器共享目录下的相对文件路径')
    parser.add_argument('local_file', help='本地保存路径（含文件名）')
    parser.add_argument('-p', '--port', type=int,
                        default=protocol.DEFAULT_PORT,
                        help='服务器端口，默认 %d' % protocol.DEFAULT_PORT)
    return parser.parse_args(argv)


def download(host, port, remote_file, local_file):
    """执行一次完整下载，返回进程退出码（0 成功，1 失败）。"""
    # tmp_path 记录本次下载专用的临时文件；只有它是本函数自己创建的，
    # 清理时也只允许删除它，绝不能触碰下载前已存在的 local_file。
    tmp_path = None
    try:
        # create_connection 内部完成 getaddrinfo 与 connect，with 语句
        # 保证退出时（含异常）关闭 TCP 连接，释放本端套接字资源。
        with socket.create_connection((host, port)) as sock:
            protocol.send_request(sock, remote_file)
            code, file_size, message = protocol.recv_response(sock)

            if code != protocol.STATUS_SUCCESS:
                # 失败响应：明确告知状态码与服务器原因，此时尚未创建
                # 任何本地文件，用户原有目标文件保持原样。
                print('下载失败，状态码 %d：%s' % (code, message))
                return 1

            # 目标目录：临时文件必须与最终文件位于同一目录，os.replace
            # 才是同卷原子替换。父目录不存在时 mkstemp 会抛出
            # FileNotFoundError，本阶段不自动创建目录。
            target_dir = os.path.dirname(os.path.abspath(local_file))
            fd, tmp_path = tempfile.mkstemp(
                dir=target_dir, prefix='.download_', suffix='.tmp')

            # mkstemp 已打开底层句柄，用 os.fdopen 接管为带缓冲的
            # 二进制文件对象，with 保证它被关闭，避免句柄泄漏。
            with os.fdopen(fd, 'wb') as out_file:
                remaining = file_size
                while remaining > 0:
                    # 每次最多索取“剩余字节数”和 64 KiB 中的较小值，
                    # 保证累计接收量不会超过协议声明的文件大小。
                    chunk = sock.recv(min(remaining, protocol.BUFFER_SIZE))
                    if chunk == b'':
                        # 对端在收满声明大小之前关闭连接：服务器可能
                        # 在发出成功头后崩溃或文件被缩短。
                        print('传输不完整：连接提前关闭，期望 %d 字节，'
                              '尚缺 %d 字节' % (file_size, remaining))
                        return 1
                    out_file.write(chunk)
                    remaining -= len(chunk)

            # 能走到这里说明数据已完整写入且文件已关闭。Windows 上
            # os.replace 要求源文件句柄先关闭，上面的 with 已保证。
            # 替换是原子操作：目标要么是旧文件、要么是完整新文件。
            os.replace(tmp_path, local_file)
            # 替换成功后临时路径已不存在，置空以防 finally 再清理。
            tmp_path = None

        print('已下载 %s，共 %d 字节' % (local_file, file_size))
        return 0

    except protocol.ProtocolError as exc:
        # 帧格式错误、连接在收满一帧前结束、UTF-8 解码失败等。
        print('协议错误：%s' % exc)
        return 1
    except OSError as exc:
        # 连接/发送失败、本地目录或写入失败等系统级错误。
        print('网络或文件操作错误：%s' % exc)
        return 1
    finally:
        # 只有下载未走完 os.replace 时 tmp_path 仍非空，此时删除本次
        # 产生的临时文件；清理本身失败只提示，不掩盖原始错误。
        if tmp_path is not None and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError as exc:
                print('警告：临时文件清理失败 %s：%s' % (tmp_path, exc))


def main(argv=None):
    args = parse_args(argv)
    return download(args.host, args.port, args.remote_file, args.local_file)


if __name__ == '__main__':
    sys.exit(main())
