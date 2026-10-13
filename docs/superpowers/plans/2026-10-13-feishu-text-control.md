# 飞书「对话控制」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让私聊机器人听懂人话（「调低两度」「开机制冷24度」），产出与卡片按钮**完全相同**的动作序列，复用同一条 MQTT 管道。

**Architecture:** 新增纯函数模块 `app/intent.py` 把文本解析成带标签的 `Intent`（`kind` + `(action, value)` 元组）；`app/replies.py` 把 `Intent + 状态` 渲染成回执文本；`app/main.py` 只做分派 —— 把动作塞进**已有的**动作队列。因此 `state.py` / `cards.py` / `mqtt_client.py` **一行不改**，去抖合并、乐观更新、确认超时全部自动继承。

**Tech Stack:** Python 3.10+、pytest、lark-oapi（新增 `im.v1.message.reply`）、paho-mqtt（不改）、SQLite。

**Spec:** `docs/superpowers/specs/2026-10-13-feishu-text-control-design.md`（本计划实现它的 §1–§9）

## Global Constraints

- Python 3.10+；**不得新增任何依赖**（不许改 `pyproject.toml` 的 `dependencies`）。
- 设计文档 §10 的 14 条决定是硬约束，尤其 D12：**形容词句永不产生动作**。
- `Kind` 只有五个值：`actions` / `query` / `help` / `unclear` / `suggest`。不许加。
- 归一化必须去掉 emoji 与零宽字符 —— `开🥶机` ≡ `开机`（spec §4.3「噪声」的必要条件）。
- 匹配原则：**匹配即消费**（片段被消费后不再被其它规则命中）；**最长匹配优先，同长时风速优先**。
- 动作输出顺序固定为 **power → mode → temp → fan**；同字段族（含 `temp`/`temp_step`）**后说的赢**，只保留一个。
- 一条消息最多产生一条 `ac/cmd`：复用现有去抖，**不许新写合并逻辑**。
- 现有 **78 项测试必须全绿且一行都不改**。若需要改，说明碰了不该碰的地方 —— 停下汇报。
- 回执文案逐字固定（spec §5.4），测试里写死字符串。
- 非 owner 一律**不回**任何东西。
- 测试必须零外部依赖：不需要 broker、不需要飞书凭据、不需要网络（已实测 lark SDK 可离线构造）。

## Review Focus

spec 没写、但真上手会咬人的五类输入，各自的测试挂在对应任务的步骤里：

1. **畸形/非文本事件** —— content 不是合法 JSON、或 `message_type != "text"`：必须静默忽略，不能抛异常打断长连接（`feishu_client._on_message` 是唯一守住这里的地方）。→ Task 6
2. **同一条消息重投** —— `temp_step` 非幂等，重投必须被 `mark_seen` 挡住，而 `state.py` 不会兜底。→ Task 5、Task 7
3. **空/纯噪声消息** —— `""`、`"   "`、`"😀"`：判 `unclear` 回帮助，不崩、不索引越界。→ Task 3
4. **一句里温度说两遍** —— 「调到24，算了26」（绝对 + 绝对）、「调到24再低两度」（绝对 + 相对）：必须**确定性**地只取最后出现的那个，绝不能同时产出 `temp` 与 `temp_step`。→ Task 2
5. **`开到` 后面不是数字** —— 「开到最大风」不能凭空空造出 `temp`；应退化成「开」+「最大风」＝ `power=1` + `fan=3`。→ Task 2

---

## 文件结构

| 文件 | 状态 | 职责 |
|---|---|---|
| `app/intent.py` | 新建 | 文本 → `Intent`。纯函数，本设计的核心。 |
| `app/replies.py` | 新建 | `Intent + AcState + Presence` → 回执文本。纯函数。 |
| `tests/test_intent.py` | 新建 | 意图解析单测 |
| `tests/test_replies.py` | 新建 | 回执文案单测 |
| `tests/test_text_control.py` | 新建 | `Service` 层的分派/去重/补卡/回执接线 |
| `app/store.py` | 改 | 加 `mark_seen`（message_id 去重） |
| `tests/test_store.py` | 改 | 追加 `mark_seen` 测试 |
| `app/feishu_client.py` | 改 | `TextHandler` 加 `message_id`；加 `reply_text` |
| `tests/test_feishu_client.py` | 新建 | 消息分派与 `reply_text` 的桩测试 |
| `app/config.py` | 改 | 加 `text_control_enabled` |
| `app/main.py` | 改 | `_on_text_message` 改成分派接线 |
| `README.md`、`docs/interface-spec.md`、`.env.example` | 改 | 文档同步 |

---

### Task 1: `intent.py` 骨架 —— 归一化 + 开关机 + 模式 + 风速

**Files:**
- Create: `app/intent.py`
- Create: `tests/test_intent.py`

**Interfaces:**
- Consumes: `app.state` 的 `AC_MODE` / `AC_FAN`（复用既有枚举值，不要重新发明数字）
- Produces:
  - `Kind = Literal["actions", "query", "help", "unclear", "suggest"]`
  - `Intent`：frozen dataclass，字段 `kind: Kind`、`actions: tuple[tuple[str, int], ...] = ()`、`hint: str = ""`（**按此顺序**，位置参数可用）
  - `normalize(text: str) -> str`
  - `parse(text: str) -> Intent`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_intent.py
"""Unit tests for app.intent -- text -> Intent (spec 4.x)."""

from app.intent import Intent, normalize, parse


def test_normalize_folds_width_case_and_noise():
    assert normalize("２４度") == "24度"
    assert normalize("开🥶机") == "开机"
    assert normalize(" COOL。 ") == "cool"


def test_power_on():
    assert parse("开机") == Intent(kind="actions", actions=(("power", 1),))


def test_power_on_aliases():
    for text in ("开空调", "打开空调", "开一下", "启动"):
        assert parse(text).actions == (("power", 1),), text


def test_power_off_aliases():
    for text in ("关机", "关掉", "关闭", "停了", "不用了"):
        assert parse(text).actions == (("power", 0),), text


def test_modes():
    assert parse("制冷").actions == (("mode", 1),)
    assert parse("冷风").actions == (("mode", 1),)
    assert parse("除湿").actions == (("mode", 2),)
    assert parse("抽湿").actions == (("mode", 2),)
    assert parse("送风").actions == (("mode", 3),)
    assert parse("制热").actions == (("mode", 4),)
    assert parse("暖风").actions == (("mode", 4),)
    assert parse("自动").actions == (("mode", 0),)


def test_fan_levels():
    assert parse("风速最大").actions == (("fan", 3),)
    assert parse("大风").actions == (("fan", 3),)
    assert parse("中风").actions == (("fan", 2),)
    assert parse("低风").actions == (("fan", 1),)
    assert parse("自动风").actions == (("fan", 0),)


def test_auto_alone_is_a_mode_but_auto_wind_is_a_fan_level():
    assert parse("自动").actions == (("mode", 0),)
    assert parse("自动风").actions == (("fan", 0),)


def test_cold_wind_is_a_mode_alias_not_a_fan_level():
    assert parse("冷风").actions == (("mode", 1),)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_intent.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.intent'`

- [ ] **Step 3: 实现 `app/intent.py`**

要点（其余留给实现者）：
- 别名表用**有序元组**，扫到即从文本里删掉该片段（匹配即消费）。
- 风速档位词 = `最大/大/强/中/中档/小/低/弱/自动`，且必须与 `风` 或 `档` 相邻才算风速；
  模式别名（`制冷/冷风/除湿/抽湿/送风/吹风/制热/暖风/热风/加热/自动`）单独一张表。
- 两张表都命中同一片段时**取更长的匹配**（`自动风` 赢过 `自动`）。
- 这一版还没实现温度、查询、帮助、形容词 —— 它们返回 `Intent(kind="unclear")`，由 Task 3 补上。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_intent.py -q`
Expected: PASS（8 项）

- [ ] **Step 5: 确认没碰坏既有测试**

Run: `python -m pytest -q`
Expected: PASS（78 + 8 = 86 项）

- [ ] **Step 6: Commit**

```bash
git add app/intent.py tests/test_intent.py
git commit -m "feat(intent): text parser skeleton -- normalise, power, mode, fan"
```

---

### Task 2: `intent.py` 温度 —— 绝对、相对、中文数字、`开到 N`

**Files:**
- Modify: `app/intent.py`
- Modify: `tests/test_intent.py`

**Interfaces:**
- Consumes: Task 1 的 `normalize` / 消费式匹配 / 动作顺序规则
- Produces: 无新符号；`parse` 开始产出 `("temp", int)` 与 `("temp_step", int)`

- [ ] **Step 1: 写失败的测试**（追加到 `tests/test_intent.py`）

```python
def test_absolute_temperature():
    assert parse("调到24度").actions == (("temp", 24),)
    assert parse("设成26℃").actions == (("temp", 26),)
    assert parse("温度24").actions == (("temp", 24),)
    assert parse("24度").actions == (("temp", 24),)


def test_out_of_range_passes_through_for_downstream_clamping():
    # 夹取是 state.py 的职责（spec 5.4「发生夹取」一项）；intent 不越权改值
    assert parse("调到99度").actions == (("temp", 99),)
    assert parse("调到0度").actions == (("temp", 0),)


def test_relative_temperature_aliases():
    assert parse("调低一点").actions == (("temp_step", -1),)
    assert parse("凉快一点").actions == (("temp_step", -1),)
    assert parse("高一点").actions == (("temp_step", 1),)


def test_relative_temperature_with_chinese_numbers():
    assert parse("调低两度").actions == (("temp_step", -2),)
    assert parse("调低二度").actions == (("temp_step", -2),)
    assert parse("升三度").actions == (("temp_step", 3),)


def test_chinese_number_needs_a_measure_word():
    # 「一直」里的「一」不能被当成数字（spec 4.1 第 3 条）——只剩「开」触发的 power
    assert parse("一直开着").actions == (("power", 1),)


def test_kaidao_carries_power_and_temperature_exactly_once():
    assert parse("开到26") == Intent(kind="actions", actions=(("power", 1), ("temp", 26)))
    assert parse("开到26度").actions == (("power", 1), ("temp", 26))


def test_kaidao_without_a_number_does_not_invent_temperature():
    # Review Focus 5
    assert parse("开到最大风").actions == (("power", 1), ("fan", 3))


def test_last_temperature_wins():
    # Review Focus 4
    assert parse("调到24，算了26").actions == (("temp", 26),)
    assert parse("调到24再低两度").actions == (("temp_step", -2),)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_intent.py -q`
Expected: FAIL —— 新增的这些断言里，温度全部落到 `unclear`

- [ ] **Step 3: 实现温度解析**

要点：
- 中文数字表 `一~十` + `两`；**只在量词（`度`/`℃`）或温度动词（`调到`/`升`/`降`/`低`/`高`）紧邻时才转换**。
- 相对温度：`(调低|降|低|凉快|冷) + (N|一点)` → `temp_step -N`；`(调高|升|高|暖|热) + (N|一点)` → `+N`；`一点` 记 `1`。
- `开到|开成 + 数字` → 展开为 `power=1` + `temp=N`，并且**整段消费**（不再让 `开` 单独触发一次 power）。
- 同字段族只保留最后一个（用「后写覆盖」的字典，最后按 power/mode/temp/fan 固定顺序输出）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_intent.py -q`
Expected: PASS（16 项）

- [ ] **Step 5: Commit**

```bash
git add app/intent.py tests/test_intent.py
git commit -m "feat(intent): temperature -- absolute, relative, Chinese numerals, 开到 N"
```

---

### Task 3: `intent.py` 分派与边界 —— query / help / unclear / suggest / 否定 / 组合

**Files:**
- Modify: `app/intent.py`
- Modify: `tests/test_intent.py`

**Interfaces:**
- Produces（本任务之后 `Intent` 才算完整）：
  - `parse("状态")` → `Intent(kind="query")`
  - `parse("帮助")` → `Intent(kind="help")`
  - `parse("太热了")` → `Intent(kind="suggest", hint="hot")`
  - 纯否定句 → `Intent(kind="actions", actions=(), hint="negated:<field>")` —— **`kind` 仍是 `actions`、`actions` 为空**，这是唯一的例外，`replies.py` 要认它
  - `hint` 是**逗号分隔的 token 串**，如 `"negated:mode"`、`"negated:mode,negated:fan"`

- [ ] **Step 1: 写失败的测试**

```python
def test_query():
    for text in ("状态", "现在多少度", "开着吗", "几度"):
        assert parse(text) == Intent(kind="query"), text


def test_help():
    for text in ("帮助", "怎么用", "能做什么"):
        assert parse(text) == Intent(kind="help"), text


def test_unclear_inputs_never_crash():
    # Review Focus 3
    for text in ("", "   ", "你好", "2", "空调🥶", "😀"):
        assert parse(text).kind == "unclear", text


def test_suggest_is_direction_aware():
    assert parse("太热了") == Intent(kind="suggest", hint="hot")
    assert parse("有点热") == Intent(kind="suggest", hint="hot")
    assert parse("有点冷") == Intent(kind="suggest", hint="cold")
    assert parse("好冷") == Intent(kind="suggest", hint="cold")
    assert parse("有点闷") == Intent(kind="suggest", hint="")


def test_suggest_never_carries_an_action():
    # spec D12 -- 这条是反目标「误触发」的守护测试
    for text in ("太热了", "有点冷", "有点闷"):
        assert parse(text).actions == (), text


def test_bare_number_is_not_a_temperature():
    assert parse("2").kind == "unclear"
    assert parse("风速2档").actions == (("fan", 2),)


def test_negation_drops_the_action_and_records_it():
    assert parse("不要制冷") == Intent(kind="actions", hint="negated:mode")
    assert parse("不要制冷，开24度").actions == (("temp", 24),)
    assert parse("不要制冷，开24度").hint == "negated:mode"


def test_multiple_actions_are_canonically_ordered():
    assert parse("开机制冷24度") == Intent(
        kind="actions", actions=(("power", 1), ("mode", 1), ("temp", 24))
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_intent.py -q`
Expected: FAIL —— `query` / `help` / `suggest` / 否定 都还没实现

- [ ] **Step 3: 实现分派**

要点：
- **先做否定预处理**：`(不要|别|不用|不需要)` 紧邻动作词 → 消费该片段、把字段记进 `negated:` token，**不**产出动作。
- 分派顺序（在归一化之后、动作解析之前）：查询词 → 帮助词 → 形容词 → 动作解析 → 兜底 `unclear`。
- 形容词表：`hot` = `太热|好热|有点热|热死|闷热`；`cold` = `太冷|好冷|有点冷|冻死`；`""` = `闷|不舒服|难受`。
- 有动作就 `kind="actions"`（**即使 `actions` 为空、只因否定**）；没动作又不是上面任何一类 → `unclear`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_intent.py -q`
Expected: PASS（24 项）

- [ ] **Step 5: Commit**

```bash
git add app/intent.py tests/test_intent.py
git commit -m "feat(intent): dispatch query/help/suggest/unclear, negation, combinations"
```

---

### Task 4: `app/replies.py` —— 回执文案

**Files:**
- Create: `app/replies.py`
- Create: `tests/test_replies.py`

**Interfaces:**
- Consumes: `Intent`（Task 1–3）、`app.state` 的 `AcState` / `Presence` / `apply_action`、`app.cards` 的 `MODE_LABELS` / `FAN_LABELS`（**复用既有标签表，不要另写一份**）
- Produces:
  - `HELP_TEXT: str`
  - `render(intent: Intent, state: AcState, presence: Presence, *, stale: bool = False, updated_at: str = "--:--:--") -> str`
  - 辅助（导出以便单测）：`state_line(state: AcState) -> str`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_replies.py
"""Unit tests for app.replies -- Intent + state -> receipt text (spec 5.4)."""

from app.intent import parse
from app.replies import HELP_TEXT, render
from app.state import AcState, Presence

ON = AcState(power=1, mode=1, temp=24, fan=0)
OFF = AcState(power=0, mode=1, temp=24, fan=0)
ONLINE = Presence(online=True)


def test_success_line_shows_the_whole_state_after_the_command():
    assert render(parse("开机"), OFF, ONLINE) == "✅ 已开机 · 制冷 · 24℃ · 自动风"


def test_clamped_value_is_explained():
    assert render(parse("调到99度"), ON, ONLINE) == \
        "✅ 已开机 · 制冷 · 30℃ · 自动风（温度范围 16~30）"


def test_setting_temperature_while_off_is_flagged():
    assert render(parse("调到24度"), OFF, ONLINE) == \
        "✅ 已关机 · 制冷 · 24℃ · 自动风（当前关机，开机后生效）"


def test_offline_device_is_flagged():
    assert render(parse("开机"), OFF, Presence(online=False)) == \
        "✅ 已开机 · 制冷 · 24℃ · 自动风（设备离线，可能不生效）"


def test_negated_only_message_gets_a_short_ack():
    assert render(parse("不要制冷"), ON, ONLINE) == "好，模式没动"


def test_query_reports_the_device_truth():
    assert render(parse("状态"), ON, ONLINE, updated_at="12:03:41") == \
        "现在 开机 · 制冷 · 24℃ · 自动风 · 🟢 在线（更新于 12:03:41）"


def test_help_and_unclear():
    assert render(parse("帮助"), ON, ONLINE) == HELP_TEXT
    assert render(parse("你好"), ON, ONLINE) == f"没听懂这句。{HELP_TEXT}"


def test_suggest_wording_follows_the_direction():
    assert render(parse("太热了"), ON, ONLINE) == "看着有点热？说「制冷 22 度」，或点卡片上的「制冷」。"
    assert render(parse("有点冷"), ON, ONLINE) == "有点冷？说「制热 26 度」，或点卡片上的「制热」。"
    assert render(parse("有点闷"), ON, ONLINE) == "要调一下吗？说「制冷 22 度」或「风速最大」。"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_replies.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.replies'`

- [ ] **Step 3: 实现 `app/replies.py`**

要点：
- 成功行 = `"✅ " + state_line(动作作用后的状态)`；`state_line` = `开机/关机 · 模式 · N℃ · 风速风`。
- 修饰后缀按固定顺序拼接、用 `（…）` 包住、多个用 `；` 连接：**夹取 → 否定 → 关机 → 离线**。
- 「夹取」的判定：对该动作调 `apply_action(state, name, value)`，若温度被改小/改大则说明发生了夹取。
  注意目标状态要**逐个动作**应用（复用 `apply_action`，不要自己写夹取）。
- `HELP_TEXT` 逐字：`能听懂这些：开机 / 关掉 / 调到24度 / 制冷 / 风速最大 / 状态`

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_replies.py -q`
Expected: PASS（9 项）

- [ ] **Step 5: Commit**

```bash
git add app/replies.py tests/test_replies.py
git commit -m "feat(replies): receipt text for every intent kind"
```

---

### Task 5: `store.mark_seen` —— message_id 去重

**Files:**
- Modify: `app/store.py`
- Modify: `tests/test_store.py`

**Interfaces:**
- Produces: `CardStore.mark_seen(self, message_id: str) -> bool`（**首次见到返回 `True`，重复返回 `False`**）
- 表：`seen(message_id TEXT PRIMARY KEY, at REAL NOT NULL)`

- [ ] **Step 1: 写失败的测试**（追加到 `tests/test_store.py`）

```python
def test_mark_seen_is_true_only_the_first_time():
    store = CardStore(":memory:")
    assert store.mark_seen("om_1") is True
    assert store.mark_seen("om_1") is False


def test_mark_seen_keeps_ids_separate():
    store = CardStore(":memory:")
    assert store.mark_seen("om_1") is True
    assert store.mark_seen("om_2") is True


def test_mark_seen_survives_reopening_the_database_file(tmp_path):
    path = tmp_path / "ac-bot.sqlite3"
    CardStore(path).mark_seen("om_1")
    assert CardStore(path).mark_seen("om_1") is False


def test_mark_seen_ignores_an_empty_id():
    # 空 id 说明上游没给 message_id，不能把它当成「已经见过」
    store = CardStore(":memory:")
    assert store.mark_seen("") is True
    assert store.mark_seen("") is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_store.py -q`
Expected: FAIL —— `AttributeError: 'CardStore' object has no attribute 'mark_seen'`

- [ ] **Step 3: 实现 `mark_seen`**

要点：`_SCHEMA` 追加建表语句（幂等 `IF NOT EXISTS`，老库直接升级）；
空字符串**提前返回 `True` 且不落库**；用 `INSERT ... ON CONFLICT DO NOTHING` + `rowcount` 判断是否首次。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_store.py -q`
Expected: PASS（10 项）

- [ ] **Step 5: Commit**

```bash
git add app/store.py tests/test_store.py
git commit -m "feat(store): dedupe incoming messages by message_id"
```

---

### Task 6: `feishu_client` —— 透传 `message_id` 与 `reply_text`

**Files:**
- Modify: `app/feishu_client.py`
- Create: `tests/test_feishu_client.py`

**Interfaces:**
- 破坏性变更：`TextHandler` 变成 `Callable[[str, str, str, str], None]` —— `(open_id, chat_id, message_id, text)`
- Produces: `FeishuClient.reply_text(self, message_id: str, text: str) -> bool`
- 实现细节：用 `ReplyMessageRequest` / `ReplyMessageRequestBody`（`msg_type="text"`、`content` 是 `{"text": ...}` 的 JSON 串）

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_feishu_client.py
"""Unit tests for app.feishu_client -- no network, no credentials."""

import types

from app.feishu_client import FeishuClient


def make_client():
    """A client whose API surface is stubbed out (offline, no real calls)."""
    client = FeishuClient(app_id="cli_test", app_secret="secret")
    calls = {}
    response = types.SimpleNamespace(
        success=lambda: True, code=0, msg="ok",
        data=types.SimpleNamespace(message_id="om_reply"),
    )

    def reply(request):
        calls["reply"] = request
        return response

    client._api = types.SimpleNamespace(
        im=types.SimpleNamespace(v1=types.SimpleNamespace(
            message=types.SimpleNamespace(reply=reply)))
    )
    return client, calls


def test_reply_text_calls_the_reply_api_and_reports_success():
    client, calls = make_client()
    assert client.reply_text("om_1", "✅ 已开机") is True
    assert calls["reply"].message_id == "om_1"


def test_reply_text_reports_failure_without_raising():
    client, _ = make_client()
    client._api.im.v1.message.reply = lambda request: types.SimpleNamespace(
        success=lambda: False, code=99991663, msg="denied", data=None)
    assert client.reply_text("om_1", "hi") is False


def test_text_message_is_handed_over_with_its_message_id():
    seen = []
    client = FeishuClient(app_id="cli", app_secret="s",
                          on_text=lambda *args: seen.append(args))
    data = types.SimpleNamespace(event=types.SimpleNamespace(
        message=types.SimpleNamespace(
            message_type="text", message_id="om_9", chat_id="oc_9",
            content='{"text":"开机"}'),
        sender=types.SimpleNamespace(
            sender_id=types.SimpleNamespace(open_id="ou_9")),
    ))
    client._on_message(data)
    assert seen == [("ou_9", "oc_9", "om_9", "开机")]


def test_non_text_and_malformed_messages_are_ignored_silently():
    # Review Focus 1 -- 这里抛异常会打断长连接
    seen = []
    client = FeishuClient(app_id="cli", app_secret="s",
                          on_text=lambda *args: seen.append(args))
    for message_type, content in (("image", '{"image_key":"k"}'), ("text", "not json")):
        data = types.SimpleNamespace(event=types.SimpleNamespace(
            message=types.SimpleNamespace(
                message_type=message_type, message_id="om_1", chat_id="oc_1",
                content=content),
            sender=types.SimpleNamespace(
                sender_id=types.SimpleNamespace(open_id="ou_1")),
        ))
        client._on_message(data)
    assert seen == [("ou_1", "oc_1", "om_1", "")]   # 畸形 JSON 退化成空串，交给 intent 判 unclear
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_feishu_client.py -q`
Expected: FAIL —— `TypeError`（handler 还是 3 个参数）/ 没有 `reply_text`

- [ ] **Step 3: 实现**

要点：`_on_message` 里取 `message.message_id` 一起传给 handler；
`reply_text` 失败时**只记日志、返回 `False`**，绝不抛；
`_extract_text` 保持现有的「畸形 JSON → 空串」行为不动。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_feishu_client.py -q`
Expected: PASS（4 项）

- [ ] **Step 5: Commit**

```bash
git add app/feishu_client.py tests/test_feishu_client.py
git commit -m "feat(feishu): pass message_id through and add reply_text"
```

---

### Task 7: 接线 —— `config.text_control_enabled` + `main._on_text_message`

**Files:**
- Modify: `app/config.py`
- Modify: `app/main.py`
- Create: `tests/test_text_control.py`

**Interfaces:**
- Consumes: `intent.parse`、`replies.render`、`store.mark_seen`、`feishu.reply_text`、既有的 `_send_or_refresh_card(chat_id)` / `_render()` / `_actions` 队列 / `_is_owner`
- Produces: `Settings.text_control_enabled: bool = True`

**分派规则（照抄，别自作主张）：**
1. `_is_owner` 不过 → 直接 return（不回任何东西）
2. `text_control_enabled` 为假 → **完全退回旧行为**：整串精确匹配 `CARD_COMMANDS` 就发卡，否则只记日志
3. `mark_seen(message_id)` 为假 → return（重投）
4. **先**整串精确匹配 `CARD_COMMANDS` → `_send_or_refresh_card(chat_id)`，**不发回执**（`/ac` 本身就是「把卡片给我」）
5. 否则 `intent.parse(text)`：
   - 若还没有卡片（`store.get_message_id(owner)` 为空）→ **补一张**（所有 `kind` 都补，只补一次），即调用既有的 `_send_or_refresh_card(chat_id)`
   - `intent.actions` 非空 → 乐观更新 `self.view`、`pending = True`、入队（**和 `_on_card_action` 走同一个队列**），然后 `self._render()` 刷新卡片
   - 最后**无论有没有动作**都回执：
     `reply_text(message_id, render(intent, self.view, self.presence, stale=is_stale(self.presence, time.time(), self.settings.stale_after_s), updated_at=self._updated_at()))`
     —— 状态用 **`self.view`**（已含本次乐观动作），不是 `self.state`（这是 spec §5.4「最后一个 ac/state + 本次乐观动作」的字面实现）。

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_text_control.py
"""Unit tests for the Service-level text dispatch -- no network, no broker."""

from app.config import Settings
from app.main import Service
from app.state import Presence


class FakeFeishu:
    def __init__(self):
        self.replies = []
        self.sent = []
        self.patched = []

    def reply_text(self, message_id, text):
        self.replies.append((message_id, text))
        return True

    def send_card(self, chat_id, card):
        self.sent.append(chat_id)
        return "om_card"

    def patch_card(self, message_id, card):
        self.patched.append(message_id)
        return True


def build_service(tmp_path, **overrides):
    values = {"feishu_app_id": "cli_test", "feishu_app_secret": "secret",
              "owner_open_id": "ou_test", "db_path": str(tmp_path / "t.sqlite3")}
    values.update(overrides)
    service = Service(Settings(_env_file=None, **values))
    service.feishu = FakeFeishu()
    # 这些测试考的是文本分派，不是在线判定 —— 先假装设备在线，免得多出「离线」后缀
    service.presence = Presence(online=True)
    return service


def test_text_command_goes_optimistic_and_queues_exactly_one_action(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "开机")
    assert service.view.power == 1
    assert service.pending is True
    assert service._actions.qsize() == 1
    assert service.feishu.replies == [("om_1", "✅ 已开机 · 制冷 · 24℃ · 自动风")]


def test_suggest_never_touches_the_aircon(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "太热了")
    assert service._actions.empty()
    assert service.view.power == 0
    assert service.pending is False
    assert service.feishu.replies == [
        ("om_1", "看着有点热？说「制冷 22 度」，或点卡片上的「制冷」。")]


def test_a_redelivered_message_is_ignored(tmp_path):
    # Review Focus 2
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "调低两度")
    service._on_text_message("ou_test", "oc_1", "om_1", "调低两度")
    assert service._actions.qsize() == 1
    assert len(service.feishu.replies) == 1


def test_three_different_messages_are_all_queued(tmp_path):
    # spec 5.3 / 验收 #11：去重只管重投，不管重复发送
    service = build_service(tmp_path)
    for i in range(3):
        service._on_text_message("ou_test", "oc_1", f"om_{i}", "调低两度")
    assert service._actions.qsize() == 3
    assert service.view.temp == 18


def test_non_owner_is_ignored_completely(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_other", "oc_1", "om_1", "开机")
    assert service.feishu.replies == []
    assert service._actions.empty()


def test_the_card_is_posted_once_and_patched_afterwards(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "开机")   # 没卡片 -> 补一张
    service._on_text_message("ou_test", "oc_1", "om_2", "关机")   # 有卡片 -> 只 patch
    assert service.feishu.sent == ["oc_1"]
    assert service.feishu.patched == ["om_card"]
    assert len(service.feishu.replies) == 2


def test_card_commands_still_post_the_card_without_a_receipt(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "/ac")
    assert service.feishu.sent == ["oc_1"]
    assert service.feishu.replies == []
    assert service._actions.empty()


def test_disabling_text_control_restores_the_old_behaviour(tmp_path):
    service = build_service(tmp_path, text_control_enabled=False)
    service._on_text_message("ou_test", "oc_1", "om_1", "开机")
    assert service.feishu.replies == []
    assert service._actions.empty()
    assert service.view.power == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_text_control.py -q`
Expected: FAIL —— `TypeError`（`_on_text_message` 还是 3 个参数）/ 没有 `text_control_enabled`

- [ ] **Step 3: 实现**

- `config.py`：在「Behaviour」段加 `text_control_enabled: bool = True`，附一行注释指向设计文档。
- `main.py`：`_on_text_message` 改成 4 参数并按上面的 5 条规则分派；
  `Service.start()` / `stop()` / `_publish_worker` / `_render` / `_on_card_action` **一行都不改**。
- `CARD_COMMANDS` 保持**整串精确匹配**语义（`text.lower() in CARD_COMMANDS`），否则「开空调」会被误吞。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_text_control.py -q`
Expected: PASS（8 项）

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: PASS（78 既有 + 8 + 8 + 8 + 9 + 4 + 4 + 8 = 127 项，0 failed）

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/main.py tests/test_text_control.py
git commit -m "feat(main): dispatch text messages through the shared action queue"
```

---

### Task 8: 文档同步

**Files:**
- Modify: `README.md`
- Modify: `docs/interface-spec.md`
- Modify: `.env.example`

**Interfaces:** 无代码接口

- [ ] **Step 1: README** —— 「当前状态」表加一行「对话控制（文本指令）」✅；
  在「MQTT 契约速览」之后加一节「对话控制」，列出 §4.2 动作表里的触发词示例、
  指向 `docs/superpowers/specs/2026-10-13-feishu-text-control-design.md`，
  并写明**「太热了」这类形容词句不会直接执行**（用户最容易误解的一点）。

- [ ] **Step 2: `docs/interface-spec.md`** —— 新增 §5.6「文本指令（对话控制）」，
  一句话说明「文本与按钮产出同一种动作，走同一条管道」，并指向设计文档；
  §12「决策记录」追加一条：形容词句不执行（D12）。

- [ ] **Step 3: `.env.example`** —— 追加

```bash
# 文本指令（对话控制）。设为 false 可退回「只有 /ac 出卡片」的旧行为。
TEXT_CONTROL_ENABLED=true
```

- [ ] **Step 4: 确认文档里没有过期的说法**

Run: `python -m pytest -q`（文档改动不该影响测试）
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md docs/interface-spec.md .env.example
git commit -m "docs: document text control and its one no-op case"
```

---

## 真机联调（代码全绿之后，需要飞书凭据与真机）

代码全部完成即可跑通这一节 —— 它**不在**上面的任务里，因为它依赖你已经建好的飞书应用和上电的 ESP32。

1. 部署到树莓派（`deploy/ac-bot.service`，注意 `User=dirj`），`TEXT_CONTROL_ENABLED=true`。
2. **先验权限**：发「开机」。若日志里 `reply_text` 报权限错误（`code=99991663` 之类），
   去开放平台**重新发一次版本**再试（设计文档 §9 的推断就靠这一步证实或推翻）。
3. 依次走设计文档 §8 的 13 条验收，**重点两条**：
   - #11 连发三次「调低两度」→ 日志里**只有一行** `ac/cmd {"temp":18}`；
   - #7 / #7b 「太热了」与「有点冷」→ 日志里**没有** `ac/cmd`，且两条回执方向不同。
4. 若 `TEXT_CONTROL_ENABLED=false` 后行为回到「只有 /ac 出卡片」→ 回退开关有效。

## 自查记录

- **spec 覆盖**：§1–§9 的每条要求都有归属 —— 动作表（T1/T2）、分派与 `Intent`（T1/T3）、
  去重（T5/T7）、补卡（T7）、回执文案（T4）、配置开关（T7）、部署说明（联调节）、
  §10 的 14 条决定（Global Constraints + 各任务要点）。
- **Review Focus 5 条**全部落到测试：1→T6、2→T5/T7、3→T3、4→T2、5→T2。
- **类型一致性**：`Intent` 的字段顺序在 T1 定义、T3 使用；`render` 的签名在 T4 定义、T7 使用；
  `mark_seen` 返回 `bool` 在 T5 定义、T7 使用；`reply_text(message_id, text) -> bool` 在 T6 定义、T7 使用。
- **比例**：代码块基本都是测试与断言，实现只给签名与要点，没有代写函数体。
