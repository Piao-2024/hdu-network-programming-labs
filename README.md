# HDU Network Programming Labs

杭州电子科技大学（HDU）网络编程设计实践课程实验仓库，收录实验2、实验3和实验4。

## 实验目录

| 目录 | 内容 | 状态 |
|---|---|---|
| [Experiment 3](<Experiment 2/README.md>) | RemoteBet：基于TCP的远程骰宝命令行游戏 | 已实现 |
| Experiment 2 | 实验2 | 预留，待上传 |
| Experiment 4 | 实验4 | 预留，待上传 |

```text
hdu-network-programming-labs/
├── Experiment 2/
│   ├── README.md
│   ├── docs/
│   ├── tests/
│   ├── protocol.py
│   ├── game_logic.py
│   ├── remote_bet_server.py
│   └── remote_bet_client.py
├── Experiment 3/
│   └── .gitkeep
├── Experiment 4/
│   └── .gitkeep
├── .gitignore
└── README.md
```

实验3、4目录中的`.gitkeep`仅用于让Git保存空目录，上传实验内容后可以删除。

## 实验2启动与测试

先进入`Experiment 2`目录，再按照该目录的[README](<Experiment 2/README.md>)启动服务端、客户端或运行测试。目录名包含空格，在终端中需加引号。例如，在仓库根目录执行：

```sh
cd "Experiment 2"
```

实验2使用Python标准库和TCP Socket，支持本机及局域网客户端/服务端连接。详细规则、协议、运行方式和测试说明见实验2文档。

## 协作约定

每位同学将自己的实验代码、说明文档及相关文件放到对应实验目录内，保留其他实验内容。实验目录应包含独立README，说明实验题目、环境要求、启动步骤和测试方式。GitHub协作者由仓库所有者另行邀请。
