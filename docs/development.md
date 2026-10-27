# 开发指南

## 安装与检查

支持 Python 3.10+，CI 覆盖 3.10、3.12、3.14。建议使用 uv 创建隔离环境，
不要依赖系统 Python 已安装的包。仓库根目录运行：

```sh
uv sync --frozen --extra dev
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check .
uv run --frozen --extra dev python -m pytest -q
uv run --frozen python -m app.main --print-card
uv build
```

格式修复使用 `uv run --frozen --extra dev ruff format .`；
静态检查修复使用 `ruff check . --fix`，审查 diff 后再运行全部检查。
Ruff 只检查基础错误、未使用符号及 import 排序，不引入全量类型检查门槛。
`.editorconfig` 和 `.gitattributes` 统一 UTF-8 / LF。不要提交 `.env` 或数据库/WAL 文件。

## 代码地图

| 模块 | 职责 |
| --- | --- |
| `main.py` | Service 编排、回调派发、动作队列与后台工作线程；保留原运行入口 |
| `forms.py` | 无 I/O 的预设/设置表单适配；保留历史默认值与宽容解析 |
| `intent.py` / `actions.py` | 中文文本与飞书组件载荷归一化 |
| `state.py` / `preset.py` / `control.py` / `schedule.py` | 状态、场景、夜间决策与时间窗口纯逻辑 |
| `env.py` | 采样筛选、聚合与下采样；文本和图表共用窗口 |
| `weather.py` | 建议与命令构造纯逻辑；外部天气查询集中在独立函数 |
| `cards.py` / `replies.py` | 卡片 schema 与中文回执 |
| `mqtt_client.py` / `feishu_client.py` | 传输适配与 SDK 边界 |
| `store.py` / `config.py` | SQLite 持久化与 pydantic-settings 配置 |

不要为缩短行数机械拆分 `app/`。优先抽取可独立测试的纯逻辑，保留 Service
旧调用入口、字段顺序、回调线程隔离、心跳/失联保护和历史窗口语义。
飞书慢调用不能阻塞 MQTT 接收线程或负责心跳的 tick。

## 测试约定

测试不需要 broker、飞书凭据、真实 `.env` 或历史数据库。用 `Settings(_env_file=None, ...)`
注入参数，SQLite 使用 pytest 的 `tmp_path`。网络边界用明确的 fake 替换，
**不要运行 `Service.start()`、菜单写入脚本或连接真实设备来验证重构**。
新行为/bugfix：先写失败测试并确认失败原因，再实现，最后跑全套回归。
纯逻辑抽取也应先固定边界案例，包括无效字段、无动作分支和动作字段顺序。

`scripts/feishu_menu.py` 即使不加 `--apply` 也会读取配置并访问飞书；不是离线 smoke test。

## 锁文件与构建

`uv.lock` 是部署真源。Ruff 是 dev extra；`uv sync --frozen` 不装开发工具，
`uv sync --frozen --extra dev` 才安装。`--frozen` 不替你检查 pyproject 是否匹配，
因此 CI 还执行 `uv lock --check`。下载失败应检查网络/镜像/证书/缓存，不能偷偷升级。
生产验证的 SDK/加密库固定配对不得无故更新；依赖变更必须说明目的、审查完整 lock diff。

构建产物在 `dist/`（不提交）。CI 导出 frozen 依赖、在新的虚拟环境安装这些依赖，
再用 `--no-deps` 安装 wheel，在仓库外运行 `--print-card`，避免 editable 安装掩盖打包缺漏。
本地复现（Linux shell；Windows 将 `bin/python` 换成 `Scripts/python.exe`）：

```sh
uv build
uv export --frozen --no-dev --no-emit-project -o dist/requirements.txt
uv venv .package-check
uv pip sync --python .package-check/bin/python dist/requirements.txt
uv pip install --python .package-check/bin/python --no-deps dist/*.whl
# 在仓库外，以绝对路径调用上面的解释器：
# /absolute/path/.package-check/bin/python -m app.main --print-card
```

不要把仅系统 Python 测试通过描述成 frozen 干净安装验证通过；记录解释器、
实际依赖版本、第三方 warning 和网络/平台阻塞。
本次实际结果与未执行项见 [2026-10-27 验证记录](verification-2026-10-27.md)。
