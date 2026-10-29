# Experiment 3：RemoteBet

基于 Python 标准库 TCP Socket 的骰宝命令行游戏，适用于 macOS 终端。服务端负责预叫、实际骰子、判型和结算；客户端只负责交互与展示。当前为单活动客户端版本，采用已确认的规则 A1：每盘一注、净赢赔率、无余额、三币不兑换。

## 本机启动

在两个终端进入项目目录：

```sh
cd "/Users/piao/Backup/Code/Git/RemoteBet/Experiment 3"
```

终端一启动服务端：

```sh
uv run --no-project --no-managed-python --no-python-downloads --python /usr/local/bin/python3.13 python remote_bet_server.py
```

终端二启动客户端：

```sh
uv run --no-project --no-managed-python --no-python-downloads --python /usr/local/bin/python3.13 python remote_bet_client.py 127.0.0.1
```

使用本机已检查的 Python 3.13.7 路径，禁用项目发现和解释器下载，不创建 `.venv`，无需安装包。其他机器应替换为实际已安装的兼容解释器路径。上面的 uv 运行方式已用于完整测试；若本机 uv 缓存访问受权限限制，应在正常 macOS 终端运行，不需要修改系统或重装 Python。

需求中的简写 `uv run python remote_bet_server.py` 与 `uv run python remote_bet_client.py 127.0.0.1` 也对应这两个入口，但解释器选择取决于本机 uv 配置；推荐显式路径保证与测试环境一致。

默认 `127.0.0.1:5000`。端口占用时双方加同一 `--port 5123`；服务端可配置 `--host`。客户端 host 为位置参数。查看参数使用 `--help`。默认配置只监听本机，局域网配置见下节；不提供公网身份认证或加密。

## 局域网：Windows 服务端 + Mac 客户端

协议无需改变，服务端已有监听地址参数。Windows 电脑需有兼容的 Python（本项目已验证的版本为3.13.7），将 `remote_bet_server.py`、`game_logic.py`、`protocol.py` 放在同一目录，在该目录的命令提示符运行：

```text
python -B remote_bet_server.py --host 0.0.0.0 --port 5000
```

`0.0.0.0` 表示服务端监听全部 IPv4 网卡；也可用 Windows 的实际局域网IP限制监听网卡。默认 `127.0.0.1` 仅允许服务端所在电脑连接，不能用于另一台电脑访问。

在 Windows 用 `ipconfig` 查看正在使用的网卡的 IPv4 地址，例如 `192.168.1.20`。若 Windows 防火墙拦截连接，允许该 Python 程序在专用网络通信，或按实际使用的TCP端口设置入站允许规则；不需要关闭整个防火墙。两台电脑须能互相访问，访客Wi-Fi/无线客户端隔离可能阻止连接。

Mac 上启动客户端时省略地址参数：

```sh
uv run --no-project --no-managed-python --no-python-downloads --python /usr/local/bin/python3.13 python remote_bet_client.py
```

看到提示后输入 Windows 的实际地址：

```text
服务端 IP:端口 [127.0.0.1:5000]（quit退出）> 192.168.1.20:5000
```

输入格式不正确会重新提示；直接回车使用提示中的本机默认地址，`--port`可改变这个默认端口。输入完整地址时，以输入的端口为准。输入`quit`或按Ctrl-D/Ctrl-C取消连接。交互输入目前支持IPv4，不接收URL或IPv6。不要把监听用的 `0.0.0.0` 或自己Mac的 `127.0.0.1` 当作Windows电脑的地址。

也可保持原来的直接连接方式：`remote_bet_client.py 192.168.1.20 --port 5000`。同一时间仍只服务一个客户端。当前协议无登录和加密，适用于可信局域网，不作公网服务使用。

已验证交互地址输入、原CLI参数和本机真实TCP流程；用户已完成Windows服务端（10.150.181.193）与Mac客户端的跨机器连接验证；自动化测试仍在Mac上执行。如果连接失败，先核对服务端是否启动、监听地址、双方端口、Windows入站规则及网络隔离。

## 玩法

```text
ya <玩法代码> <数量> <coin|silver|gold>
```

| 代码 | 玩法 | 条件 | 净赢倍数 |
|---|---|---|---|
| tc | 头彩 | 与预叫点数及顺序一致 | 35 |
| dc | 大彩 | 与预叫点数一致，顺序不限 | 17 |
| kp | 空盘 | 两枚均偶数且不同 | 5 |
| qx | 七星 | 点数之和为七 | 5 |
| dd | 单对 | 两枚均奇数，不要求相等 | 3 |
| sx | 散星 | 点数之和为3、5、9或11 | 2 |

上表是摘要，精确判型和交叠资格以 [游戏规则](docs/game-rules.md) 为准。按表顺序选择最终点型，头彩额外保留大彩资格；低优先级的其他条件即使成立也不获奖。每盘只押一种玩法。

例如 `ya tc 10 coin`：若头彩中奖，总返还360 coin、净赢350 coin；未中净亏10 coin。数量限1～1,000,000整数，三币不兑换，不维护账户或初始余额。结果后自动新开盘，继续下注或输入 `quit`；Ctrl-D/Ctrl-C在输入提示处也会尽力正常退出。服务端用Ctrl-C停止。

非法下注不消耗当前盘；断线不自动重发、不恢复会话。请求发出但结果未收到时会提示“结果可能未知”。阻塞在终端输入期间，断线可能到下一次提交时才被发现。单客户端服务端会串行处理连接，后续连接可能等待或超时。

## 测试

```sh
uv run --no-project --no-managed-python --no-python-downloads --python /usr/local/bin/python3.13 python -B -m unittest discover -s tests -v
```

测试包含1296组规则组合、金额边界、JSON Lines分片/粘连、错误恢复、真实回环TCP的100盘以及独立服务端/客户端进程。常规网络测试使用操作系统分配的临时端口；另有一个uv启动冒烟测试显式验证默认5000，若端口已有其他应用占用则跳过该项；沙箱环境可能需要回环网络权限。详细覆盖和实测记录见 [测试方案](docs/testing.md)。

## 文件

- `game_logic.py`：命令校验、抽样、纯判型及结算。
- `protocol.py`：统一消息定义、UTF-8 JSON Lines、帧缓冲及完整读取。
- `remote_bet_server.py` / `remote_bet_client.py`：服务端与CLI入口。
- `tests/`：仅标准库 unittest 的自动化测试。

技术文档：[需求](docs/requirements.md)、[架构](docs/architecture.md)、[协议](docs/protocol.md)、[规则](docs/game-rules.md)、[测试](docs/testing.md)、[实施计划](docs/implementation-plan.md)、[开发记录](docs/development-log.md)。
