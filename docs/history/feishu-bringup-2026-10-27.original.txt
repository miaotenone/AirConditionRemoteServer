# 飞书链路 —— 验证记录与环境事实

日期：2026-10-14
状态：**卡片路径已完整验证**。长连接、卡片下发、卡片原地更新、按钮与下拉回调、
`ac/cmd` 下发全部在真机（飞书客户端 + 树莓派 + ESP32）上跑通。

本文件与 [`mqtt-bringup.md`](mqtt-bringup.md) 是同一类记录：把"真的验过什么、环境长什么样"
固化下来。契约权威定义见 [`interface-spec.md`](interface-spec.md)；
控制台操作步骤见 [`feishu-setup.md`](feishu-setup.md)。

---

## 1. 拓扑

```
飞书客户端（手机）
   │  长连接（出站 wss，无需公网 IP）
   ▼
ac-bot.service        树莓派 ~/project/AirConditionRemoteServer
   │  MQTT over LAN
   ▼
Mosquitto 2.0.11      127.0.0.1:1883
   │
ESP32 网关            192.168.8.105
```

- 飞书侧**只有出站长连接**：树莓派不监听端口、不开公网、不用内网穿透。
- 事件与回调都走**同一个**长连接：`im.message.receive_v1` 与 `card.action.trigger`。

## 2. 部署事实（树莓派）

| 项 | 值 |
|---|---|
| 代码 | `~/project/AirConditionRemoteServer`（**git 检出**，依赖用 `uv sync` 装）|
| 解释器 | `~/project/AirConditionRemoteServer/.venv`（`uv` 0.13.1 建，版本由 `uv.lock` 锁定），Python 3.10.12 |
| 关键依赖 | `lark-oapi 1.7.4`（本机 Windows 侧 1.7.2，行为一致）、`paho-mqtt 2.1.0`、`pydantic 2.14.1`、`pydantic-settings 2.15.0` |
| 服务 | `ac-bot.service`，`User=dirj`，`enabled` + `active`，`Restart=always` |
| 依赖关系 | `Requires=mosquitto.service`（broker 停了它就一起停）|
| 密钥 | `~/project/AirConditionRemoteServer/.env`，`0600 dirj:dirj`（**不入仓库**，`git clone` 带不来，必须手工拷）|
| 状态库 | `/var/lib/ac-bot/ac-bot.sqlite3`（systemd `StateDirectory=ac-bot`）|

> **2026-10-15 迁移**：原先手工拷在 `/opt/ac-bot`（非 git 检出，代码靠 `cp -a` 覆盖），现改为
> **git 检出**到 `~/project/AirConditionRemoteServer`，虚拟环境改用 `uv` 建、依赖版本由
> `uv.lock` 锁定（`lark-oapi 1.7.4` / `pycryptodome 3.24.1`；uv 默认解析会跳成 1.7.5 / 4.0.0，
> 故在 `pyproject.toml` 的 `[tool.uv] constraint-dependencies` 里钉住）。
> 固件仓库同样克隆在 `~/project/AirConditionRemoteCTL`，只作参考、**不参与运行**。
> 状态库仍在 `/var/lib/ac-bot/`，预设 / 历史 / 设置都不受这次迁移影响。
> 旧目录 `/opt/ac-bot` **已删除**（迁移验证通过后清理）；真要回滚，把 unit 指回旧路径
> 加 `git checkout <旧提交>` 就够了。

重装/复验：

```bash
systemctl status ac-bot            # 应为 active (running)
journalctl -u ac-bot -f            # 实时日志
```

> **更新代码（当前流程：开发机推送 + ssh 传过去）**：两个仓库都是**私有**的，树莓派上
> 没有任何 GitHub 凭据，所以不在树莓派上 `git pull`，而是把提交打成 bundle 传过去：
>
> ```bash
> # 1) 开发机（Windows PowerShell）—— 先上 GitHub，再打包传过去
> git push origin HEAD:refs/heads/feature/presets-weather-dht11
> git bundle create -q $env:TEMP\ac-bot.bundle HEAD
> scp $env:TEMP\ac-bot.bundle root@100.114.10.43:/tmp/
>
> # 2) 树莓派 —— 快进到那个提交
> cd ~/project/AirConditionRemoteServer
> git fetch /tmp/ac-bot.bundle HEAD && git merge --ff-only FETCH_HEAD
> #   （开发机若重写过历史，--ff-only 会拒绝，改用 git reset --hard FETCH_HEAD）
> uv sync                            # 依赖有变动时才需要（uv.lock 是唯一真源）
> sudo systemctl restart ac-bot
> ```
>
> 想让树莓派直接 `git pull` 也行，但要先给它配凭据：`dirj` 下已生成
> `~/.ssh/id_ed25519_github`，把 `.pub` 贴到两个仓库的 *Settings → Deploy keys*（**只读**），
> 再把 remote 换成 `git@github.com:miaotenone/<repo>.git`。不配不影响上面的流程。
>
> ⚠️ `git clone`（或换机器重装）**不会**带来 `.env` —— 那是凭据，必须手工拷。

## 3. 飞书侧事实（应用与标识）

| 项 | 值 |
|---|---|
| App ID | `cli_aa472d99e223dbeb`（显示名**「空调适配」**）|
| 机器人 open_id | `ou_f70ba5a1c68de0eae1aa4e722cda2d2c`，`activate_status=2`（已启用）|
| 唯一控制人 `OWNER_OPEN_ID` | `ou_7eed279fef505d195019b7c109d96bbb` |
| 私聊 `chat_id` | `oc_673b57b98e0629199bd41cd335530f16`（`chat_type=p2p`）|
| 在用卡片 `message_id` | `om_x100b63d111c4d8a4ceac33fe07ce2ca`（存在 `card` 表里，被原地 patch）|

已授权 scope（`GET /open-apis/application/v6/scopes`，**用应用自身 token 即可读**）：

```
im:message                    收/发消息
im:message.p2p_msg:readonly   收私聊消息  ← im.message.receive_v1 靠它
im:message:send_as_bot        以机器人身份发消息  ← 发卡片靠它
```

- **没有**群聊消息权限 → 必须在**私聊**里发；群里 @ 机器人收不到。
- `contact/*`、`application/v6/applications/*` 一律 `99991672`（应用读不了自己的配置，
  也读不了通讯录）—— 所以 `open_id` 只能从真实事件里读，不能查目录。
- 长连接**每 90s 一次 ping/pong**，全部有回应即为健康；日志里 `ping success` / `receive pong`。

## 4. 已发现并修复的缺陷（关键）

**症状**：卡片能发出，但点按钮**完全没有反应**。日志：

```
INFO  app.feishu_client: card action from : value=None option=None
WARNING __main__: ignoring event from non-owner ''
```

2026-10-13 23:04 ~ 2026-10-14 00:02 之间**真实点击被丢了 4 次**。

**根因**：`lark-oapi` 交给 `register_p2_card_action_trigger` 的是
`P2CardActionTrigger`，其模型**只映射 `event`**：

```python
class P2CardActionTrigger(EventContext):
    _types = {"event": P2CardActionTriggerData}   # operator / action / context 都在 event 下
```

而代码读的是顶层 `data.operator` / `data.action` / `data.context`。读顶层**不报错**，
只是静默得到全 `None` → 操作人被当成空串 → 被 `_is_owner` 当非主人丢弃。
（`register_p2_im_message_receive_v1` 那边一直是 `data.event`，所以文本路径没受影响。）

**修复**：`ca77647`，读 `data.event.*`，并保留"扁平形状"回退；两个形状都进了单测。
修复后同一批点击立刻正常：

```
INFO  app.feishu_client: card action from ou_7eed…: value={'a': 'power', 'v': 0} option=None
INFO  __main__: card action power=0 from ou_7eed…
INFO  app.mqtt_client: ac/cmd -> {"power":0}
```

## 5. 已验证行为（真机）

**卡片回调 10/10 全部解析正确，0 次 `patch_card failed`**：

| 回调 `value` / `option` | 归一化结果 | 发出 |
|---|---|---|
| `{'a':'power','v':0}` | `power=0` | `{"power":0}` |
| `{'a':'power','v':1}` | `power=1` | `{"power":1}` |
| `{'a':'mode','v':4}` | `mode=4` | `{"mode":4}` |
| `{'a':'mode','v':1}` | `mode=1` | `{"mode":1}` |
| `{'a':'temp_step','v':-1}` | `temp_step=-1` | `{"temp":23}`（相对量按当前态算绝对值）|
| **`value=None, option='30'`** | `temp=30` | `{"temp":30}` ✅ `select_static` 走 `action.option`，与 `actions.py` 假设一致 |
| `{'a':'fan','v':0/1/3}` | `fan=0/1/3` | `{"fan":0/1/3}` |

其它已验证：

| 项 | 证据 |
|---|---|
| 卡片下发 | `POST /im/v1/messages`（`msg_type=interactive`）成功，返回 `message_id` |
| 卡片原地更新 | 每次点击后 3~4 次 `PATCH request completed with status 200`，聊天里不新增消息 |
| 乐观更新 | 点击后先 patch 出 `⏳`，`ac/state` 回来再 patch 成实状态 |
| 多字段合并 | 文本「开机制冷24度」→ **一条** `{"power":1,"mode":1,"temp":24}`（3 字段）；另一条 `{"power":1,"temp":26}` |
| 文本去重 | `seen` 表按 `message_id` 去重，实测 15 条互不重复 |
| 权限 | 发卡片、回文本（`reply`）都没出现 `99991672` |

## 6. 复验命令

```bash
# 日志里看回调是否解析正确（应有 ou_ … 而不是空串）
journalctl -u ac-bot -o cat | grep "card action"

# 看真值（会真实操作空调，注意影子状态陷阱）
timeout 3 mosquitto_sub -h 127.0.0.1 -v -t 'ac/#'

# 看卡片映射与去重表
sudo -u dirj python3 -c "
import sqlite3; c=sqlite3.connect('/var/lib/ac-bot/ac-bot.sqlite3')
for (n,) in c.execute(\"select name from sqlite_master where type='table'\"):
    print(n, c.execute(f'select count(*) from {n}').fetchone()[0])"
```

排障提示：**事件原始 payload（`receive message … payload: {…}`）只在 `FEISHU_LOG_LEVEL=DEBUG`
时进 journal**，INFO 级别看不到。2026-10-14 丢的那 4 次点击正好发生在 INFO 时段（00:01–00:04），
journal 里**一行 payload 都没有**，事后无从回溯 —— 所以排障前先在 `~/project/AirConditionRemoteServer/.env` 里把
`FEISHU_LOG_LEVEL` 改成 `DEBUG` 并 `systemctl restart ac-bot`（改回 `INFO` 记得再重启）。
`chat_id` 与回调结构是当时开着 DEBUG 才拿到的。
补了一层兜底：**字段一个都解不出来的点击，现在会在 WARNING 打出原始报文**
（`card action payload matched no known shape: {…}`，见设计 §10 D21）——
最危险的那类故障即使不开 DEBUG 也留得下证据。

## 7. 已知限制 / 待验项

1. **验收清单（`feishu-setup.md` §10）还有几项没在真机走完**：
   - 第 4 条：连点 3 次「＋」只发一条 `ac/cmd`（合并逻辑已由文本侧 3 字段合并间接证明，
     但"卡片连点"没单独测）；
   - 第 5 条：用实体遥控器改状态后 ≤30s 心跳对齐；
   - 第 6/7 条：ESP32 断电/上电 → 卡片 🔴/🟢（固件期已用 iptables 验过 LWT，服务侧渲染未验）。
2. **孤儿卡片**：验证期用脚本手动发过一张卡片 `om_x100b63d0cb95…`，它**不在 `card` 表里**，
   因此永远不会被更新。一条聊天里出现两张卡片会让人困惑 —— 需要时用
   `DELETE /im/v1/messages/:message_id` 删掉它（同一 App 才能删自己发的消息）。
3. **`allow_anonymous` 仍是 true**（与 `mqtt-bringup.md` §5.2 同一条），要收紧时两边一起改。
4. **长连接单实例**：同一 App 多处登录会互相踢；改动/调试时确认只有 `ac-bot.service` 一个进程。
5. 树莓派 IP 必须固定（`192.168.8.103`），见 `mqtt-bringup.md` §5.3。
