# 历史验收与复盘索引

本目录保存 2026-10-27 规范化前的原始记录，`.original.txt` **保留原文，不改写历史**。
历史记录中的私人账号、路径、网络标识、旧命令和当时结论仅作证据；
它们不是当前安装指南，也不代表此刻生产服务状态。原文换行可由 Git 统一为 LF。

| 归档 | 来源 |
| --- | --- |
| [README 原文](README-2026-10-27.original.txt) | 原用户入口中的个人运行记录、验收与复盘 |
| [飞书联调原文](feishu-bringup-2026-10-27.original.txt) | 原 `docs/feishu-bringup.md` 的部署与测试证据 |
| [MQTT 联调原文](mqtt-bringup-2026-10-27.original.txt) | 原 `docs/mqtt-bringup.md` 的设备/网络验收证据 |
| [旧 systemd unit](ac-bot-service-2026-10-27.original.txt) | 含旧个人账号/路径及 broker 联动修复说明 |
| [旧 broker 配置](mosquitto-ac-2026-10-27.original.txt) | 原全接口匿名监听，仅历史证据，勿部署 |

原 README/联调原文中的相对链接按**原路径**解读；归档为 txt 避免误当当前导航。
设计过程与复盘仍保留在 [2026-10-15 增量设计](../superpowers/specs/2026-10-15-presets-weather-dht11-design.md)，
其中过时协议结论以顶部更正声明及 [现行接口规格 §4](../interface-spec.md#4-mqtt-契约与固件仓库的唯一接口) 为准。
当前安装看 [部署指南](../deployment.md)，普通使用看 [README](../../README.md)。
