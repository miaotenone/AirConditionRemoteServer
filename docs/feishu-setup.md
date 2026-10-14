# 飞书自建应用 —— 建应用清单

目标：拿到三样东西，并让**长连接**（不需要公网、不需要内网穿透）能收到消息和卡片点击。

| 要拿到的东西 | 填到哪 |
|---|---|
| `App ID`（`cli_...`）| `.env` 的 `FEISHU_APP_ID` |
| `App Secret` | `.env` 的 `FEISHU_APP_SECRET` |
| 你自己的 `open_id`（`ou_...`）| `.env` 的 `OWNER_OPEN_ID` |

> 控制台文案会随版本调整，下面写的是**路径与要勾的东西**；如果某一步的名字对不上，
> 找近义词即可。最后面的「§8 我们已验证的代码侧事实」是不受控制台文案影响的部分。

---

## 1. 建应用

1. 打开 <https://open.feishu.cn/app>（企业自建应用入口）。
2. **创建企业自建应用** → 填名称（例如 `空调遥控器`）、图标、描述。
3. 进入应用 → **凭证与基础信息**，复制：
   - **App ID** → `FEISHU_APP_ID`
   - **App Secret** → `FEISHU_APP_SECRET`

> 个人版飞书也能建自建应用；如果组织策略禁止，需要管理员开权限。

## 2. 开「机器人」能力

**添加应用能力** → **机器人** → 启用。

不开这一步，机器人无法给你发消息，卡片也就发不出来。

## 3. 开权限（Scopes）

**权限管理** → 添加权限。至少需要：

| 权限 | 用途 |
|---|---|
| 获取与发送单聊、群组消息（`im:message`）| 接收你发的 `设置卡片`，给会话发卡片 |
| 以应用的身份发消息（`im:message:send_as_bot`）| 发卡片 / 更新卡片 |
| 读取用户发给机器人的单聊消息（`im:message.p2p_msg:readonly`）| 拿 `chat_id` |

> 卡片按钮点击**不走**权限，走第 5 步的「回调」。
>
> 文本回执（`im.v1.message.reply`）用的就是上面这个 `im:message:send_as_bot`，**不需要额外权限**；
> 但和发卡片一样，权限改动后必须**重新发布版本**才生效（见第 11 节第 4 条）。

## 4. 事件订阅方式切成「长连接」

**开发配置 → 事件与回调 → 订阅方式** → 选 **「使用长连接接收事件（推荐）」**。

这一步是整套方案不暴露公网的关键；选错（比如选了「将事件发送至开发者服务器」）
就得自己准备 HTTPS 回调地址和验签。

## 5. 订阅「接收消息」事件

同一个页面 → **添加事件** → 搜 `接收消息` / `im.message.receive_v1` → 添加。

## 6. 订阅「卡片回传交互」回调

**事件与回调 → 回调订阅**（或同一页的「回调」标签）→ 添加
**卡片回传交互**（`card.action.trigger`）。

> 旧版是 `card.action.trigger_v1`。我们代码用的是**新版**
> （`EventDispatcherHandler.register_p2_card_action_trigger`），所以这里也要选新版。
> 如果只找得到 v1，代码要相应换注册方法 —— 看到时告诉我，一行的事。

## 7. 发布与可用范围

- **版本管理与发布** → 创建版本 → 发布（企业自建应用通常需要管理员审批）。
- **可用范围**设为**只有你自己**即可（v1 就是私聊单用户）。

## 8. 拿 `OWNER_OPEN_ID`

**方法 A（推荐，零配置、零额外权限）：让服务自己告诉你**

1. 把 `.env` 里的 `OWNER_OPEN_ID` 写成**空值** —— 是 `OWNER_OPEN_ID=`（键留着），
   **不要删掉整行**：字段是必填的，删了会 `ValidationError ... owner_open_id Field required`。
2. 启动服务：`python -m app.main`。
3. 在飞书里给机器人发一句话（或者点一下卡片上的按钮）。
4. 日志里会出现一行（`LOG_LEVEL=INFO` 就能看到）：
   ```
   2026-10-13 21:04:33 WARNING app.main: ignoring event from non-owner 'ou_3f8a1b2c3d4e5f6a7b8c9d0e1f2a3b4c'
   ```
   引号里那串 `ou_` 开头的就是你的 `open_id`。
5. 填回 `.env`：`OWNER_OPEN_ID=ou_3f8a1b2c...`，重启服务。

> 非 owner 的**消息不会得到任何回复**（避免探测，见 `interface-spec.md` §10），所以只能看日志，
> 别等机器人回你。第 1 步的空值状态是安全的：`_is_owner` 是纯字符串比较，空值谁也匹配不上。

**方法 B（不想起服务）：** 用 `tenant_access_token` 调
`POST /open-apis/contact/v3/users/batch_get_id`，body `{"emails":["你的邮箱"]}`，
需要权限 **获取用户 ID**（`contact:user.id:readonly`）。返回里的 `open_id` 就是它。

> ⚠️ **`open_id` 是「按应用」的**：同一个飞书账号，在 A 应用和 B 应用里拿到的 `open_id` 是
> **两个不同的值**。别从别的应用或别的配置里抄一个过来 —— 必须用**这个应用**给出的值。
>
> ⚠️ **Windows 记事本陷阱**：`.env` 若存成「UTF-8 **带 BOM**」，python-dotenv 会把第一行的键读成
> `\ufeffFEISHU_APP_ID`，于是 `FEISHU_APP_ID` 变成缺失，启动直接报 `feishu_app_id Field required`。
> 用 VSCode，或存成「UTF-8（无 BOM）」。

## 9. 填 `.env` 并启动

```bash
cp .env.example .env
$EDITOR .env          # 填 FEISHU_APP_ID / FEISHU_APP_SECRET / OWNER_OPEN_ID

# 本地先干跑一次，只验证卡片 JSON 能生成（不需要任何凭据）
python -m app.main --print-card

# 正式启动
python -m app.main
```

部署成服务见 [`deploy/ac-bot.service`](../deploy/ac-bot.service) 顶部注释，
以及 [`interface-spec.md`](interface-spec.md) §8。

## 10. 验收清单（端到端）

| # | 操作 | 预期 |
|---|---|---|
| 1 | 启动服务 | 日志出现 `feishu long connection started` 和 `mqtt connected` |
| 2 | 给机器人发 `设置卡片` | 收到**遥控器卡片**，顶部显示当前状态 |
| 3 | 卡片上点「开机」 | 卡片立刻出现 ⏳ → 约 1 秒内变实状态、空调开机 |
| 4 | 连点 3 次「＋」 | 只发**一条** `ac/cmd`（日志里只有一行），温度 +3 |
| 5 | 长按遥控器实体键把空调关掉（或用遥控器改温度）| 静置 ≤30 秒（心跳）后卡片与实际**对齐** |
| 6 | 把 ESP32 断电 | 约 25 秒后卡片变「🔴 离线」 |
| 7 | ESP32 上电 | 卡片自动回到「🟢 在线」 |

> 第 5 条就是 [`mqtt-bringup.md`](mqtt-bringup.md) §5 说的**影子状态陷阱**：固件不知道你用
> 实体遥控器做了什么，只能靠它下一次上报来对齐。

文本控制的验收（完整 13 条见设计文档 §8，这里是最关键的几条）：

| # | 操作 | 预期 |
|---|---|---|
| 8 | 发「开机制冷24度」 | 回 `✅ 已开机 · 制冷 · 24℃ · 自动风`；日志里**只有一行** `ac/cmd`（含 3 个字段）|
| 9 | 发「太热了」 | 回一条建议；**没有** `ac/cmd`，空调不动 |
| 10 | 发「调低两度」三遍 | 回三条回执，温度降 6℃（去重只管重投，不管重复发）|
| 11 | 发「状态」 | 回 `现在 …`；卡片**不闪**（内容没变就不 patch）|
| 12 | 发「不要制冷」 | 回 `好，模式没动`；空调不动 |
| 13 | 发「你好」 | 回 `没听懂这句。` + 语法清单 |

---

## 11. 常见坑

1. **卡片发出来了，但点按钮没反应** → 三种原因，靠日志区分：
   - 长连接里**一个回调包都收不到** → 第 6 步的回调没订阅，或订阅成了 `v1` 版本；
   - 收到了但字段读不出来，日志是 `card action from : value=None option=None`，且后面跟着
     `WARNING card action payload matched no known shape: {…}` → **那行 `{…}` 就是原始报文**，
     发我即可定位（旧版只认 `data.event` 下的嵌套形状，2026-10-14 就是这样丢了 4 次点击，
     见 §12 与 [`feishu-bringup.md`](feishu-bringup.md) §4）；
   - 收到了、字段也对，但被丢弃，日志是 `ignoring event from non-owner 'ou_…'`
     → `OWNER_OPEN_ID` 没填对。
2. **完全收不到消息** → 第 4 步没切成「长连接」，或机器人能力没开，或应用没发布/不在可用范围。
3. **`app not found` / `invalid app_id`** → App ID/Secret 抄错，或复制时带了空格；`.env` 里不要加引号。
4. **能收消息但发不出卡片** → 缺 `im:message:send_as_bot` 权限，或权限加了但**没重新发布版本**（权限变更要重新发版才生效）。
5. **卡片能显示但更新不了** → 卡片 JSON 里 `config.update_multi` 必须为 `true`（我们的 `cards.py` 已经设了）。
6. **长连接反复断** → 同一 App 用同一个长连接客户端 id 多处登录会互相踢；确认只有这一个进程。
7. **文本回执发不出去** → 日志里看 `reply_text failed: code=… msg=…`。和发卡片同权限同发版要求；
   另外回复的是**原消息**，若那条消息已被撤回/删除，`reply` 会失败但**不影响** `ac/cmd` 已经生效。

---

## 12. 我们已经在代码侧验证过的事实

这些不依赖控制台文案，已经对着**本机安装的 `lark-oapi 1.7.2`** 实测过：

- 长连接入口：`lark.ws.Client(app_id, app_secret, log_level=..., event_handler=..., auto_reconnect=True)`
- 注册回调要在 **builder** 上，不在 handler 上：
  ```python
  EventDispatcherHandler.builder("", "") \
      .register_p2_card_action_trigger(fn) \
      .register_p2_im_message_receive_v1(fn) \
      .build()
  ```
- 卡片点击回调返回 `P2CardActionTriggerResponse({})`
  （来自 `lark_oapi.event.callback.model.p2_card_action_trigger`；SDK 自己的
  `channel.py` 就是这么写的 —— **不是** `lark.P2CardActionTriggerResponse`）。
- 回调数据**嵌在 `event` 下**（`P2CardActionTrigger` 只把 `event` 映射成
  `P2CardActionTriggerData`）：`data.event.operator.open_id`、
  `data.event.action.value`、`data.event.action.option`、
  `data.event.context.open_chat_id`、`data.event.context.open_message_id`。
  **读顶层不报错，只会静默得到全 `None`** —— 2026-10-14 就是这样丢了 4 次真实点击，
  详见 [`feishu-bringup.md`](feishu-bringup.md) §4。
- 发卡片：`POST /im/v1/messages`（`msg_type=interactive`）；
  更新卡片：`PATCH /im/v1/messages/:message_id`
- 回文本：`POST /im/v1/messages/:message_id/reply`（`ReplyMessageRequest`，`msg_type=text`，
  `content` 是 `{"text":"…"}` 的 JSON 字符串）—— 回在**原消息线程**里，不刷屏
- 卡片 JSON 用 **2.0**（`"schema": "2.0"`）；按钮回调写成
  `"behaviors": [{"type": "callback", "value": {"a": "...", "v": ...}}]`

---

## 13. 底部常驻菜单（机器人自定义菜单）

让控制项常驻在输入区（Telegram 风格）。**服务端零改动**：菜单项的响应动作选
**「发送文字消息」**，点菜单＝发一条文本，走的正是 §10 验收过的文本路径 ——
同样的解析、同样的去抖、同样的回执、同样的补卡。

配置全在控制台（**没有 OpenAPI**；`im/v1/chats/:chat_id/menu_tree` 是**群菜单**，与机器人菜单无关）：

1. **机器人能力 → 机器人自定义菜单** → **菜单状态**开启。三个选项：
   - **样式**：选**可切换菜单**（在菜单与输入框之间切换；另一种「悬浮菜单」是悬浮在
     输入框上方、不影响输入，可配置的菜单更多）。可切换菜单限 **3 个主菜单 × 5 个子菜单**。
   - **默认展示**：选**菜单**（打开会话先看到菜单）。
   - **菜单配置**：建 3 个主菜单，每个主菜单下加子菜单。
2. 每一项：**响应动作** → **发送文字消息** → 文字**逐字照抄**（不要加空格）。
3. **创建新版本并发布**，等约 5 分钟生效。

| 主菜单 | 子菜单（显示名）| 发送文字消息（照抄）|
|---|---|---|
| 电源 | 开机 | `开机` |
| 电源 | 关机 | `关机` |
| 温度 | 升2度 | `调高两度` |
| 温度 | 降2度 | `调低两度` |
| 温度 | 设为24度 | `调到24度` |
| 更多 | 状态 | `状态` |
| 更多 | 风速最大 | `风速最大` |
| 更多 | 风速自动 | `风速自动` |
| 更多 | 制冷24度 | `制冷24度` |
| 更多 | 制热26度 | `制热26度` |
| 更多 | 设置卡片 | `设置卡片` |

> - 上表每条文字都过了解析器；**`高风` 不能用**（解析成 `unclear`，高风位要用 `风速最大`）。
> - `设置卡片` 是**召唤词**（在解析器之前匹配）：新发一张卡片到最新位置并撤回旧卡，
>   不发文字回执。旧召唤词 `/ac`、`空调` 已退场 —— 现在只回「没听懂这句。…」。
> - 主菜单带子菜单时**它本身不响应点击**（飞书规则）。
> - **「发送文字消息」需客户端 ≥ 7.22**，低版本该菜单项**直接不显示** ——
>   表现为「配置了却看不到」，不是服务故障（服务侧看日志：点一次有没有
>   `text message from …`）。可切换样式本身需 ≥ 5.27。
> - 不需要新权限、不需要改服务、不需要重启。
