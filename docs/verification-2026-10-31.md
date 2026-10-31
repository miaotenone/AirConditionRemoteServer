# 2026-10-31 主分支整合验证

本轮在 Windows 开发机规范提交历史并整合最新服务端代码。未启动实际服务、访问飞书菜单或触发 MQTT 控制；真实 `.env` 和 SQLite 数据库仅记录文件大小及修改时间，用于确认本轮工作未改动运行数据。

## 历史与文件核对

- 迁移前全部引用已保存为 Git bundle，并通过完整性验证。
- 59 次历史提交逐一比较，新旧文件树完全一致；作者、提交者和时间信息保留，原初始提交失效签名移除并保存在备份。
- 整理后应用、测试、脚本、部署模板、锁文件与此前最新功能分支一致；新增变更为贡献与迁移文档。

## 质量门禁

运行环境为 Windows、CPython 3.12.14、uv 0.12.12。已安装的 frozen 依赖版本：Ruff 0.14.2、pytest 9.1.1、lark-oapi 1.7.4、pycryptodome 3.24.1、paho-mqtt 2.1.0、pydantic-settings 2.16.0。没有为整理历史升级锁文件。

| 验证 | 实际结果 |
| --- | --- |
| `uv lock --check --offline` | 32 packages，成功 |
| `uv sync --frozen --extra dev --offline` | 成功，锁文件未改动 |
| `uv run --frozen --extra dev ruff check .` | All checks passed |
| `uv run --frozen --extra dev ruff format --check .` | 37 files already formatted |
| `uv run --frozen --extra dev python -m pytest -q --basetemp <本轮独立临时目录>` | 436 passed，2 warnings，20.15 秒 |
| `uv run --frozen python -m app.main --print-card` | 成功，JSON 可解析，schema 2.0 |
| `uv build --offline --out-dir <本轮临时发行目录>` | sdist 与 wheel 均成功 |
| sdist 资源、运行数据排除及 wheel 依赖元数据检查 | 锁文件、示例配置、部署文件及文档齐全；无真实 `.env` 或 SQLite；生产依赖约束正确 |
| `uv export --frozen --no-dev --no-emit-project` 与独立 venv 离线安装 | 成功安装 21 个锁定生产依赖及发行 wheel |
| 仓库外独立 venv，`python -I` 检查 | 项目及固定依赖版本正确，纯表单 API 可导入并通过默认值检查 |
| 仓库外独立 venv，`python -I -m app.main --print-card` | 成功，JSON 可解析，schema 2.0 |
| 运行文件元数据对比 | `.env` 和 `ac-bot.sqlite3` 大小及修改时间均未变化 |

两条 warning 来自 lark-oapi 的内置 protobuf `utcfromtimestamp()` 和 WebSocket 模块 `asyncio.get_event_loop()` 弃用提示，没有测试失败。完整门禁脚本最终退出码为 0。

首次 pytest 运行进度到达 100%，但会话退出时清理共享临时目录 `pytest-current` 遇到 Windows `PermissionError`（WinError 5），该次命令退出码为 1，未记为通过。重跑通过 `--basetemp` 指定本轮独立临时目录，避开共享目录清理；未修改应用、测试或仓库 pytest 配置。

本轮完整日志为本机临时目录 `C:\Users\DiRJ\AppData\Local\Temp\opencode\server-normalization-check-20261031-retry.log`，发行包及独立环境保存在同目录下的 `ac-server-normalize-rny1tvla` 子目录；这些本地验证产物不随仓库发布。

## 验证边界

本轮离线结果不替代远端 CI 多版本矩阵、Linux systemd / Mosquitto 模板验收或真实飞书、MQTT、设备与失联恢复验证。历史上已完成的验证仍以相应日期的记录为准，不因整合主分支重新标注通过。
