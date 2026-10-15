# 实施计划：预设 · 天气建议 · DHT11 反馈与夜间闭环 · 固件失联保护

设计（What/Why）：[`../specs/2026-10-15-presets-weather-dht11-design.md`](../specs/2026-10-15-presets-weather-dht11-design.md)

- 目标读者：**没见过本仓库、也没读过设计的人**。每个任务给出**文件、接口、签名、要写的测试、要跑的命令**。
- 纪律：**TDD**（先写失败测试）、**DRY**、**YAGNI**、**小步提交**。
- 两个仓库：`AirConditionRemoteServer`（Python，本仓）+ `AirConditionRemoteCTL`（ESP32）。
- 唯一跨仓接口：**MQTT 契约**。服务器决策、固件感知——边界不变。

## 不变约束（每次改动都要守住）

1. 真值在 ESP32 的 `ac/state`（retained）；服务只镜像 + 乐观更新。
2. 一切控制动作最终**只能走一条 `ac/cmd`**，经 `merge_actions` 合并。
3. 纯逻辑与 I/O 分离：`state/cards/actions/intent/replies/store` 及新增纯模块不 import 飞书/MQTT/broker。
4. 新逻辑**零外部依赖**、可离线单测（照 `python -m pytest -q`）。
5. 任何新增文本指令**整句精确匹配**，防误触发优先。
6. 服务侧先校验，不依赖固件兜底。

---

## 阶段 0 · 冻结设计 + 计划（本文）

- [x] 设计封版，落到 `docs/superpowers/specs/2026-10-15-presets-weather-dht11-design.md`
- [x] 本计划

---

## 阶段 1 · 存储与纯模型（服务器，可单测）

### T1.1 `store.py`：卡片按种类寻址 + 新表
- `card` 表改为 `card(owner, kind, message_id)`，`kind ∈ {remote, preset, settings, weather}`；**开机自动迁移**旧库（旧行记为 `remote`）。
- 保留 `get_message_id(owner)`（兼容 = `kind='remote'`）与会新增 `get_card(owner, kind)` / `set_card(owner, kind, message_id)` / `clear_card(owner, kind)`。
- 新表：`preset`、`schedule`、`env_history`、`settings`（字段见设计 §10）。
- 测试：`tests/test_store.py` 追加迁移、按类寻址、preset CRUD、schedule 状态流转、env_history 读写与聚合源、settings get/put。

### T1.2 `<新> app/preset.py`：预设模型与语义（纯）
- `@dataclass(frozen=True) Preset: id, name, power, mode, temp, fan, timer_enabled, timer_at(HH:MM|None)`
- `normalize_name(text) -> str`（trim + 折叠空白；1~12 字）
- `actions(preset) -> tuple[(str,int)...]` —— 恒含四项（power/mode/temp/fan，设计 §5.1）
- `resolve(preset, now_local) -> Resolve`，`Resolve = ("immediate", None) | ("at", epoch)`：
  - 未开定时 → immediate；开了定时且今天该刻未到 → at；已过 → immediate。
- 测试：`tests/test_preset.py`。

### T1.3 `<新> app/schedule.py`：一次性计划 + 每日推送时刻（纯）
- `at_today(hhmm, now_local) -> epoch`
- `is_overdue(run_at, now, grace_s=900) -> bool`
- `next_daily(hhmm, now_local) -> epoch`（每日「推送时间」用）
- 测试：`tests/test_schedule.py`（含跨零点、已过、超 grace）。

### T1.4 `config.py`：新增默认值（设计 §11）
- `dht_record_s=300`、`night_default_target=26`、`night_default_start="23:00"`、`night_default_end="07:00"`、`weather_default_push="17:00"`、`hb_interval_s=30`、`weather_lat/lon=""`（可选覆盖）。
- 测试：`tests/test_config.py` 追加。

---

## 阶段 2 · 纯决策逻辑（服务器，可单测）

### T2.1 `<新> app/weather.py`：推荐（纯）+ 取数（薄 I/O）
- `recommend(outdoor_c, trend_delta_c=0) -> Recommendation(mode, temp, fan, note)`
  - 阈值表：`≥35→26`、`30~35→25`、`26~30→24`、`18~26→送风`、`<18→制热`；
  - `trend_delta_c <= -2`（未来两小时明显降温）→ 温度**上调 1℃**；夹到 16~30。
- `parse_open_meteo(payload) -> WeatherNow(outdoor_c, humidity, next2h_delta_c)`（纯，喂固定 JSON）
- `geolocate/fetch` 放同一模块但**拆函数**，测试只打纯函数。
- 测试：`tests/test_weather.py`。

### T2.2 `<新> app/env.py`：环境读数与历史聚合（纯）
- `EnvReading(temp, humidity, ok)`；`parse_env(payload) -> EnvReading`
- `aggregate(rows, *, now, window) -> Summary`，`window ∈ {current, tonight, day, week, all}`
  - `current`=最近一条；`tonight`=最近一次 `night_start~night_end`；`day`=本地今日 00:00 起；`week`=最近 7 天；`all`=全表。
  - 只统计 `ok=1`；空窗口返回明确「无数据」标记。
- 测试：`tests/test_env.py`。

### T2.3 `<新> app/control.py`：夜间闭环决策（纯）
- `decide(room, history, forecast_delta, target, ac, now, state) -> Decision(actions|[], reason)`
  - 死区 ±1℃；迟滞分离开/关阈值；最小动作间隔 600s；风速恒 `low`（夜间硬规则）；温度夹 16~30。
  - `room > target+1` → 确保开机 + 设定温度=target（按 mode 制冷/制热）；
  - `room < target-1` → 抬设定温度；`≤ target-1` 已开机则**关机**；
  - 带内 → 不动；`history` 显示已朝目标走 → 少干预。
  - 入参 `state` 承载 `last_action_at`、`manual_until`（手动让位）。
- 测试：`tests/test_control.py`（含防抖/迟滞/风速锁/让位）。

---

## 阶段 3 · 文本与回执（服务器，可单测）

### T3.1 `intent.py`：新类目（整句精确匹配，排在最前）
- `Kind` 增 `history | night | weather | preset`。
- 历史：`当前/今夜/一天/一周/全部`（整句）；夜间：`开夜间/关夜间/夜间状态`；天气：`天气/今天天气/建议`；预设：`预设/设置预设`；
- 套用：`用<name>` / `套用<name>` → `Intent(kind="preset", hint="apply:<name>")`。
- 测试：`tests/test_intent.py` 追加，**并断言这些词不会误触发动作**。

### T3.2 `replies.py`：新回执
- `render` 增 branch：`history`（接受 Summary）、`night`（开关确认）、`weather`（建议文案）、`preset`（预设卡提示 / 套用回执）。
  - 需要上下文的（history/weather/preset apply）走**新的显式函数**（`render_history(summary)` 等），避免给 `render` 塞可选大参。
- 更新 `HELP_TEXT`（新增行）+ `tests/test_help_text.py` 的 `EXAMPLES`（有意例外，设计 §18.9）。
- 测试：`tests/test_replies.py` 追加。

---

## 阶段 4 · 卡片（服务器，可单测）

### T4.1 `cards.py`：预设卡 / 预设表单卡 / 设置卡 / 建议卡 / 历史卡
- `build_preset_card(presets, status="")`；每条按钮 `preset_apply:<id>` + `preset_edit:<id>` / `preset_delete:<id>`（带 `confirm`）；`preset_new`。
- `build_preset_form(preset=None, status="")`：`form` 根层 + `input(name)` + `select_static(mode/fan/temp/power)` + `picker_time(at)` + `form_submit` 按钮。
- `build_settings_card(settings_map, status="")`：推送时间、夜间开关/时间段/设定值。
- `build_weather_card(now, rec, trend, status="")`：`[立即执行 weather_exec][忽略 weather_ignore]`。
- `build_history_card(summary)`（可选，先做文字；卡片留后 → 本阶段只做函数骨架 + 测试）。
- 测试：`tests/test_cards.py` 追加（结构 + 回调值 + 表单 name/编码）。

### T4.2 `actions.py`：UI 动作词表 + 表单归一化
- 新增 `normalize_ui_action(value, form_value, card_name) -> UIAction | None`（`preset_open/new/edit/delete/apply`、`settings_open`、`night_toggle`、`weather_exec/ignore`、`preset_save`、`settings_save`）。
- AC 动作仍走 `normalize_action`。**UI 动作绝不进 AC 队列**（由服务同步处理）。
- 测试：`tests/test_actions.py` 追加。

---

## 阶段 5 · 服务接线（服务器）

### T5.1 `mqtt_client.py`
- 订阅 `<prefix>/env`；新增 `on_env` 回调（带 `retained` 标志）。
- 发布 `<prefix>/hb`（QoS0，非 retained）——`publish_heartbeat()`。
- 订阅 `<prefix>/event`；`on_event` 回调。

### T5.2 `main.py`
- 卡片按种类寻址：`remote/preset/settings/weather` 各一条消息，各自 patch；回调按词前缀路由，不再改写 remote 指针。
- `_on_card_action` 透传 `form_value`；分派 AC 动作 vs UI 动作。
- 新增 `EnvState`（最近读数 + 到达时间）与 `_on_mqtt_env`（**忽略 retained 重放**）。
- `_tick` 扩展：每 5 分钟落 `env_history`；到「推送时间」推天气卡；跑 `schedule` 到期任务；跑夜间闭环 tick；每 30s 发 `ac/hb`。
- 手动让位：所有用户发起的 AC 动作记 `last_manual_at`。

### T5.3 契约与配置
- `.env.example` 增新键；`docs/interface-spec.md` §4 表增 `env/hb/event`；§7 配置表。

---

## 阶段 6 · 固件（`AirConditionRemoteCTL`）

### T6.1 DHT11 感知
- `pins.h`: `#define PIN_DHT 4`
- `config.h`: `DHT_READ_MS 30000`、`LINK_LOST_OFF_MS 300000`、`HB_TIMEOUT_MS 90000`、`HB_INTERVAL_S 30`
- `<新> src/dht_sensor.{h,cpp}`：`dht_begin()/dht_read(&temp,&hum)`
- `platformio.ini`: `beegee-tokyo/DHTesp`；`selftest` 的 `build_src_filter` 排除 `dht_sensor.cpp`
- `net_mqtt`: 发布 `<prefix>/env`（retained）

### T6.2 失联保护
- `net_mqtt`: 订阅 `<prefix>/hb`，记录 `s_lastHbMs`；暴露 `net_service_alive()`。
- `main.cpp`: `link_ok() = net_connected() && hb 新鲜`；连续失联 ≥5 分钟 → 发一帧关机 + `g_state.power=0` + 发 `<prefix>/event {"type":"failsafe_off","prev":{...}}`（retained）。
- 开机保护：`arm` 需**收到过第一条 hb**。

### T6.3 OLED 改版
- `display`: 4 页轮播（空调/环境/网络/夜间），局部刷新不闪屏，顶部常驻动作提示。

---

## 阶段 7 · 文档与验收

- `docs/wiring.md` 加 DHT11 接线表；`README.md`（两仓）状态表更新；`docs/feishu-setup.md` 菜单表更新。
- 跑两仓验收：服务器 `python -m pytest -q` 全绿；固件 `pio run` 编译通过。

---

## 交付顺序与提交粒度

1. 每完成一个 T，跑一次 `python -m pytest -q`；全绿再进下一个。
2. 每个阶段结束提交一次（`feat(scope): ...`，中文正文照本仓既有风格）。
3. 固件改动单独提交（另一仓库）。

---

## 执行状态（2026-10-15 夜）

**阶段 1–6 全部落地并上线；阶段 7 的文档本轮补齐。全程没有改动任何代码、也没有改任何既有断言。**

| 阶段 | 状态 |
|---|---|
| 0 冻结设计 | ✅ |
| 1 存储与纯模型 | ✅ |
| 2 纯决策逻辑 | ✅ |
| 3 文本与回执 | ✅ |
| 4 卡片 | ✅ |
| 5 服务接线 | ✅ 已部署到树莓派（`/opt/ac-bot`）并运行中 |
| 6 固件 | ✅ 已烧录（DHT11 出真值、红外到空调、OLED 正常）|
| 7 文档与验收 | 🟡 文档本轮补齐；**真机验收未走完**（见下）|

### 服务器验收（设计 §14）逐条

| # | 项 | 状态 | 依据 |
|---|---|---|---|
| 1 | 新建预设 → 套用 → 一条 `ac/cmd` | ✅ | 21:52:36「秋天-10」→ 已套用；`ac/state` 字段全对 |
| 2 | 挂定时：已过点→立即 / 未到点→预约 | 🟡 | 「已过点→立即」✅（秋天-10 挂 15:00、21:52 点它）；「未来到点自动执行」未验 |
| 3 | 预设定时不会自己触发 | ✅ | 秋天-10 挂的就是 15:00，15:00 前后日志无任何命令 |
| 4 | 到点自动推建议卡 / 立即执行 / 忽略 | ❌ | 当天 17:00 没推出来，且**失败不重试**（设计 §18.12.3）|
| 5 | `env` 每 30s、历史每 5 分钟、汇总回复 | ✅ | `env_history` 41 行、5 分钟一条；历史卡与文字回执均正常 |
| 6 | 夜间闭环（含 07:00 退出、风速锁低）| 🟡 | 闭环在跑（21:00–07:00 / 26℃）；**动作对不对没验** |
| 7 | 防抖（达标带内不动、最小动作间隔）| 🟡 | 「未确认」假警报提示需要复看（设计 §18.12.1）|
| 8 | 失联 5 分钟 → 关机 + `power=0` | ❌ | 真机未测（要人在空调旁，且会真关空调）|
| 9 | 开机保护（没连过不关）| ✅ | 启动日志 `[failsafe] armed (first contact)` |
| 10 | 失联关机后再联网 → 恢复 | ❌ | 真机未测 |
| 11 | 断网读失败 → `ok:false`，不记旧值 | 🟡 | 单测覆盖；真机未验（库里全是 `ok=1`）|
| 12 | 既有单测保持全绿 | ✅ | `python -m pytest -q` → **373 passed** |

### 两仓验收命令

```powershell
python -m pytest -q              # 服务端：应 373 passed
python -m platformio run         # 固件：应编译通过（无代码改动，本轮未重跑）
```
