# MQTT 联调

本服务与固件的唯一接口见 [接口规格 §4](interface-spec.md#4-mqtt-契约与固件仓库的唯一接口)。
开发匿名 broker 只绑定 loopback；接入设备前看 [部署指南](deployment.md) 的
生产认证、ACL、防火墙和 listener 地址说明。不要直接改正在运行的 broker。

## 隔离验证清单

- 双端 broker 地址、账号、端口和 prefix 一致，服务与设备使用不同身份。
- 服务仅写 cmd/hb、设备仅写 state/status/env/event，匿名连接被生产配置拒绝。
- 核对发布 QoS、订阅 QoS、retained 三者，不以单一“QoS0”概括整个链路。
- cmd/hb/event 不 retained；env 虽 retained，服务忽略旧重放；状态只是影子缓存。
- 不向真实设备发布试验命令；记录测试 broker、测试固件版本和脱敏日志。
- broker stop/start/restart 与失联保护测试会影响设备，须由操作者单独安排维护窗口。

离线适配器与服务回归：

```sh
uv run --frozen --extra dev python -m pytest -q tests/test_mqtt_client.py tests/test_service_v12.py
```

自动恢复是尽力而为；时间、事件丢失、用户命令顺序限制见
[接口规格 §4.4](interface-spec.md#44-时间与恢复边界)。

## 原始证据

2026-10-27 前的设备、网络与链路验收原文见
[MQTT 联调归档](history/mqtt-bringup-2026-10-27.original.txt)。
旧网络地址/设备标识只属于当时现场，不是开箱默认值。
