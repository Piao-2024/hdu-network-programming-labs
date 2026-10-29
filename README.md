# HDU Network Programming Labs

杭州电子科技大学（HDU）网络编程设计实践课程实验仓库，收录实验2、实验3和实验4。

## 实验目录

| 目录 | 内容 | 状态 |
|---|---|---|
| Experiment 2 | 实验2 | 预留，待上传 |
| [Experiment 3](<Experiment 3/README.md>) | RemoteBet：基于 TCP 的远程骰宝命令行游戏 | 已实现 |
| [Experiment 4](<Experiment 4>) | TCP 文件下载服务：iter/thread/select/asyncio 四种服务器模型 | 已实现 |

```text
hdu-network-programming-labs/
├── Experiment 2/
│   └── .gitkeep
├── Experiment 3/
│   ├── README.md
│   ├── docs/
│   ├── tests/
│   ├── protocol.py
│   ├── game_logic.py
│   ├── remote_bet_server.py
│   └── remote_bet_client.py
├── Experiment 4/
│   ├── protocol.py
│   ├── download_client.py
│   ├── download_server.py
│   ├── server_iter.py
│   ├── server_thread.py
│   ├── server_select.py
│   └── server_asyncio.py
├── .gitignore
└── README.md
```

实验2目录中的`.gitkeep`仅用于让 Git 保存空目录，上传实验内容后可以删除。

## 实验3启动与测试

先进入`Experiment 3`目录，再按照该目录的[README](<Experiment 3/README.md>)启动服务端、客户端或运行测试。目录名包含空格，在终端中需加引号。例如，在仓库根目录执行：

```sh
cd "Experiment 3"
```

实验3使用Python标准库和TCP Socket，支持本机及局域网客户端/服务端连接。详细规则、协议、运行方式和测试说明见实验3文档。

## 实验4启动与测试

先进入`Experiment 4`目录（目录名包含空格，终端中需加引号）：

```sh
cd "Experiment 4"
```

实验4仅使用 Python 标准库实现 TCP 文件下载服务。终端一启动服务器（以 thread 模式、端口 9100 为例）：

```sh
python download_server.py -p 9100 --mode thread 127.0.0.1
```

另开终端，用客户端下载共享目录中的文件：

```sh
python download_client.py -p 9100 127.0.0.1 test.txt downloads\test.txt
```

`--mode` 可选 `iter`、`thread`、`select`、`asyncio` 四种服务器模型，应用层协议、错误状态码与 64 KiB 分块在四种模型间保持一致。服务器可下载文件统一放在 `shared_files` 目录，客户端下载结果保存到 `downloads` 目录。

## 协作约定

每位同学将自己的实验代码、说明文档及相关文件放到对应实验目录内，保留其他实验内容。实验目录应包含独立README，说明实验题目、环境要求、启动步骤和测试方式。GitHub协作者由仓库所有者另行邀请。
