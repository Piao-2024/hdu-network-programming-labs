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
统一的服务器命令行入口。

用法：
    python download_server.py [-p PORT] [--mode MODE] host

--mode 决定具体的服务器实现。iter（单线程迭代）、thread（多线程
并发）、select（非阻塞 I/O 多路复用）和 asyncio（协程）四种模式
均已实现，按选择懒加载对应模块。
"""

import argparse
import sys

import protocol


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='TCP 文件下载服务器入口')
    parser.add_argument('host', help='绑定的主机名或 IP 地址（如 127.0.0.1）')
    parser.add_argument('-p', '--port', type=int,
                        default=protocol.DEFAULT_PORT,
                        help='监听端口，默认 %d' % protocol.DEFAULT_PORT)
    parser.add_argument('--mode',
                        choices=['iter', 'thread', 'select', 'asyncio'],
                        default='iter',
                        help='服务器模型，默认 iter，可选 iter/thread/select/asyncio')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.mode == 'iter':
        # 懒加载：只有选中 iter 时才导入模块，避免为未实现模式付出
        # 导入代价，也避免模块缺失时在入口处直接报错。
        import server_iter
        try:
            server_iter.run(args.host, args.port)
        except KeyboardInterrupt:
            # Ctrl+C 停止服务器：run 内部的 with 会负责关闭监听
            # 套接字，这里只需友好提示。
            print('\n服务器已停止')
            return 0
        return 0

    if args.mode == 'thread':
        # 同样懒加载多线程模块，工作线程为 daemon，Ctrl+C 时进程
        # 直接结束，不保证仍在传输的下载优雅完成。
        import server_thread
        try:
            server_thread.run(args.host, args.port)
        except KeyboardInterrupt:
            print('\n服务器已停止')
            return 0
        return 0

    if args.mode == 'select':
        # 懒加载非阻塞多路复用模块，单线程事件循环；Ctrl+C 由其
        # finally 统一释放全部连接资源。
        import server_select
        try:
            server_select.run(args.host, args.port)
        except KeyboardInterrupt:
            print('\n服务器已停止')
            return 0
        return 0

    if args.mode == 'asyncio':
        # 懒加载协程模块，事件循环由 asyncio.run 管理；Ctrl+C
        # 触发后友好退出，不保证在途下载优雅完成。
        import server_asyncio
        try:
            server_asyncio.run(args.host, args.port)
        except KeyboardInterrupt:
            print('\n服务器已停止')
            return 0
        return 0

    # choices 已限定四种模式且均有分支，理论上不会走到这里。
    return 1


if __name__ == '__main__':
    sys.exit(main())
