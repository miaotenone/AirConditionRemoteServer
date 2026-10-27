## 目的与范围

说明用户问题、主要变化与未改变的行为。

## 验证

- [ ] 新行为/bugfix 有先失败后通过的测试
- [ ] `uv lock --check`
- [ ] `uv run --frozen --extra dev ruff check .`
- [ ] `uv run --frozen --extra dev ruff format --check .`
- [ ] `uv run --frozen --extra dev python -m pytest -q`
- [ ] `uv build` 与隔离 wheel 安装 smoke check

填写实际结果、warning 与阻塞；未执行的检查不要勾选。

## 兼容与安全

- [ ] 未提交真实 `.env`、数据库、凭据或个人标识
- [ ] MQTT/保护/历史窗口未变；如有变化已明确双端兼容与回归方案
- [ ] 已审查 lock diff，未无故升级生产依赖
- [ ] 文档 / CHANGELOG 与用户可见变化同步

无需以连接生产或控制真实空调作为默认 PR 验证。
