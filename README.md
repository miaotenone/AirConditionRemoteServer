# AirConditionRemoteServer

通过飞书私聊与交互卡片控制 ESP32 空调网关的 Python 服务。飞书使用长连接，
无需公开回调端口；本服务通过 Mosquitto 与设备通信。固件是独立项目
`AirConditionRemoteCTL`，两端以 [MQTT 契约](docs/interface-spec.md#4-mqtt-契约与固件仓库的唯一接口) 为边界。

支持中文短句控制、常用预设及单次预约、天气建议、DHT11 历史图表与夜间闭环。
仅供单个 owner 私聊使用，不是多人权限系统。天气建议需要用户确认才执行。

## 快速部署：WalnutPi / 树莓派（Debian 12）

按 **安装依赖 → 设置飞书 → 连接设备 → 填配置 → 启动验收** 的顺序操作。
需要一台能联网、有 sudo 权限的 Linux 主机，以及已刷好 `AirConditionRemoteCTL`
固件的 ESP32；两者接入同一可信局域网，ESP32 使用 2.4GHz WiFi。

> 以下是**全新安装**流程，以普通用户（如 `pi`）执行，只在标有 `sudo` 的地方提权。
> 已有 `.env`、数据库、Mosquitto 配置或 `ac-bot` 服务时，不要覆盖或再启动第二个实例；
> 请使用现有配置，迁移见 [部署指南](docs/deployment.md#6-从旧个人目录部署迁移)。

### 1. 安装依赖，获取项目

```sh
sudo apt-get update
sudo apt-get install -y git curl ca-certificates python3 python3-venv mosquitto mosquitto-clients nano

# 已有 uv 可跳过安装；这里固定一个已验证的版本
curl -LsSf https://astral.sh/uv/0.12.12/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

git clone https://github.com/miaotenone/AirConditionRemoteServer.git
cd AirConditionRemoteServer
uv sync --frozen --no-dev --python /usr/bin/python3
```

需要 Python 3.10+；Debian 12 自带 Python 3.11。**不要从 Windows 或其他机器复制 `.venv`**，
须在目标机器、最终目录执行 `uv sync`。已有源码就进入原目录，不重复 clone。
后续命令均在仓库根目录执行；`nano` 编辑后按 `Ctrl+O`、回车保存，`Ctrl+X` 退出。

### 2. 准备飞书机器人

在 [飞书开放平台](https://open.feishu.cn/app) 创建企业自建应用，按以下清单配置：

- 启用**机器人**能力，记录 **App ID / App Secret**。
- 添加权限：`im:message`、`im:message:send_as_bot`、`im:message.p2p_msg:readonly`。
- 在「事件与回调」选择**长连接**，添加消息事件 `im.message.receive_v1` 和卡片回调 `card.action.trigger`。
- 创建并发布版本，可用范围包含自己；权限改动后也要重新发布。

不需要公网 IP、回调 URL 或内网穿透。控制台设置细节见 [飞书设置](docs/feishu-setup.md)。
暂时不知道自己的 `open_id` 没关系，第 5 步可以从日志获取。
若控制台提示「应用未建立长连接」，先完成第 3、4 步并在第 5 步前台启动服务，
再返回控制台保存订阅并发布，然后继续获取 owner 和验收。

### 3. 配置 MQTT，让 ESP32 能连接

**已有可用 broker**：跳过下面的安装配置，记下它的地址、端口、服务端与设备各自的账号，
并确认权限符合 [MQTT 契约](docs/interface-spec.md#4-mqtt-契约与固件仓库的唯一接口)。

**全新本机 broker**：先运行 `hostname -I` 查看地址，将下面的 `LAN_IP` 改成主机的真实
**局域网 IPv4**；不要使用 `127.0.0.1` 或 Tailscale 的 `100.x` 地址给普通 ESP32 连接。
建议在路由器上为主机固定 DHCP 地址，避免重启后地址变化。

```sh
hostname -I
LAN_IP=192.168.1.11  # 示例！替换成你自己的局域网地址

# 交互设置两个不同密码，分别记下。-c 只可用于首次创建文件！
sudo mosquitto_passwd -c /etc/mosquitto/ac.passwd ac-bot
sudo mosquitto_passwd /etc/mosquitto/ac.passwd ac-device
sudo chown root:mosquitto /etc/mosquitto/ac.passwd
sudo chmod 0640 /etc/mosquitto/ac.passwd
sudo install -o root -g mosquitto -m 0640 deploy/mosquitto-ac.acl.example /etc/mosquitto/ac.acl
sed "s/192\.0\.2\.10/$LAN_IP/" deploy/mosquitto-ac.production.conf.example \
  | sudo tee /etc/mosquitto/conf.d/ac.conf > /dev/null
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto
systemctl status mosquitto --no-pager
```

确认显示 `active (running)`；如启用主机防火墙，只放行受信设备网络到 TCP 1883。
**不做公网端口转发**：1883 的账号与数据仍是明文，只在可信隔离网络使用，跨不可信网络用 VPN。
已有 broker 的 listener、账号和 ACL 调整见 [部署指南](docs/deployment.md#4-mqtt开发与生产分开)，不要直接覆盖。

在 ESP32 配置页填写：**MQTT 地址 = 上面的 LAN_IP，端口 = 1883，账号 = `ac-device`，
密码 = 对应的设备密码，topic 前缀 = `ac`**。主机 `.env` 使用另一个账号 `ac-bot`。
首次配网可连接设备热点 `AC-Gateway-Setup`（默认密码 `acgateway`），打开
`http://192.168.4.1`，填写 WiFi 与 MQTT 后保存；已配置的设备只需核对参数，不必恢复出厂。
接线、刷机和其他配网方式见固件仓库 `AirConditionRemoteCTL` 的 README。

### 4. 填写服务配置

```sh
# 仅首次复制；已有 .env 则直接编辑，保留原值
test -f .env || cp .env.example .env
chmod 600 .env
nano .env
```

通常只需要修改这些项，其余保留默认值：

| 配置项 | 填什么 |
| --- | --- |
| `FEISHU_APP_ID` | 飞书应用的 `cli_...` |
| `FEISHU_APP_SECRET` | 同一应用的 App Secret |
| `OWNER_OPEN_ID` | 自己在这个应用下的 `ou_...`；首次可保留 `OWNER_OPEN_ID=` 空值，**不要删掉这一行** |
| `MQTT_HOST` | 第 3 步主机的局域网 IP；使用外部 broker 时填其地址 |
| `MQTT_PORT` | `1883`，或已有 broker 的实际端口 |
| `MQTT_USER` / `MQTT_PASS` | `ac-bot` / 为它设置的密码；已有 broker 填实际服务账号，**不是设备账号** |
| `MQTT_PREFIX` | `ac`；与设备、broker ACL 保持一致 |
| `DB_PATH` | 保留 `ac-bot.sqlite3`，保存预设、设置及历史，不要随意删除 |

`.env` 使用 UTF-8 无 BOM，不要提交或分享。配置从**工作目录**读取，进程环境变量优先。
定时、夜间功能使用主机本地时间；用 `date` 核对时区是否符合自己的使用地点。

### 5. 启动，并在飞书里验收

```sh
# 离线检查：应输出卡片 JSON，不连接飞书或 MQTT
.venv/bin/python -m app.main --print-card

# 前台启动，保持这个终端打开
.venv/bin/python -m app.main
```

1. 日志应出现 **`mqtt connected`** 和飞书 **`connected to wss://...`**。
   只有 `service up` / `feishu long connection started` 不代表连接成功。
2. 若 owner 留空，给机器人私聊发一句话，从 `ignoring event from non-owner 'ou_...'`
   读取自己的 ID；此时**不会回复或开放控制**。按 `Ctrl+C` 停止，填回 `.env` 的
   `OWNER_OPEN_ID`，再运行 `.venv/bin/python -m app.main`。
3. 私聊发 **`设置卡片`**，应收到遥控卡片；发 **`状态`** 查询，核对设备在线及新鲜上报，
   ESP32 的 `LINK` 页应显示 MQTT 和服务心跳正常。
4. 确认现场安全后，手动发送 `开机制冷26度`，观察设备反馈与实机动作；卡片状态不是物理执行证明。

**MQTT 断开时进程仍能运行并重连，但不能控制设备。** 不要只看进程或 systemd 的 active 状态。
跑通后按下面的可选步骤常驻运行；不要让前台和后台同时运行。

### 6. 可选：开机自启、关闭 SSH 后继续运行

<details>
<summary>展开：以当前普通用户运行 systemd 服务（个人设备简易部署）</summary>

先在前台按 `Ctrl+C` 停止，回到**仓库根目录**。以下只用于尚无 `ac-bot.service` 的新安装，
自动填写当前账号和项目绝对路径，继续读取同一份 `.env` 与数据库；**安装后不要移动项目目录**。
专用账号部署和既有服务迁移请使用 [部署指南](docs/deployment.md)，不要直接安装指向 `/opt/ac-bot` 的模板。

```sh
sudo tee /etc/systemd/system/ac-bot.service > /dev/null <<EOF
[Unit]
Description=Feishu AC remote bridge
After=network-online.target mosquitto.service
Wants=network-online.target
Requires=mosquitto.service
PartOf=mosquitto.service

[Service]
User=$(id -un)
Group=$(id -gn)
WorkingDirectory="$PWD"
ExecStart="$PWD/.venv/bin/python" -m app.main
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=multi-user.target mosquitto.service
EOF
sudo systemd-analyze verify /etc/systemd/system/ac-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now ac-bot
systemctl status ac-bot --no-pager
sudo journalctl -u ac-bot -n 30 --no-pager
```

修改 `.env` 后执行 `sudo systemctl restart ac-bot`；实时看日志用
`sudo journalctl -u ac-bot -f`，停止用 `sudo systemctl stop ac-bot`。
采用外部 broker 时，创建 unit 前将 `After=` 改为 `After=network-online.target`，
删除 `Requires=` / `PartOf=` 两行，并将 `WantedBy=` 改为 `WantedBy=multi-user.target`。

</details>

### 常见问题

| 现象 | 先检查 |
| --- | --- |
| `uv: command not found` / Python 无法执行 | 重新设置第 1 步 PATH；不要使用从其他系统复制的 `.venv` |
| MQTT 连接失败 | broker 是否运行；LAN IP/端口、账号密码是否正确；防火墙是否允许；查 `sudo journalctl -u mosquitto -n 30 --no-pager` |
| 机器人不回复 | owner 是否为空或填错应用的 ID；飞书权限、事件、可用范围及发布是否完成 |
| 有卡片但按钮无响应 | 是否订阅 `card.action.trigger`，修改后是否重新发布 |
| 飞书正常、ESP32 离线 | 设备地址不能填 `127.0.0.1`；核对 `ac-device` 密码、前缀、2.4GHz WiFi 与 LINK 页 |
| `Address already in use` / 多个实例互相干扰 | 不要另开 Mosquitto 或第二个 bot；先查已有 systemd 服务 |

排障日志可能包含个人 ID、连接 URL 和密钥，分享前必须脱敏。

### 仅本机预览 / Windows 开发

安装 Python 3.10+ 和 [uv](https://docs.astral.sh/uv/)，在项目目录执行
`uv sync --frozen`、`uv run --frozen python -m app.main --print-card` 即可离线预览。
Windows PowerShell 用 `Copy-Item .env.example .env` 创建首次配置，真实启动用
`uv run --frozen python -m app.main`，仍需配置飞书及可达的 MQTT broker。
`deploy/mosquitto-ac.conf` **只监听本机、允许匿名，仅供隔离开发，不可用于 ESP32 局域网接入**。

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
