# 开发记录

计划：docs/implementation-plan.md；规格：docs/requirements.md、game-rules.md、protocol.md。

用户已明确授权开发，并确认采用方案 A；规则版本为 A1。

执行方式：按 executing-plans 和 TDD 在本会话逐层执行，不安装依赖、不改Python环境、不提交/推送。

Ruling: 在当前目录工作，不创建worktree — 仓库尚无HEAD，且用户指定此目录、禁止另建项目和提交 — 代价是改动留在当前工作区。

预检：规则层产生Bet/Classification/Settlement；服务端负责将其组成protocol定义的Message；客户端仅展示，不依赖game_logic。协议字段没有发现未解决冲突。

## 任务与验证

- 阶段0：规则方案A已确认，开发已授权。
- 阶段1：规则模块及确定性测试，先RED再GREEN。
- 阶段2：协议及分片/粘连/超时测试，先RED再GREEN。
- 阶段3：服务端会话和错误恢复测试，先RED再GREEN。
- 阶段4：客户端呈现与响应状态测试，先RED再GREEN。
- 阶段5：实际本机TCP、双进程端到端、100盘及异常验证。
- 阶段6：最终审查、README和文档同步，不提交或发布。

阶段1完成：先运行测试确认game_logic缺失导致RED，再实现；7个测试通过，含1296组×六玩法和金额/币种边界。

阶段2完成：协议缺失时RED；实现后15个总测试通过。修正一个测试时钟夹具：原夹具在超时之前制造EOF，按实际调用次序固定t=0/6/10后验证半帧期限不重置。

阶段3完成：服务端缺失时RED；实现serve_session及监听入口后20个总测试通过。socketpair验证固定抽样、错误不耗盘、编号消耗、重复/过期请求、退出和断连清理。

阶段4完成：客户端缺失时RED；实现后28个总测试通过。首次执行发现round_open没有request_id而通用响应检查要求它，已依照协议用盘号关联；骰面测试只统计图案行，避免把coin中的字母o计入点数。

阶段5完成：真实回环TCP和两个进程E2E已执行；沙箱内监听失败后获授权原命令重试通过。uv缓存访问限制同样获授权后原命令通过，无安装/环境变更。

最终独立审查：未发现Critical；发现完整帧迟到绕过deadline的Important问题，以及编号耗尽和未发送却称结果未知两项。后两项按用户可观察的协议结束契约/诊断真实性提升为必须修复，均已补回归先RED再GREEN。

Final: fixed 完整帧迟到 — test_completed_frame_arriving_after_deadline_is_rejected RED→GREEN；并保留及时缓冲帧的接收时刻。
Final: fixed ID耗尽 — test_exhausted_request_id_closes_even_after_invalid_bet RED→GREEN；MAX_ID的bet在业务前拒绝，quit可正常退出。
Final: fixed 未发送状态误报 — test_unsent_oversized_input_is_not_an_unknown_result RED→GREEN；仅编码失败清除待定标记，发送错误仍保留结果未知。

Final: Ruling: 审阅未评估的统计/商业公平、余额/恢复/并发/远程部署维持明确排除范围，服务端结算不由客户端重算；其他Python版本和手工TTY验收不作通过声明 — 遵循当前规格和实测边界 — 如需这些能力应追加需求与验证。

阶段6完成：README含uv实际启动方式、规则摘要、退出、测试及限制；六份文档同步A1确认及当前实现状态。最后完整uv套件42/42通过，0跳过；100盘max=0.000178秒、mean=0.000107秒。具体证据与覆盖映射在testing.md第7节。所有改动保留在当前工作区，不执行commit/push或清理未提交文件。

## 局域网地址输入扩展

用户明确要求：Windows运行server、Mac运行client，client启动后输入IP:port。

设计：无host参数才提示输入IPv4:端口；空输入使用127.0.0.1及--port默认端口；非法输入重试；quit/EOF/Ctrl-C无连接退出；保留host/--port原用法。服务端已支持--host，无需改协议或规则。仅更新客户端、测试和相关文档。

验证：先新增测试并观察旧实现不提示输入、缺少parse_endpoint导致RED；实现后47项测试通过，包括实际TCP端到端输入地址流程。尚未在Windows或另一台实体电脑上运行，README提供操作前提和启动说明；不自动修改防火墙或将本机服务暴露到局域网。

## 课程仓库整理

按用户要求将实验3的RemoteBet代码、测试和文档移到Experiment 3，Experiment 2和Experiment 4仅保留.gitkeep。仓库首页改为课程实验索引。移动后从Experiment 3目录执行原uv命令，47个测试全部通过。

用户要求代码首部声明不提交：本地源码保留声明，Git提交中的源码去除该注释前缀；程序语法树保持一致。未跟踪的Code.zip不纳入提交。
