# MQTT 链路 —— 验证记录与环境事实

日期：2026-10-13
状态：**已完整验证**。ESP32 ⇄ 树莓派 broker 双向链路、IR 下发、遗嘱（LWT）全部跑通。
本文件是服务本体的**地基事实**：写 `mqtt_client.py` 时以这里记录的 topic/payload/时序为准。

契约权威定义见 [`interface-spec.md`](interface-spec.md) §4；固件侧实现在
`AirConditionRemoteCTL/src/net_mqtt.cpp`。

---

## 1. 拓扑

```
ESP32 网关          192.168.8.105   （DHCP，写在固件配置里的目标是下面的 Pi IP）
   │  MQTT over LAN
   ▼
Mosquitto 2.0.11   树莓派 :1883
   │
树莓派 wlan0        192.168.8.103/24   网关 192.168.8.1
   │  Tailscale
   ▼
100.114.10.43       （运维入口：ssh root@100.114.10.43，免密）
```

- 主机名 `raspberrypi4b`，Ubuntu 22.04.5 LTS (aarch64)，Python 3.10.12。
- broker 只监听 `0.0.0.0:1883`，`allow_anonymous true`（内网信任，见 §5）。
- **ESP32 里写的是字面量 `192.168.8.103`** → 树莓派必须固定这个 IP（DHCP 保留或静态）。

## 2. ESP32 侧配置（NVS，已验证跨重启保持）

用固件新增的**串口配置台**写入（免手机）：

```powershell
# 在 AirConditionRemoteCTL 仓库下
python scripts/provision.py COM16 --windows-profile <你的 2.4GHz SSID> --host 192.168.8.103 --mqtt-port 1883
```

| 字段 | 值 |
|---|---|
| WiFi | 家用 2.4GHz（SSID/密码只存在 ESP32 的 NVS，**不进仓库**）|
| MQTT host / port | `192.168.8.103` / `1883` |
| user / pass | 空（与 broker 的 `allow_anonymous` 对应）|
| topic 前缀 | `ac` |
| 设备名 | `ac-gateway` |
| 品牌码 | `0x10`（美的1）|

开机串口实录（说明"自家凭据优先直连"生效）：

```
[wifi] trying stored ssid '<SSID>'..
[wifi] connected, IP=192.168.8.105
[mqtt] connected
[ac-gateway] ready, brand=0x10 topic=ac
```

## 3. 已验证行为（固件 README §4 1–9 全过）

| 项 | 结果 |
|---|---|
| 上行 | 订阅 `ac/#` 立刻收到 retained `ac/state`；`ac/status {"online":true}` |
| 下行 · 拒绝类 | 坏 JSON / 未知字段 / 空 payload → 串口 `[cmd] ... ignored`，**不发 IR** |
| 下行 · 接受类 | `{"power":1}` → IR `68 0C 00 FF 30 10 01 01 18 00 55 16` → `ac/state power:1`（`power:0` 同理，第 7 字节翻转）|
| 温度/模式 | `{"temp":22,"mode":1}` → state 同步，IR 第 9 字节 `0x16`=22 |
| 越界夹取 | `{"temp":99}`→30（`0x1E`）、`{"temp":0}`→16（`0x10`）|
| 连续快发 | 连发 18→22 共 5 帧全发，最终 `ac/state` = 最后一条 |
| 遗嘱 | iptables 隔离 ESP32 约 25s → `{"online":false}`；规则移除后 ~5s 自动 `{"online":true}` |
| 抗抖动 | 掉线后 ESP32 自己重连（串口 `[mqtt] connect failed rc=-2` → `[mqtt] connected`），无需重启 |

**IR 帧布局（多参数 `0x30`，服务侧无需构造，仅用于排障）**：
`68 0C 00 FF 30 <brand> <power> <mode> <temp> <fan> <checksum> 16`，温度字节 `0x10`~`0x1E`。

## 4. 复验命令（在树莓派上）

```bash
# 看 retain 的真值
mosquitto_sub -h 127.0.0.1 -t 'ac/state'  -C 1 -W 3
mosquitto_sub -h 127.0.0.1 -t 'ac/status' -C 1 -W 3

# 发一条命令（注意：会真实操作空调，见 §5 影子状态）
mosquitto_pub -h 127.0.0.1 -t 'ac/cmd' -m '{"power":0}'

# 常驻抓全量（排障）
mosquitto_sub -h 127.0.0.1 -v -t 'ac/#'
```

## 5. 已知限制 / 注意事项

1. **⚠️ 影子状态陷阱**：固件只维护自己的状态影子（默认 `power:0`）。它**不知道**你用实体遥控器做过什么。
   每次接受一条命令都会发**整帧**（含 `power`），所以：**若你用遥控器开着空调，下一条任何被接受的命令都会按影子状态把它关掉。**
   → 服务侧要如实显示 `ac/state`，并考虑开机时先发一次完整状态对齐（后续可做）。
2. **`allow_anonymous` 仍是 true**（接口规格 §12 待确认项 1）。局域网内可接受；要收紧就上 `password_file` + ACL，
   并在 ESP32 串口配置台设 `user`/`mqttpass`。**改之前两边要一起改**。
3. **树莓派 IP 必须固定**（192.168.8.103）；固件里没有域名解析。
4. **心跳**：固件每 30s 重发一次 `ac/state`。服务可用"最后一条 `ac/state` 的到达时间"判断新鲜度，
   超时（约 >90s）但 `status.online=true` 时提示"状态可能过期"（规格 §10）。
5. 局域网 ping ESP32 偏高（曾测 ~74ms，怀疑 WiFi 信号弱）；MQTT 往返实际在数十~数百 ms，
   对 3s 回调预算无压力，但**不要**把 MQTT 往返放在飞书回调的同步路径上（规格 §5.5）。
