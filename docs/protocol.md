# TCP 应用层协议 v1（A1实现）

本文是客户端和服务端共同的消息定义源。消息形状已由 protocol.py 实现；`ruleset` 的游戏语义为用户确认的 A1，见 [game-rules.md](game-rules.md)。字段禁止在其他文档另起一套命名。

## 1. 传输和帧

- TCP 是有序字节流，一次 `recv()` 可能只给出半条消息，也可能给出多条，不能把一次接收当成一条消息。
- 采用 UTF-8 JSON Lines，每帧为一个 JSON 对象的紧凑表示，加一个字节 LF（`0x0A`）；无 BOM。发送使用完整发送语义（如 `sendall`）。序列化禁止 NaN/Infinity，严格解码禁止重复键。
- JSON 字符串中的换行编码为转义 `\n`，不能出现裸 LF；提示文本可含转义换行，command 字段则禁止 CR/LF。仅以帧结束 LF 切分，不对文本做整行 strip 后再解析。
- 帧上限为 8192 字节，包含末尾 LF；JSON 负载上限 8191 字节。接收块建议 4096 字节。缓冲按字节保存，找到 LF 后再严格 UTF-8 解码，避免拆开多字节中文导致误判。
- 每次追加后按 LF 依次取出全部完整帧，逐帧检查长度、解码和分派；留下最后的未完成片段，供下次调用读取。尾部未完成片段达到 8192 字节且仍无 LF，已无合法完成可能，应立即关闭。合法的多帧合并长度可超过 8192，不应仅因总缓冲超过单帧上限就拒绝。
- 空行不是合法消息。CRLF 不作为另一种约定，负载末尾裸 CR 也拒绝。JSON 字符串内部合法转义的 CR 不影响分帧。
- EOF 且缓冲为空：正常传输结束；除已收到 bye 的主动退出外，客户端视为意外断开。EOF 且缓冲非空：截断帧，丢弃、关闭，不尝试补 JSON。socket 错误同样终止当前连接。

方案比较：长度前缀同样正确，但人工观察与本地调试更繁琐；纯文本不利于结果字段和错误关联。选 JSON Lines，双方不混用裸文本欢迎或裸文本下注。

## 2. 信封与严格类型

每条消息必含 `version: 1` 和字符串 `type`。下表列出的字段全部必需，没有额外可选字段；不认识的字段拒绝。JSON integer 不接受布尔值或 1.0。所有标识符使用整数，范围 1～2,147,483,647。达到编号上限须结束会话，不回绕：request_id为上限的quit可正常退出，bet在业务处理前返回致命INTERNAL_ERROR（本注未处理）；round_id为上限的合法下注可结算，结果后以致命INTERNAL_ERROR结束，不再开盘。此编号耗尽处理优先于盘号及命令业务校验。

- `request_id`：客户端会话内严格递增的新请求编号，bet、quit 共用序列；首个可为 1。重复或倒退以 DUPLICATE_REQUEST 拒绝；不缓存重放结果，不提供断线幂等恢复。
- 服务端对通过信封校验的新请求，先登记 request_id，再检查状态和业务；业务失败也已消耗该编号。无法通过信封校验的请求不登记。客户端每次提交新命令使用新编号。
- `round_id`：服务端会话内递增盘号，从 1 开始，只有成功下注后才加一；不同连接编号可重复，不可跨连接引用。
- `ruleset`：已确认方案 A 使用 `A1`，含义为规则文档的单注、资格和净赔语义。A1已由当前实现使用。客户端仅接受自身支持的标识，不静默忽略未知规则。

## 3. 消息目录

方向 S→C 为服务端到客户端；C→S 相反。以下金额均为整数，币种和玩法枚举只在规则文档定义。

| type / 方向 | 除 version/type 外的必要字段 | 约束与用途 |
|---|---|---|
| welcome / S→C | ruleset:string, message:string | 首条消息，说明游戏与规则标识；message ≤1024 UTF-8 字节 |
| round_open / S→C | round_id:integer, predicted_dice:array, message:string | 两个 1～6 整数构成有序数组；message ≤1024 字节，含提示；客户端可本地呈现玩法表 |
| bet / C→S | request_id:integer, round_id:integer, command:string | command 为原始下注命令；长度及内容属于下述业务校验，不属于信封类型校验 |
| round_result / S→C | request_id:integer, round_id:integer, predicted_dice:array, actual_dice:array, matched_types:array, final_type:string, eligible_types:array, play:string, amount:integer, currency:string, outcome:string, multiplier:integer, payout:integer, net_change:integer | 所有字段由服务端生成，详情如下 |
| error / S→C | request_id:integer或null, round_id:integer或null, code:string, message:string, fatal:boolean | 无可靠请求编号时 null；round_id 表示服务端当前盘号，尚无开盘时 null；message ≤1024 字节 |
| quit / C→S | request_id:integer | 与当前盘无关，OPEN 时允许 |
| bye / S→C | request_id:integer, message:string | 对 quit 的确认，message ≤1024 字节，发送后关闭 |

round_result：actual_dice 同预叫数组约束；matched_types 无重复，按规则表顺序；final_type 为六玩法之一或 banker；eligible_types 为规则算出的资格列表；play/amount/currency 为规范化后的本注；outcome 仅 win/lose；multiplier 是所押玩法赔率数字。payout 非负整数；net_change 为有符号整数。最大数量及 35 倍净赔决定方案 A 下 payout≤36,000,000，|net_change|≤35,000,000。result 不含余额，也不包含客户端自行推算出的数据。客户端做结构和关联检查，不重算中奖来覆盖服务端。

校验层次例外须明确：bet 的 command 在协议层只检查其存在且为字符串；256 字节限制、CR/LF 禁令及命令语法由 parse_bet 校验，失败为可恢复 INVALID_COMMAND，而不是致命 INVALID_MESSAGE。协议层仍先执行整帧长度上限。客户端编码器同样不预先拒绝这些业务内容，以便服务器统一裁决。round_result 的枚举和金额结构边界由协议层检查，中奖资格和金额的业务生成由游戏逻辑负责。

## 4. 完整消息示例

示例遵循已确认方案 A，所有行末实际带 LF。以下是同一连接：welcome、开盘、押注、结果、下一盘、业务错误、退出及确认；没有隐藏的 continue 消息。

```json
{"version":1,"type":"welcome","ruleset":"A1","message":"欢迎来到 RemoteBet\n输入 quit 退出"}
{"version":1,"type":"round_open","round_id":1,"predicted_dice":[6,1],"message":"开盘：ya <玩法代码> <下注数量> <coin|silver|gold>"}
{"version":1,"type":"bet","request_id":1,"round_id":1,"command":"ya tc 10 coin"}
{"version":1,"type":"round_result","request_id":1,"round_id":1,"predicted_dice":[6,1],"actual_dice":[6,1],"matched_types":["tc","dc","qx"],"final_type":"tc","eligible_types":["tc","dc"],"play":"tc","amount":10,"currency":"coin","outcome":"win","multiplier":35,"payout":360,"net_change":350}
{"version":1,"type":"round_open","round_id":2,"predicted_dice":[5,2],"message":"下一盘已开盘，请下注或 quit"}
{"version":1,"type":"bet","request_id":2,"round_id":2,"command":"ya qx 0 coin"}
{"version":1,"type":"error","request_id":2,"round_id":2,"code":"INVALID_AMOUNT","message":"数量须在允许范围内","fatal":false}
{"version":1,"type":"quit","request_id":3}
{"version":1,"type":"bye","request_id":3,"message":"已退出 RemoteBet"}
```

致命错误示例（无法信任原始请求编号时）：

```json
{"version":1,"type":"error","request_id":null,"round_id":1,"code":"INVALID_ENCODING","message":"消息必须为 UTF-8","fatal":true}
```

## 5. 请求响应和状态约定

连接成功服务端无须等客户端握手，依次发送 welcome、round_open。客户端校验 version/ruleset 后才展示下注提示。只允许一项未完成请求，不主动流水线发送。

| 请求/事件 | 服务器动作 | 客户端期待 |
|---|---|---|
| 合法 bet（OPEN） | OPEN→SETTLING→发送 round_result→新盘 OPEN 并发送 round_open | 先收关联结果，再收新盘，收到两者后才允许下一次输入 |
| 业务无效 bet | 一条非致命 error，当前盘仍 OPEN | 显示错误，在同一盘重新输入，不等待新开盘 |
| 重复请求或盘号过期 | 一条非致命 error，不掷骰 | 同上；客户端已有当前 round_open 信息，不用错误中的盘号伪造预叫 |
| OPEN 收到 quit | 发送 bye，关闭连接 | 校验 request_id 后退出 |
| 致命错误 | 尽力发送一条 fatal error，关闭 | 显示诊断，结束会话 |

服务器顺序处理完整帧；即使多个请求粘在一次 recv 中，也先处理前帧到新状态再处理后帧。客户端禁止流水线；服务端仍校验每帧盘号，因此两注同盘只有第一条可能结算。发送结果成功与否不影响“本盘最多计算一次”的约束；发送失败关闭会话，不重新掷骰或跨连接重发。

客户端收到未知类型、版本、额外字段、错误关联编号、非预期顺序或损坏结构时，报告协议错误并关闭，不与服务端互发 error 形成循环。服务端只接受 bet/quit，其他方向的合法类型也视为 UNEXPECTED_MESSAGE。

关联规则的例外：结构合法的 fatal error 可在任意接收等待阶段到达（包括 welcome 前、结果后等待下一盘时），request_id 可为 null；若非 null 必须关联当前请求。客户端显示诊断并关闭。非致命 error 仅允许在等待 bet 响应时接收，必须带当前 request_id 和当前 round_id；之后保持当前开盘快照。客户端自身超时仍直接关闭，不要求先收到服务端 error。

## 6. 错误表与优先级

先检查传输边界和编码，再检查 JSON，再检查版本、类型及字段，再检查 request_id、状态/round_id，最后检查命令。一次仅回最先发现的错误。

| code | fatal | 触发条件 |
|---|---|---|
| FRAME_TOO_LARGE | true | 单帧超过字节上限，或无 LF 残片达到上限 |
| INVALID_ENCODING | true | 非严格 UTF-8 或 BOM |
| INVALID_JSON | true | 空帧、畸形 JSON、非对象、重复键、非有限数字或不合规 CRLF |
| UNSUPPORTED_VERSION | true | version 为整数但不是 1 |
| INVALID_MESSAGE | true | 缺失/未知字段、字段类型错误、无效 ID 范围、未知 type、骰子结构无效 |
| UNEXPECTED_MESSAGE | true | 已知但方向或会话阶段不允许的类型 |
| DUPLICATE_REQUEST | false | 编号已处理或倒退；不再次消耗、结算 |
| ROUND_MISMATCH | false | bet 的 round_id 不等于当前盘 |
| INVALID_COMMAND | false | 长度超限、CR/LF、token 数错误、动词错误 |
| INVALID_PLAY | false | 未知玩法 |
| INVALID_AMOUNT | false | 数字格式、长度或数量范围错误 |
| INVALID_CURRENCY | false | 未知币种 |
| TIMEOUT | true | 半帧等待超时 |
| INTERNAL_ERROR | true | 服务端内部处理失败；不向客户端暴露堆栈 |

余额不足尚无错误码：若 Q05/Q06 要求账本，再新增 INSUFFICIENT_FUNDS 和相应测试，不能让客户端猜测错误字符串。连接已损坏或错误响应发送失败时直接关闭，不保证任何 error 送达。

## 7. 等待、断连与退出

技术设计默认连接超时 5 秒；客户端发送请求后等待响应 10 秒；合法 bet 的 round_result 和后续 round_open 共用该截止时间，初次 welcome+round_open 同样 10 秒。quit 等 bye 最多 2 秒，超时关闭并提示退出未确认。

服务端 OPEN 时允许玩家无限等待输入，不设空闲踢出；一旦收到某帧第一个字节，该帧须在 10 秒内完整接收，后续追加不得重置起始截止时间。协议读取器使用标准库单调时钟与 socket 等待控制，不能设置一个永久 10 秒超时而误踢正常思考的玩家。完整帧后的下一残片独立计时。最后一块收齐时仍核对接收时间，不能因已经完整而绕过期限；及时接收且已缓冲的完整帧保留其接收时间，不因后续读取调用较晚而误判迟到。

同步 CLI 阻塞在终端输入期间不实时监视断线；用户下一次提交时通过发送/读取发现并提示。这是 MVP 明确边界。半关闭按会话 EOF 处理，不尝试让客户端断开发送端后继续结算。客户端在请求已发送但结果未收到时只能报告“结果未知”；方案 A 无恢复/余额退款承诺。Ctrl-C 中止服务端时释放监听和活动连接，客户端稍后检测 EOF。
