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
多线程并发 TCP 文件下载服务器（thread-per-connection 模型）。

职责分工：
  - 主线程只做 accept()，每接到一个连接就创建一个工作线程并立刻
    回到 accept()，因此某个客户端在阻塞读取/慢速传输时不会拖住
    主线程接收新连接；
  - 工作线程复用 server_iter.handle_client() 完成协议处理、路径
    校验和文件发送，本模块不重复实现这些逻辑。

每个连接使用独立的 socket 和独立的文件句柄，线程之间没有需要
保护的共享可变状态，因此无需加锁。
"""

import socket
import threading

import server_iter


def _serve_connection(conn, addr):
    """工作线程入口：接管 conn 的所有权并负责最终关闭。

    handle_client 内部已经隔离了单连接上可预期的协议错误和大部分
    OSError；这里再兜一层 OSError，保证任何单连接失败都只结束本
    线程，不会冒泡到主线程的 accept 循环。
    """
    thread_name = threading.current_thread().name
    print('[%s] 开始服务客户端 %s' % (thread_name, addr))
    try:
        # 直接复用迭代服务器的连接处理逻辑：阻塞式 recv/send 在
        # 独立线程中执行，线程阻塞不影响其他连接。
        server_iter.handle_client(conn)
    except OSError as exc:
        print('[%s] 连接 %s 处理异常：%s' % (thread_name, addr, exc))
    finally:
        # 连接所有权随派发移交给工作线程，只有该线程可以关闭它；
        # 主线程 start() 之后绝不能再触碰这个 socket。
        conn.close()
        print('[%s] 连接已关闭：%s' % (thread_name, addr))


def run(host, port):
    """创建监听套接字，主线程循环 accept 并向工作线程派发。"""
    # with 保证主线程退出（含 Ctrl+C 中断）时监听 socket 被关闭。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listen_sock:
        # 重启后快速重新绑定，避免 TIME_WAIT 导致端口暂时不可用。
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind((host, port))
        listen_sock.listen()
        print('多线程服务器已启动，监听 %s:%d，共享目录 %s'
              % (host, port, server_iter.SHARED_DIR))

        while True:
            # 主线程在 accept 上阻塞；新连接到达后只做“创建线程 +
            # 启动”这一轻量动作，随后立即回到下一次 accept。
            conn, addr = listen_sock.accept()
            print('客户端接入：%s，派发工作线程' % (addr,))
            worker = threading.Thread(
                target=_serve_connection,
                args=(conn, addr),
                # daemon=True：线程随主进程一起退出，Ctrl+C 后无需
                # 逐个 join。边界是进程停止时仍在传输的下载不保证
                # 传完，客户端会按声明大小识别为不完整传输。
                daemon=True,
            )
            worker.start()
            # 注意：此处不能 close(conn)。socket 句柄已交给工作线程，
            # 主线程关闭会导致工作线程的连接失效。
