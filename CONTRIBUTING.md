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

## 提交规范

使用 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)，提交说明统一使用英文。标题格式为 `type(scope): description`，不超过 72 个字符，描述使用祈使句、不加句号。

| type | 用途 |
| --- | --- |
| `feat` | 新功能 |
| `fix` | 行为或缺陷修复 |
| `docs` | 文档变更 |
| `refactor` | 不改变行为的重构 |
| `test` | 测试增加或调整 |
| `perf` | 性能优化 |
| `build` | 工具链、依赖、打包与部署构建配置 |
| `ci` | 持续集成流程 |
| `chore` | 仓库维护 |
| `revert` | 撤销已有改动 |

scope 使用稳定的英文模块名，例如 `service`、`intent`、`cards`、`feishu`、`mqtt`、`weather`、`store`、`deploy`、`repo`。正文与标题空一行，说明变更动机、主要行为及必要的兼容性信息，每行不超过 72 个字符；验证记录仅写实际执行的命令和结果。不在历史提交中补写未经验证的测试结果，不虚构 issue 编号或签署信息。

```text
fix(service): defer card updates until callbacks return

Move post-click card updates out of callback processing so slow network
requests do not delay the callback response.
```

一个提交聚焦一项可审查的逻辑变更，同时包含必要的测试与文档。破坏兼容性的改动使用 `type(scope)!: description`，并在正文末尾以 `BREAKING CHANGE:` 说明影响与迁移方式。撤销提交应在正文列出被撤销的提交哈希和原因。

日常开发基于最新 `main` 建立短期功能分支，检查与评审完成后整合回主分支并删除已合并分支。公开历史改写必须有维护者明确授权、可恢复备份和并发更新保护，不作为日常合并方式。本次历史整理见 [迁移记录](docs/git-history-normalization.md)。
