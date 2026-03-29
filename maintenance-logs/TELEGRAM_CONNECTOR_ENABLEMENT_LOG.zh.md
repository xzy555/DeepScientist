# Telegram Connector 正式接入记录

这份文件用于记录本轮将 Telegram 从“已实现但默认不突出”提升为 DeepScientist 一等连接器的过程。

## 目标

- 让 Telegram 在 system connector gate 中默认可见。
- 保留现有实现路线：
  - `TelegramPollingService`
  - `GenericRelayChannel`
  - `TelegramConnectorBridge`
- 补齐面向用户的公开文档，使 Telegram 像 QQ / Weixin 一样可发现、可配置、可绑定。

## 当前进度

- 已创建根目录中英文两份改造记录文件。
- 已将 Telegram 加入默认 system connector 可见列表。
- 已同步更新默认配置相关测试，使 Telegram 默认被视为 system-enabled connector。
- 已新增 Telegram 独立中英文指南。
- 已在公开 docs 索引和顶层 README 中加入 Telegram 指南入口。
- 已修复 Settings 页里 generic profile 的保存竞态问题，新的 Telegram profile 现在会携带最新 draft payload 直接保存，而不是依赖可能仍未刷新的 React state。

## 已修改文件

- `src/deepscientist/config/models.py`
- `tests/test_init_and_quest.py`
- `tests/test_generic_connectors.py`
- `docs/en/16_TELEGRAM_CONNECTOR_GUIDE.md`
- `docs/zh/16_TELEGRAM_CONNECTOR_GUIDE.md`
- `docs/en/README.md`
- `docs/zh/README.md`
- `README.md`
- `src/ui/src/components/settings/SettingsPage.tsx`
- `src/ui/src/components/settings/ConnectorSettingsForm.tsx`

## 已执行验证

- `python3 -m compileall src/deepscientist/config/models.py tests/test_init_and_quest.py tests/test_generic_connectors.py`

## 尚未完成的验证

- 已尝试运行针对性 `pytest`，但当前环境没有安装 `pytest`，因此未能在这里执行测试。

## 说明

- 本轮不会重写 Telegram 后端，而是复用现有 polling/runtime 方案，把它提升成正式可绑定 connector。
