# 变更日志

## Unreleased

### 主分支与提交历史（2026-10-31）

- 按维护者授权整合最新服务端功能与工程规范化到 `main`，清理阶段性功能分支。
- 主线 59 次历史提交统一为英文 Conventional Commits，逐项保留文件树、作者及时间；新增贡献规范、新旧哈希与恢复说明，见 [迁移记录](docs/git-history-normalization.md)。
- 离线检查与打包安装验证见 [本轮记录](docs/verification-2026-10-31.md)。源码整合不代表生产部署。

### 开发与部署
- 增加 Ruff lint/format、编辑器/换行规范、frozen dev 安装入口与 CI。
- 本地示例数据库路径改为相对路径；生产配置独立放在 `/etc/ac-bot/`。
- 源码包明确包含锁文件、示例配置和部署文档，避免 README 安装步骤引用缺失文件。
- systemd 模板改用专用账号与 `/opt/ac-bot`；保留 StateDirectory 和 broker 恢复依赖。
- 分离仅 loopback 匿名开发 broker 与认证生产/ACL 模板，提供旧数据迁移流程。

### 可维护性
- 抽取纯表单适配和天气动作构造，保留 Service 兼容入口与既有默认行为。
- README 改为中文用户入口，原验收/复盘原文归档；补贡献、安全与开发文档。
- 纠正 QoS、event retained 与时间/自动恢复边界说明，修复设计文档旧本机链接。
- 不升级原生产锁依赖、不更改协议、控制优先级、心跳/失联保护或历史窗口。

## 0.1.0

已有服务基线：飞书遥控与文本控制、MQTT 桥接、预设与预约、天气建议、
DHT11 历史、夜间闭环及失联恢复。历史证据见 [归档索引](docs/history/README.md)。
