# AirConditionRemoteServer

通过飞书私聊与交互卡片控制 ESP32 空调网关的 Python 服务。飞书使用长连接，
无需公开回调端口；本服务通过 Mosquitto 与设备通信。固件是独立项目
`AirConditionRemoteCTL`，两端以 [MQTT 契约](docs/interface-spec.md#4-mqtt-契约与固件仓库的唯一接口) 为边界。

支持中文短句控制、常用预设及单次预约、天气建议、DHT11 历史图表与夜间闭环。
仅供单个 owner 私聊使用，不是多人权限系统。天气建议需要用户确认才执行。

## 快速开始

需要 Python 3.10+、[uv](https://docs.astral.sh/uv/)、飞书自建应用，以及可连接的 MQTT broker。
先完成 [飞书设置](docs/feishu-setup.md)。Windows 可使用 PowerShell，Linux 使用终端：

```sh
git clone <你的仓库地址> AirConditionRemoteServer
cd AirConditionRemoteServer
uv sync --frozen
```

不需要凭据、数据库、网络连接的卡片预览（依赖需已安装）：

```sh
uv run --frozen python -m app.main --print-card
```

启动真实服务前，复制配置并填写凭据：

```sh
# Linux / macOS；PowerShell 使用 Copy-Item .env.example .env
cp .env.example .env
# 编辑 .env，设置飞书 ID/Secret、OWNER_OPEN_ID 和 MQTT 连接参数
uv run --frozen python -m app.main
```

`.env.example` 的 `DB_PATH=ac-bot.sqlite3` 在当前目录生成本地数据库，无需 root。
`Settings` 从**工作目录**加载 `.env`；请在仓库根目录启动。进程环境变量优先于 `.env`。
若首次不知道 `open_id`，可临时留空，查看本人私聊事件的日志后填写并重启；
空 owner 只记录来访 ID，不开放控制权限。不要分享日志中的个人标识或密钥。

MQTT 不可达时，进程仍可启动并在后台重连，但无法控制设备；
请核对连接日志与设备反馈，不要把进程启动视为连接成功。纯本机调试可用：

```sh
mosquitto -c deploy/mosquitto-ac.conf -v
```

该配置**仅监听 loopback、允许匿名，仅供开发**，设备无法从局域网连接。
接入真实设备或部署 Linux 服务请使用 [部署指南](docs/deployment.md) 的认证、ACL 和防火墙方案。
本项目不会自动安装或重启任何正在运行的服务。

## 使用方式

向机器人发 `设置卡片` 打开遥控卡片；发 `开机`、`制冷26度`、`调低两度` 控制；
发 `状态`、`帮助` 查询。`预设`、`天气`、`夜间`、`历史` 打开对应入口。
`当前 / 今夜 / 一天 / 一周 / 全部` 查询环境历史；“一天”从本地当天 00:00 起算，
不是滚动 24 小时；少于两个样点不画曲线。历史窗口与保护策略保持既有行为。

**安全边界**：状态是网关影子状态，不是空调物理执行证明。设备有失联关机保护，
自动恢复为尽力而为，不能保证成功或最新用户操作优先；详见
[协议与时间边界](docs/interface-spec.md#44-时间与恢复边界)。不要用于人身或设备安全关键控制。

## 开发与文档

```sh
uv sync --frozen --extra dev
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check .
uv run --frozen --extra dev python -m pytest
```

- [开发指南](docs/development.md)：目录职责、离线测试、锁文件和构建检查
- [部署指南](docs/deployment.md)：systemd、配置路径、MQTT 安全与旧数据迁移
- [贡献指南](CONTRIBUTING.md) · [安全说明](SECURITY.md) · [变更日志](CHANGELOG.md)
- [接口规格](docs/interface-spec.md) · [飞书设置](docs/feishu-setup.md)
- [MQTT 联调](docs/mqtt-bringup.md) · [飞书联调](docs/feishu-bringup.md)
- [历史验收与复盘](docs/history/README.md)：保留原文，不作为当前安装命令

生产依赖以 `uv.lock` 为准，固定 `lark-oapi==1.7.4`、`pycryptodome==3.24.1`；
请勿为解决下载问题直接升级。使用 `uv sync --frozen`，不要把裸 `uv sync` 当部署步骤。
项目版本 `0.1.0`，许可证见 [LICENSE](LICENSE)。
