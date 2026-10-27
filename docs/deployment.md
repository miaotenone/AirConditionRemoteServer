# Linux 部署指南

适用于 systemd + 本机 Mosquitto 的 Linux 主机（例如树莓派）。以下命令是**供管理员
逐项审查的安装流程**，不是自动安装脚本；执行会改变目标主机，须由你选择维护窗口。
本次仓库规范化不连接、安装、重启当前生产实例。既有部署不要直接照抄覆盖。

## 1. 目录与配置约定

| 用途 | 本地开发 | 本指南的生产模板 |
| --- | --- | --- |
| 代码/虚拟环境 | 仓库根目录 / `.venv` | `/opt/ac-bot` / `.venv` |
| 运行账号 | 当前用户 | 无登录权限的 `ac-bot` |
| 配置 | 当前工作目录 `.env` | `/etc/ac-bot/ac-bot.env`，root:root，0600 |
| SQLite | `ac-bot.sqlite3`，相对当前目录 | `/var/lib/ac-bot/ac-bot.sqlite3` |
| broker | loopback 匿名，仅开发 | 私网认证 + 最小权限 ACL + 防火墙 |

`Settings` 默认读取工作目录的 `.env`，进程环境覆盖文件值。systemd 的
`EnvironmentFile=` 又会覆盖 unit 的 `Environment=`，所以**不能把本地 `.env.example`
原样当生产配置**：它的相对 DB_PATH 会覆盖 unit 中的绝对路径。使用
[`deploy/ac-bot.env.example`](../deploy/ac-bot.env.example)，保留生产绝对 DB_PATH。
不要在 `/opt/ac-bot` 留另一个 `.env`。systemd 管理员读取 root-only 的 EnvironmentFile，
不要求运行账号直接可读此文件。环境变量不是隔离于同 UID/root 的秘密存储。

## 2. 准备账号和依赖

按发行版安装 Python 3.10+、uv、Mosquitto 与客户端工具。以下为 Debian/Ubuntu 风格，
已存在的账号/目录先检查，不重复创建或覆盖；uv 按其官方文档安装并固定用于部署的版本。

```sh
sudo apt-get install mosquitto mosquitto-clients
sudo useradd --system --user-group --home-dir /var/lib/ac-bot --no-create-home --shell /usr/sbin/nologin ac-bot
sudo install -d -o root -g root -m 0755 /opt/ac-bot
sudo install -d -o root -g root -m 0700 /etc/ac-bot
```

在可写 staging checkout 审查代码及 `uv.lock`，运行开发检查；部署可以使用经过审查的
checkout，但**必须在最终 `/opt/ac-bot` 路径创建虚拟环境**，不能复制别处的 `.venv`
（脚本 shebang 可能含旧绝对路径）。让部署账号准备代码，运行账号只读代码：

```sh
# 将选定、审查后的源码放到 /opt/ac-bot；不要复制 .env、数据库或旧 .venv。
cd /opt/ac-bot
# 以有权写入 /opt/ac-bot 的部署身份运行，非 ac-bot 服务账号：
uv sync --frozen --no-dev
sudo chown -R root:root /opt/ac-bot
# 确认运行账号能遍历父目录、读取代码并执行解释器：
sudo -u ac-bot /opt/ac-bot/.venv/bin/python -m app.main --print-card
```

**uv 解释器路径注意**：uv 可能链接部署用户家目录缓存里的 Python。若该家目录不可遍历，
服务会报 permission denied。使用发行版系统 Python（例如
`uv sync --frozen --no-dev --python /usr/bin/python3`），或把 uv 管理的 Python
安装到运行账号可读的共享目录；不要通过开放整个私人家目录解决。
固定生产配对是 lark-oapi 1.7.4 / pycryptodome 3.24.1，不因下载失败升级。

## 3. 配置服务

在源码目录安装模板并编辑副本，模板中的占位符必须替换：

```sh
sudo install -o root -g root -m 0600 deploy/ac-bot.env.example /etc/ac-bot/ac-bot.env
sudoedit /etc/ac-bot/ac-bot.env
sudo install -o root -g root -m 0644 deploy/ac-bot.service /etc/systemd/system/ac-bot.service
sudo systemd-analyze verify /etc/systemd/system/ac-bot.service
```

核对必填飞书凭据、唯一 owner、MQTT 账号、prefix 和数据库路径。不要让安装命令覆盖
已存在的真实配置。需要自定义目录时，审查 unit 的 User/Group、WorkingDirectory、
EnvironmentFile、ExecStart 和 DB_PATH，不能只改一个路径。StateDirectory 保留为 `ac-bot`。

`StateDirectory=ac-bot` 在启动前创建 `/var/lib/ac-bot` 并赋予运行账号，权限为 0750。
SQLite 需能写**目录**，以创建 journal/WAL，不能只允许写数据库文件。

## 4. MQTT：开发与生产分开

[`deploy/mosquitto-ac.conf`](../deploy/mosquitto-ac.conf) 是 loopback 匿名开发配置，
仅用 `mosquitto -c deploy/mosquitto-ac.conf -v` 在隔离开发机前台运行。
不要直接复制它覆盖生产 LAN listener。旧全接口匿名配置保存在
[历史归档](history/README.md)，不推荐继续使用。

生产使用 [`mosquitto-ac.production.conf.example`](../deploy/mosquitto-ac.production.conf.example)
与 [`mosquitto-ac.acl.example`](../deploy/mosquitto-ac.acl.example)，面向 Mosquitto 2.x
的静态账号/ACL 配置。新版本迁移请按安装版本的 man page 单独审查。
在隔离测试主机验证后，由管理员在维护窗口安装：

1. 把示例 `192.0.2.10` 换成主机真实私网 IP。该示例地址不可直接用于部署。
   只绑定 LAN IP 时，本地 bot 的 `MQTT_HOST` **也要使用该 LAN IP**，不是 127.0.0.1。
2. 创建两个不同账户：服务 `ac-bot`、设备 `ac-device`；在设备配置页同步凭据。
   这只配置已有认证能力，不改变固件协议。先核对设备确实保存了正确凭据。
3. 审查所有 broker listener 与 drop-in，避免旧匿名 listener 继续开放、1883 端口冲突
   或相互覆盖认证配置。不要重复发行版主配置的 persistence / persistence_location。
4. ACL 只授予服务写 cmd/hb、读 state/status/env/event；设备权限相反。
   自定义 topic prefix 时双方和 ACL 必须一起修改。不要授予通配 `write ac/#`。

```sh
# 仅首次创建新密码文件才使用 -c；已有文件不得用 -c，避免抹掉其他账号。
sudo mosquitto_passwd -c /etc/mosquitto/ac.passwd ac-bot
sudo mosquitto_passwd /etc/mosquitto/ac.passwd ac-device
sudo chown root:mosquitto /etc/mosquitto/ac.passwd
sudo chmod 0640 /etc/mosquitto/ac.passwd
sudo install -o root -g mosquitto -m 0640 deploy/mosquitto-ac.acl.example /etc/mosquitto/ac.acl
# 先复制到待审查副本，替换 listener 地址，再安装为 /etc/mosquitto/conf.d/ac.conf。
```

broker 进程必须能读取密码和 ACL 文件；防火墙只允许受信设备/服务网络访问 1883，
不做路由器公网转发。TCP 1883 的密码和 payload 均未加密；跨不可信网络用 VPN，
或另行设计并验证双端 TLS，不能只给 broker 开 TLS 后假定当前客户端已支持。
密码输入使用交互方式，不要把真实密码写进命令、issue 或 shell 历史。

## 5. 启动与恢复依赖

首次部署且 broker 配置验证通过后，管理员才执行：

```sh
sudo systemctl daemon-reload
sudo systemctl enable ac-bot.service
sudo systemctl start ac-bot.service
systemctl status ac-bot.service mosquitto.service
journalctl -u ac-bot.service -n 100 --no-pager
```

unit 保留四项联动，**不要简化成 Wants=mosquitto.service**：

- `After=mosquitto.service`：启动顺序；不证明 broker 已能正常收发。
- `Requires=mosquitto.service`：本机 broker 必要依赖，停止 broker 会停止 bot。
- `PartOf=mosquitto.service`：显式 restart broker 时，bot 跟随重启。
- `[Install] WantedBy=mosquitto.service`：enable 后，单独 stop 再 start broker 也拉起 bot。

维护窗口验证 stop/start/restart 三种路径并确认心跳恢复。**这会影响真实空调，
不属于离线 CI**；先使用测试设备/隔离网络，不要在未知状态下故意断心跳。
旧已 enable 的 unit 变更 Install 关系后，需审查并重新 `systemctl reenable ac-bot`，
仅 daemon-reload 不会补新依赖链接。新模板不声称已安装到任何主机。

服务自动重连、保护与恢复的现有边界见 [接口规格 §4.4](interface-spec.md#44-时间与恢复边界)。
systemd active 不是端到端健康保证，要同时核对 broker、服务日志和设备反馈。

## 6. 从旧个人目录部署迁移

不要直接启动新 unit：它可能创建空库，看起来像“预设丢失”。先记录旧 unit、drop-in、
工作目录、运行账号与**实际生效的 DB_PATH**；真实配置由管理员本机查看，不公开输出。
历史上可能同时存在 checkout 内的旧库与 `/var/lib/ac-bot` 的库，不能根据文件名猜。

1. 维护窗口停止旧 bot，确认没有第二个进程仍写库；broker 保持运行，了解设备失联保护
   会发生什么。不要同时运行两套服务（client_id 与定时任务可能冲突）。
2. 备份旧 unit/配置，使用 SQLite backup API 或 `sqlite3` 的 `.backup` 生成一致快照。
   不要在 SQLite 活跃时只复制主文件；WAL/shm 可能含尚未合并的数据。备份限制权限。
3. 准备新代码/虚拟环境，按已确认的源库迁移到 `/var/lib/ac-bot/ac-bot.sqlite3`。
   如果原库已在这个路径，**不要覆盖或创建新空库**，只备份并调整归属。
4. 确认服务停止后，设置目标目录 `ac-bot:ac-bot` / 0750，数据库及存在的 WAL/shm
   为同一账号可写；禁止用宽泛 chmod 777。用 `PRAGMA quick_check` 验证备份和目标库。
5. 安装新 unit/配置、重新 enable 依赖链接，然后启动。核对预设、设置、卡片映射与
   历史记录仍在，收发恢复正常。此次规范化**没有数据库 schema 迁移**。
6. 失败则停止新服务，保留日志和一致备份，恢复旧 unit/配置/所有权，再启动旧服务。
   不要把旧库覆盖正在写的新库；也不要删除唯一备份。

迁移完整记录可放在本地运维文档，但提交前脱敏。旧部署原文仅在 history 作为证据保留。

## 参考

- [systemd.exec：EnvironmentFile 与 StateDirectory](https://www.freedesktop.org/software/systemd/man/256/systemd.exec.html)
- [systemd.unit：依赖与 Install](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html)
- [Mosquitto 配置手册](https://mosquitto.org/man/mosquitto-conf-5.html)
