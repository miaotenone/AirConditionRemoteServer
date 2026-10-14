# AirConditionRemoteServer

树莓派上的常驻服务：把**飞书机器人的交互卡片**与**局域网里 ESP32 空调网关的 MQTT 状态**连起来，
让你在手机上像用遥控器一样控制空调。

- 上游固件仓库：`AirConditionRemoteCTL`（ESP32 红外网关，**已完成**）
- 两个仓库的**唯一接口**是 MQTT 契约：见 [`docs/interface-spec.md`](docs/interface-spec.md) §4

---

## 当前状态

| 阶段 | 状态 |
|---|---|
| MQTT 链路（ESP32 ⇄ Mosquitto ⇄ 树莓派）| ✅ **已完整验证**，见 [`docs/mqtt-bringup.md`](docs/mqtt-bringup.md) |
| Mosquitto 部署 | ✅ 已装并 `enable`（[`deploy/mosquitto-ac.conf`](deploy/mosquitto-ac.conf)）|
| 服务本体 `app/` | ✅ 纯逻辑 **159 项单测全绿**（不需要 broker / 凭据 / 网络）|
| 飞书自建应用 / 长连接 | ✅ 已建「空调适配」并发布；长连接每 90s 心跳，健康 |
| **卡片端到端联调** | ✅ **已跑通**：10/10 回调解析正确、卡片原地 patch、`ac/cmd` 生效 → [`docs/feishu-bringup.md`](docs/feishu-bringup.md) |
| 部署到树莓派 | ✅ `ac-bot.service` 已 `enable` + `active`（`User=dirj`）|
| 文本控制（发中文短句）| ✅ 已实现并在真机使用中；验收清单 §10 尚有数项待走 |

**已验证的证据**：上/下行双向通、拒绝类命令不动空调、接受类命令的 IR 帧与 `ac/state` 同步、
越界夹取、连续快发取最后一条、断线后遗嘱 `{"online":false}` 与自动重连
（细节见 [`docs/mqtt-bringup.md`](docs/mqtt-bringup.md)）；飞书侧长连接、卡片下发与原地更新、
按钮与下拉回调、多字段合并（细节见 [`docs/feishu-bringup.md`](docs/feishu-bringup.md)）。

## 架构

```
┌────────┐  卡片回调(长连接)  ┌──────────────────────┐
│ 飞书 App │ ←──────────────→ │  树莓派服务 (本仓库)   │
│ (手机端) │  消息/卡片 API     │  ├─ feishu_client     │
└────────┘                   │  ├─ state + SQLite    │
                             │  └─ mqtt_client       │
                             └──────────┬───────────┘
                                        │ MQTT (局域网, 127.0.0.1)
                                   ┌────▼────┐
                                   │Mosquitto│  ← broker 也在树莓派
                                   └────┬────┘
                                        │ MQTT (局域网)
                                   ┌────▼────────────┐
                                   │ ESP32 网关 0x10  │ → 红外 → 美的空调
                                   └─────────────────┘
```

- **真值在 ESP32**：`ac/state`（retained）是唯一事实来源；服务只做中转 + 缓存 + 乐观更新。
- **飞书用长连接**（`lark-oapi` 的 `lark.ws.Client`），树莓派不暴露公网、不需要内网穿透。
- 每个可点击元素携带统一的 `{"a":"<action>","v":<int>}`；下拉与按钮回调在 `feishu_client` 里归一化。

完整设计（卡片 schema、按钮动作表、时序、错误处理、验收标准）见
[`docs/interface-spec.md`](docs/interface-spec.md)。

## 目录结构

```
app/
  main.py          # 装配 + 事件循环：乐观更新 / 去抖合并 / 确认超时 / 过期提示
  config.py        # pydantic-settings；密钥用 SecretStr，不会漏进日志
  mqtt_client.py   # paho-mqtt 适配器：订阅 ac/state、ac/status，发布 ac/cmd
  state.py         # AcState / Presence：夹取、动作应用、批量合并、内容指纹
  actions.py       # 回调归一化：按钮 action.value 与下拉 action.option → (a, v)
  intent.py        # 文本 → Intent（纯函数：分词、词表、否定、兜底）
  replies.py       # Intent + 当前状态 → 回执文案（纯函数）
  cards.py         # 卡片 JSON 构造（纯函数；可 --print-card 直接看）
  feishu_client.py # 长连接 + 发卡 / 更新卡 / 回文本
  store.py         # SQLite：owner ↔ 卡片 message_id、已处理消息去重
tests/             # 159 项单测，零外部依赖（不需要 broker、凭据或网络）
deploy/            # mosquitto-ac.conf（✅ 已部署）、ac-bot.service
docs/              # interface-spec.md（契约）、mqtt-bringup.md（MQTT 验证）、
                   # feishu-bringup.md（飞书链路验证）、feishu-setup.md（建应用清单）
```

## 跑起来 / 开发

```bash
# 跑测试：只用标准库 + pytest，不需要 MQTT、飞书或任何凭据
python -m pytest -q

# 看一眼卡片长什么样（不需要任何配置；卡片 JSON 直接打到终端）
python -m app.main --print-card

# 正式运行：需要 .env（照 .env.example 填）
cp .env.example .env
python -m app.main
```

设计上刻意让**纯逻辑与 I/O 分开**：`state.py` / `cards.py` / `actions.py` / `intent.py` /
`replies.py` / `store.py` 不依赖 MQTT 或飞书，所以点按钮**或发一句话**的逻辑（夹取、去抖合并、
回调归一化、分词、回执文案、卡片结构）能在本机毫秒级验证；`mqtt_client.py` / `feishu_client.py`
只是薄适配器。

## 文本控制

私聊里直接发中文，和点卡片**完全等价** —— 解析出来的还是 `(action, value)`，进同一个动作队列，
所以去抖、乐观更新、确认超时都不用重写。

```
开机制冷24度      →  ✅ 已开机 · 制冷 · 24℃ · 自动风
调低两度          →  ✅ 已开机 · 制冷 · 22℃ · 自动风
风速最大          →  ✅ 已开机 · 制冷 · 22℃ · 高风
状态              →  现在 开机 · 制冷 · 22℃ · 高风 · 🟢 在线（更新于 12:03:41）
不要制冷          →  好，模式没动
太热了            →  看着有点热？说「制冷 22 度」，或点卡片上的「制冷」。   ← 只建议，不执行
```

- **规则解析，不接 LLM**：只认空调真实具备的说法，听不懂就回「没听懂这句」+ 语法清单，绝不猜。
- **形容词句永不执行**（`太热了` / `有点冷`）：温度该降 1 还是 3、要不要切制冷，只有你知道。
  说清动作（`制冷22度`）就照做，`太热了，制冷22度` 也照做。
- **问句优先**：含 `状态`/`多少度`/`几度`/`怎么样`，或**以 `了吗`/`了呢`/`了没` 结尾**（`开机了吗`）
  一律当查询。反过来 `开空调好吗` 是**请求**，照做 —— 只把「…了吗」这种完成态问句当状态查询。
- **短词上锁**：`开` / `关` / `停` / `打开` / `关闭` / `停了` 只在整句是对空调说的时候才算命令，
  所以 `没关系`、`开心`、`开关`、`关灯`、`打开心扉`、`雨停了` **都不会动空调**（回「没听懂」）。
  代价是 `一直开着`、`把窗户关了` 也不动作 —— 这是**故意的**，防误触发优先。
- 完整词表、分派顺序、回执格式见 [`docs/interface-spec.md`](docs/interface-spec.md) §5.6；
  设计与取舍（含 18 条决策、7 条已知限制）见
  [`docs/superpowers/specs/2026-10-13-feishu-text-control-design.md`](docs/superpowers/specs/2026-10-13-feishu-text-control-design.md)。
- **召唤卡片**：发 `设置卡片`（可带句号）→ 一张**新卡**发到消息流最新位置，旧卡被撤回
  （撤不回则旧卡变成一行「遥控器已更新，最新一张在下面 👇」）。旧召唤词 `/ac`、`/start`、
  `ac`、`空调` 已退场 —— 现在回「没听懂这句。…」，并照旧补一张卡。
- 不想要这个功能：`TEXT_CONTROL_ENABLED=false`，回到「只有 `设置卡片` 有反应」。

## MQTT 契约速览

| Topic | 方向（相对本服务）| QoS | Retained | Payload |
|---|---|---|---|---|
| `ac/cmd` | 发布 | 1 | 否 | `{"power":1}` / `{"temp":24}` / `{"mode":1,"fan":2}` |
| `ac/state` | 订阅 | 1 | 是 | `{"online":true,"power":1,"mode":1,"temp":24,"fan":0,"brand":16}` |
| `ac/status` | 订阅 | 1 | 是 | `{"online":true}` / 遗嘱 `{"online":false}` |

取值范围：`power` 0/1；`mode` 自动0/制冷1/除湿2/送风3/制热4；`temp` 16~30；`fan` 自动0/低1/中2/高3。
**服务侧必须先校验，不依赖固件兜底。**

## 环境事实（目标树莓派）

| 项 | 值 |
|---|---|
| 主机 | `raspberrypi4b` · Ubuntu 22.04.5 LTS (aarch64) · Python 3.10.12 |
| Broker | Mosquitto 2.0.11，`0.0.0.0:1883`，`allow_anonymous true` |
| 树莓派 LAN | `192.168.8.103/24`（网关 `192.168.8.1`）—— **需固定 IP** |
| ESP32 | `192.168.8.105`，前缀 `ac`，设备名 `ac-gateway`，品牌 `0x10` |
| 运维入口 | `ssh root@100.114.10.43`（Tailscale，免密）|

> ⚠️ **影子状态陷阱**：固件不知道你用实体遥控器做过什么。每次接受命令都会发**整帧**（含 `power`），
> 所以若你用遥控器开着空调，下一条被接受的命令会按它的影子状态把空调关掉。详见
> [`docs/mqtt-bringup.md`](docs/mqtt-bringup.md) §5。

## 下一步

1. **建飞书自建应用**：照 [`docs/feishu-setup.md`](docs/feishu-setup.md) 拿到 `App ID` /
   `App Secret` / `OWNER_OPEN_ID`；订阅方式切成**长连接**；订阅 `im.message.receive_v1`
   与卡片回调 `card.action.trigger`。
2. **部署到树莓派**：`/opt/ac-bot` + venv + `.env`，按
   [`deploy/ac-bot.service`](deploy/ac-bot.service) 上 systemd（注意 `User=dirj`）。
3. **首次联调**（预计要来回几轮）：
   - 对着真机微调卡片排版（`column_set` 的宽度、按钮换行）；
   - 确认下拉 `select_static` 的回调确实走 `action.option`；
   - 确认应用开了**发消息 / 回复消息**权限，否则文本回执发不出去（长连接收得到 ≠ 回得了）；
   - 走一遍 [`feishu-setup.md`](docs/feishu-setup.md) §10 的 7 条验收清单；
   - 走一遍文本控制的 13 条真机验收清单（设计文档 §8）。
4. 收紧 MQTT 鉴权：`allow_anonymous` → 账号 + ACL，**两边一起改**。

## 说明与限制

- v1 是**私聊单用户**（`OWNER_OPEN_ID`），机器人不拉群、不做多用户、不做多空调、不做定时场景。
- 能力**只覆盖控制**：状态的真值永远来自 ESP32；空调被实体遥控器改变时，服务只能在下一次对齐后得知。
- **连发才合并**：三条「调低两度」若**快于** `ac/state` 往返，会合并成一条 `ac/cmd`（降 6℃）；
  若慢慢打（真值已过期），三条会各自从当时的真值算 —— 这是既有管道（卡片连点也一样）的性质，
  详见设计文档 §13 已知限制 1。**联调按「连发」测。**
- 本仓库与固件仓库的 MQTT 契约必须保持一致；改任一侧都要同步另一侧与
  [`docs/interface-spec.md`](docs/interface-spec.md) §4。
