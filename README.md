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
| 服务本体（`app/`）| ⬜ 未开工 |
| 飞书自建应用 / 长连接 | ⬜ 未开工（需要 App ID/Secret）|

**已验证的证据**：上/下行双向通、拒绝类命令不动空调、接受类命令的 IR 帧与 `ac/state` 同步、
越界夹取、连续快发取最后一条、断线后遗嘱 `{"online":false}` 与自动重连。细节见
[`docs/mqtt-bringup.md`](docs/mqtt-bringup.md)。

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
app/            # 服务本体（未开工，规划见 docs/interface-spec.md §9）
tests/          # cards.py / state.py 的纯函数单测
deploy/         # mosquitto-ac.conf（✅ 已部署）、ac-bot.service
docs/           # interface-spec.md（接口契约）、mqtt-bringup.md（验证记录 + 环境事实）
```

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

1. 搭 `app/` 骨架：`config.py`（pydantic-settings）→ `mqtt_client.py`（paho-mqtt，接本机 broker）
   → `state.py` → `cards.py`，先把 **MQTT ⇄ 卡片** 跑通，再接飞书。
2. 建飞书自建应用：拿 `App ID` / `App Secret`、开机器人能力与长连接所需事件权限。
3. 端到端联调后，按 `docs/interface-spec.md` §8 上 systemd。

## 说明与限制

- v1 是**私聊单用户**（`OWNER_OPEN_ID`），机器人不拉群、不做多用户、不做多空调、不做定时场景。
- 能力**只覆盖控制**：状态的真值永远来自 ESP32；空调被实体遥控器改变时，服务只能在下一次对齐后得知。
- 本仓库与固件仓库的 MQTT 契约必须保持一致；改任一侧都要同步另一侧与
  [`docs/interface-spec.md`](docs/interface-spec.md) §4。
