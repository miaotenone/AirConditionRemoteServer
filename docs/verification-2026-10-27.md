# 2026-10-27 规范化本地验证记录

本记录描述本次工作区的**实际命令结果**，不声称 CI 已远端运行或生产已部署。
未读取真实 `.env`、`ac-bot.sqlite3`，未连接生产、未修改固件或控制策略，未提交。

## 运行环境与结果

Windows，系统 CPython 3.14.3；uv 0.12.12；隔离安装的固定 Ruff 0.14.2。
系统 Python 预装依赖与生产 lock 不同：lark-oapi 1.7.2、pycryptodome 3.23.0、
pydantic-settings 2.13.1、paho-mqtt 2.1.0、pytest 9.0.2。
以下测试结果**不能替代 frozen 干净安装验证**。

| 验证 | 实际结果 |
| --- | --- |
| 修改前 `python -m pytest -q` | 424 passed，1 个上游 warning |
| 新纯逻辑测试（实现前） | 12 failed，原因是新适配入口尚不存在；随后实现 |
| 修改后 `python -m pytest -q` | 436 passed，1 个相同上游 warning |
| 固定 Ruff 0.14.2 `check .` | All checks passed |
| 固定 Ruff 0.14.2 `format --check .` | 37 files already formatted |
| `uv lock --check --offline` | 32 packages，成功 |
| 原 HEAD 与新 lock 逐 package 比较 | 原全部非项目生产记录不变，仅增加 Ruff 及 dev 元数据 |
| `uv build --offline` | wheel + sdist 成功 |
| 隔离 Python 3.14 venv，`uv pip install --offline --no-deps <wheel>` | 成功，仓库外 `-I` 导入新 forms/weather 纯 API 成功 |
| 系统 Python 实际 `python -m app.main --print-card` | JSON 可解析，schema 2.0，不实例化配置/数据库 |
| sdist 资源与敏感文件排除检查 | lock、示例配置、部署文件与文档齐全；无 `.env` 或 sqlite3 文件 |
| 本地 Markdown 相对路径检查 | 无断链（未验证远程 URL 可达性） |
| CI YAML 解析 | jobs / matrix / steps 结构可解析 |
| 5 份历史原文与 HEAD 来源比较 | 内容一致，比较允许 CRLF/LF 差异 |
| `git diff --check` | 成功 |

上游 warning：lark-oapi 内嵌 protobuf 的 `datetime.datetime.utcfromtimestamp()`
在 Python 3.14 发出 DeprecationWarning；未通过过滤 warning 或升级生产依赖掩盖。

## 发现并修复

初始构建成功，但检查归档发现 setuptools 默认 sdist 未收录 uv.lock、示例配置与部署文档。
先运行归档内容断言观察失败，再增加 `MANIFEST.in` 显式允许清单；重建后断言通过。
CI 增加相同的归档资源/敏感文件排除检查。

## 阻塞与未执行

以下是实现代理初次交付时的限制；主会话后续证据见文末补充，不以
系统依赖测试替代锁定运行环境验证。

- 干净 `uv sync --frozen --extra dev` 多次在锁定依赖下载阶段超时；未为绕过它升级
  lark-oapi / pycryptodome。主会话另行跟进下载，本记录不宣称该路径已通过。
- no-deps 隔离环境仅验证安装、包内容和纯 API，不具备完整运行依赖。一次 `-I`
  全入口导入尝试因缺少 pydantic 失败；完整入口用已有系统依赖验证通过，不能
  把两者合并声称“干净 wheel 全运行通过”。CI 另有先安装 frozen 依赖的完整 smoke。
- 当前 Windows 没有可直接执行的 systemd-analyze / Mosquitto，未运行 Linux unit
  或 broker 配置验收；这些模板须在隔离 Linux 环境/维护窗口审查。
- GitHub Actions 未远端触发；Python 3.10 / 3.12 / 3.14 CI matrix 是待远端执行的保障。
- 未进行真实飞书、MQTT 设备、broker 联动或失联关机测试，以免影响生产。

## 主会话独立集成复核

- 只读独立审查未发现本次新增的业务回归；补齐发行包依赖约束：
  `lark-oapi==1.7.4`、`pycryptodome==3.24.1` 同时进入项目依赖元数据，
  不再只依赖 uv 专用约束。`uv lock` 更新后逐条比较，原全部非项目
  package 记录保持不变，没有升级生产包。
- CI 新增 wheel METADATA 精确版本断言，并补全三个生产配置模板的 sdist
  必需资源检查；断言先在旧元数据上失败，新归档通过。
- README 修正为 MQTT 异步连接：进程可启动并后台重连，不意味着设备可控。
- 最终无凭据源码快照：系统 Python 3.14 下 436 passed / 1 个上游 warning；
  已冻结安装的 Python 3.11 生产依赖环境下运行同一最终源码：
  **436 passed，无 warning**，已显式核对两个固定版本与源码路径。
- 最终 Ruff **0.14.2**：lint 与 format 均通过；`uv lock --check --offline`
  通过。原 424 个具名测试全部保留；3,238 组纯函数前后行为对比一致。
- 新建独立 Python 3.11 venv，使用 frozen 导出的生产 requirements 离线
  安装 21 个依赖，再安装最终 wheel；在仓库外使用 `-I` 验证 forms API、
  固定运行依赖和无配置卡片预览，全部通过。wheel/sdist 构建和增强的
  归档检查通过。这是完整生产依赖的安装 smoke，不是 no-deps 验证。
- 最终源码快照的 `uv sync --frozen --extra dev` 初次在 Ruff 下载阶段
  超时；后台重试随后成功，安装全部 29 个项目及开发依赖，下载阻塞已解除。
- 在该完整 frozen Python 3.11 环境重新执行 `uv sync --frozen --extra dev
  --offline`、Ruff lint / format、完整 pytest 和无配置卡片预览：全部通过，
  **436 passed in 13.03s，无 warning**；37 个 Python 文件格式检查通过。
  显式核对 Ruff 0.14.2、lark-oapi 1.7.4、pycryptodome 3.24.1，预览 JSON
  可解析。此前下载超时不再是交付阻塞，但不代表远端 CI 或部署已验证。
- 本轮未提交或推送；真实 `.env` 和数据库仅核对长度/修改时间，未读取内容。
