# 贡献指南

1. 先读 [README](README.md)、[开发指南](docs/development.md) 和 [接口规格](docs/interface-spec.md)。
2. 新行为或缺陷修复先增加失败测试；保持 MQTT 字段、字段顺序、QoS/retained、
   心跳保护与历史窗口兼容。协议变更先讨论双端方案，不顺手修改固件。
3. 用 `uv sync --frozen --extra dev` 安装，提交前运行 Ruff lint、format check、
   全部 pytest 和 `uv build`。命令见开发指南。
4. PR 描述动机、行为边界、测试结果与已知阻塞；有用户可见变化时更新 CHANGELOG。
5. 不提交真实 `.env`、数据库、token、open_id、私人路径或生产日志。
   复现样例请脱敏，不在 CI 接入生产。

避免纯机械重写与无关升级。保留历史设计/验收证据，用明确更正说明覆盖过时结论。
变更锁文件时逐项审查生产依赖；当前验证配对为 lark-oapi 1.7.4 / pycryptodome 3.24.1。
