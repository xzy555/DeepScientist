# 15 Codex Provider 配置

DeepScientist 不会为 MiniMax、GLM、火山方舟、阿里百炼单独实现一套 provider 适配器。

它复用的是你本机已经能正常工作的 Codex CLI 配置。

推荐顺序始终是：

1. 先让 Codex 自己能工作
2. 确认 `codex` 或 `codex --profile <name>` 在终端里可用
3. 运行 `ds doctor`
4. 再运行 `ds` 或 `ds --codex-profile <name>`

## 三种推荐使用方式

### 1. 默认 OpenAI 登录路径

如果你的 Codex CLI 走的是标准 OpenAI 登录流，就用这一条。

```bash
codex --login
ds doctor
ds
```

### 2. 临时使用 provider profile

如果你已经有一个可用的 Codex profile，例如 `minimax`、`glm`、`ark`、`bailian`，最简单的方式就是直接在启动 `ds` 时透传它。

```bash
codex --profile minimax
ds doctor --codex-profile minimax
ds --codex-profile minimax
```

这是最简单的路径。只是临时试用某个 provider 时，不需要先改 `runners.yaml`。

### 3. 持久化 provider profile

如果你希望 DeepScientist 之后默认就走这个 profile，可以写进 `runners.yaml`：

```yaml
codex:
  enabled: true
  binary: codex
  config_dir: ~/.codex
  profile: minimax
  model: inherit
  model_reasoning_effort: xhigh
  approval_policy: on-request
  sandbox_mode: workspace-write
```

注意：

- 对 provider-backed 的 Codex profile，建议优先使用 `model: inherit`
- 除非你非常确定该 provider 接受你要显式传入的模型名，否则不要再额外硬写一个模型
- DeepScientist 会复用你终端里同一个 `~/.codex/config.toml` 与环境变量

## Provider 一览

| Provider | 官方文档 | 是否需要 Codex 登录 | DeepScientist 应该怎么用 |
|---|---|---|---|
| OpenAI | 正常 Codex 配置即可 | 是 | 不需要 profile，直接 `ds` |
| MiniMax | [MiniMax Codex CLI](https://platform.minimaxi.com/docs/coding-plan/codex-cli) | 否 | 使用你自己的 Codex profile，例如 `ds --codex-profile minimax` |
| GLM | [GLM Coding Plan：其他工具](https://docs.bigmodel.cn/cn/coding-plan/tool/others) | 否 | 使用一个指向 GLM coding endpoint 的 Codex profile |
| 火山方舟 | [Ark Coding Plan 总览](https://www.volcengine.com/docs/82379/1925114?lang=zh) | 否 | 使用一个指向 Ark coding endpoint 的 Codex profile |
| 阿里百炼 | [百炼 Coding Plan：其他工具](https://help.aliyun.com/zh/model-studio/other-tools-coding-plan) | 否 | 使用一个指向 Bailian coding endpoint 的 Codex profile |

## OpenAI

### 需要准备什么

- 正常安装的 Codex CLI
- 已成功执行过一次 `codex --login`，或者在 `codex` 交互界面里完成首次认证

### DeepScientist 命令

```bash
ds doctor
ds
```

### 持久化 runner 配置

```yaml
codex:
  enabled: true
  binary: codex
  config_dir: ~/.codex
  profile: ""
  model: gpt-5.4
```

## MiniMax

MiniMax 是最典型的 profile 模式。它的官方 Codex CLI 文档直接给了自定义 provider 的配置方式，并明确写了 `requires_openai_auth = false`。

官方文档：

- <https://platform.minimaxi.com/docs/coding-plan/codex-cli>

### 需要准备什么

- 已安装 Codex CLI
- 在启动 Codex 和 DeepScientist 的 shell 中可见的 `MINIMAX_API_KEY`
- `~/.codex/config.toml` 中已经配置好的 Codex profile

### Codex 侧配置

MiniMax 官方页面给了真实的 Codex custom provider 示例。profile 名称由你自己决定。下面用 `minimax` 作为示例；如果你已经配置成 `m27`，就继续使用 `m27`。

```toml
[model_providers.minimax]
name = "MiniMax Chat Completions API"
base_url = "https://api.minimaxi.com/v1"
env_key = "MINIMAX_API_KEY"
wire_api = "chat"
requires_openai_auth = false
request_max_retries = 4
stream_max_retries = 10
stream_idle_timeout_ms = 300000

[profiles.minimax]
model = "codex-MiniMax-M2.5"
model_provider = "minimax"
```

然后执行：

```bash
export MINIMAX_API_KEY="..."
codex --profile minimax
```

### DeepScientist 命令

```bash
ds doctor --codex-profile minimax
ds --codex-profile minimax
```

### 持久化 runner 配置

```yaml
codex:
  enabled: true
  binary: codex
  config_dir: ~/.codex
  profile: minimax
  model: inherit
```

## GLM

GLM 的官方文档把 Coding Plan 描述成 OpenAI-compatible 的 coding endpoint，而不是单独的 Codex 登录流程。

官方文档：

- <https://docs.bigmodel.cn/cn/coding-plan/tool/others>
- <https://docs.bigmodel.cn/cn/coding-plan/faq>

### 官方给出的 provider 关键值

- Base URL：`https://open.bigmodel.cn/api/coding/paas/v4`
- API Key：你的 GLM Coding Plan key
- Model：文档中明确写了 `GLM-4.7`，部分场景也支持 `GLM-5`

### 推荐做法

GLM 当前没有像 MiniMax 那样单独给出一篇 Codex CLI 专页。对 DeepScientist 来说，最稳的做法是：

1. 在 `~/.codex/config.toml` 中创建一个指向上面 GLM coding endpoint 的 Codex profile
2. 先确保 `codex --profile glm` 在终端里能工作
3. 再让 DeepScientist 复用同一个 profile

### DeepScientist 命令

```bash
ds doctor --codex-profile glm
ds --codex-profile glm
```

### 持久化 runner 配置

```yaml
codex:
  enabled: true
  binary: codex
  config_dir: ~/.codex
  profile: glm
  model: inherit
```

## 火山方舟

火山方舟的 Coding Plan 官方文档明确列出了 Codex CLI。

官方文档：

- <https://www.volcengine.com/docs/82379/1925114?lang=zh>

### 官方给出的 provider 关键值

- OpenAI-compatible Base URL：`https://ark.cn-beijing.volces.com/api/coding/v3`
- 支持的 coding 模型：`doubao-seed-code-preview-latest`、`ark-code-latest`
- 必须使用 Coding Plan 的 key 和对应的 Coding Plan endpoint

### 推荐做法

先创建一个指向 Ark coding endpoint 的 Codex profile，并先验证：

```bash
codex --profile ark
```

然后再启动 DeepScientist：

```bash
ds doctor --codex-profile ark
ds --codex-profile ark
```

### 持久化 runner 配置

```yaml
codex:
  enabled: true
  binary: codex
  config_dir: ~/.codex
  profile: ark
  model: inherit
```

## 阿里百炼

阿里百炼的 Coding Plan 官方文档也是 OpenAI-compatible endpoint 路径。它特别强调：必须使用 Coding Plan 专属 key 和 endpoint，而不是普通平台 endpoint。

官方文档：

- <https://help.aliyun.com/zh/model-studio/other-tools-coding-plan>
- <https://help.aliyun.com/zh/model-studio/coding-plan-faq>

### 官方给出的 provider 关键值

- OpenAI-compatible Base URL：`https://coding.dashscope.aliyuncs.com/v1`
- API Key：Coding Plan 专属 key，通常形如 `sk-sp-...`
- Model：请以当前百炼 Coding Plan 概览页支持的模型为准

### 推荐做法

先创建一个指向 Bailian Coding Plan endpoint 的 Codex profile，并先验证：

```bash
codex --profile bailian
```

然后再启动 DeepScientist：

```bash
ds doctor --codex-profile bailian
ds --codex-profile bailian
```

### 持久化 runner 配置

```yaml
codex:
  enabled: true
  binary: codex
  config_dir: ~/.codex
  profile: bailian
  model: inherit
```

## 一份统一的排障清单

如果 provider-backed profile 在 DeepScientist 里还是失败：

1. 先手动运行 `codex --profile <name>`
2. 确认 provider API key 在同一个 shell 中可见
3. 确认 Base URL 使用的是 Coding Plan endpoint，而不是普通通用 API endpoint
4. DeepScientist 里优先保持 `model: inherit`
5. 运行 `ds doctor --codex-profile <name>`
6. 最后再运行 `ds --codex-profile <name>`
