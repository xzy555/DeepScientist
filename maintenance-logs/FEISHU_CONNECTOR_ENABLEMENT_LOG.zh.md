# Feishu Connector 正式接入记录

这份文件用于记录本轮将 Feishu 从“已实现但默认不突出”提升为 DeepScientist 一等连接器的过程。

## 目标

- 让 Feishu 在 system connector gate 中默认可见。
- 保留现有实现路线：
  - `FeishuLongConnectionService`
  - `GenericRelayChannel`
  - `FeishuConnectorBridge`
- 补齐面向用户的公开文档，使 Feishu 像 QQ、Weixin、Telegram、WhatsApp 一样可发现、可配置、可绑定。

## 当前进度

- 已创建根目录中英文两份改造记录文件。
- 已将 Feishu 加入默认 system connector 可见列表。
- 已同步更新默认配置测试，使 Feishu 默认被视为 system-enabled connector。
- 已新增 Feishu 独立中英文指南。
- 已在公开 docs 索引和顶层 README 中加入 Feishu 指南入口。
- 已修复 Settings 页里 generic profile 的保存竞态问题，新的 Feishu profile 现在会携带最新 draft payload 直接保存，而不是依赖可能仍未刷新的 React state。

## 已修改文件

- `src/deepscientist/config/models.py`
- `tests/test_init_and_quest.py`
- `docs/en/18_FEISHU_CONNECTOR_GUIDE.md`
- `docs/zh/18_FEISHU_CONNECTOR_GUIDE.md`
- `docs/en/README.md`
- `docs/zh/README.md`
- `README.md`
- `src/ui/src/components/settings/SettingsPage.tsx`
- `src/ui/src/components/settings/ConnectorSettingsForm.tsx`

## 已执行验证

- `python3 -m compileall src/deepscientist/config/models.py tests/test_init_and_quest.py`

## 尚未完成的验证

- 当前环境没有安装 `pytest`，因此未能在这里执行 Feishu 相关的针对性测试。

## 说明

- 本轮不会重写 Feishu 后端，而是复用现有 long-connection/runtime 方案，把它提升成正式可绑定 connector。
