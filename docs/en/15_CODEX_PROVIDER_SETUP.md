# 15 Codex Provider Setup

DeepScientist does not implement separate provider adapters for MiniMax, GLM, Volcengine Ark, or Alibaba Bailian.

Instead, it reuses the same Codex CLI setup that already works in your terminal.

The recommended order is always:

1. make Codex itself work first
2. confirm `codex` or `codex --profile <name>` works in a terminal
3. run `ds doctor`
4. run `ds` or `ds --codex-profile <name>`

## Three supported patterns

### 1. Default OpenAI login path

Use this when your Codex CLI works through the standard OpenAI login flow.

```bash
codex --login
ds doctor
ds
```

### 2. One-off provider profile

Use this when you already have a named Codex profile such as `minimax`, `glm`, `ark`, or `bailian`.

```bash
codex --profile minimax
ds doctor --codex-profile minimax
ds --codex-profile minimax
```

This is the simplest path. You do not need to edit `runners.yaml` just to try one provider-backed session.

### 3. Persistent provider profile

Use this when you want DeepScientist to keep using the same profile by default.

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

Important:

- keep `model: inherit` for provider-backed Codex profiles unless you are certain the provider accepts the explicit model id you plan to send
- DeepScientist will reuse the same `~/.codex/config.toml` and environment that your terminal Codex already uses

## Provider matrix

| Provider | Official docs | Codex login needed | What DeepScientist should use |
|---|---|---|---|
| OpenAI | use the normal Codex setup | Yes | no profile; run `ds` |
| MiniMax | [MiniMax Codex CLI](https://platform.minimaxi.com/docs/coding-plan/codex-cli) | No | your Codex profile, for example `ds --codex-profile minimax` |
| GLM | [GLM Coding Plan: Other Tools](https://docs.bigmodel.cn/cn/coding-plan/tool/others) | No | a Codex profile that targets the GLM coding endpoint |
| Volcengine Ark | [Ark Coding Plan Overview](https://www.volcengine.com/docs/82379/1925114?lang=zh) | No | a Codex profile that targets the Ark coding endpoint |
| Alibaba Bailian | [Bailian Coding Plan: Other Tools](https://help.aliyun.com/zh/model-studio/other-tools-coding-plan) | No | a Codex profile that targets the Bailian coding endpoint |

## OpenAI

### What to prepare

- a normal Codex CLI install
- a successful `codex --login` or `codex` interactive first-run setup

### DeepScientist commands

```bash
ds doctor
ds
```

### Persistent runner config

```yaml
codex:
  enabled: true
  binary: codex
  config_dir: ~/.codex
  profile: ""
  model: gpt-5.4
```

## MiniMax

MiniMax is the clearest profile-based case. Its official Codex CLI guide configures a custom Codex provider and sets `requires_openai_auth = false`.

Official doc:

- <https://platform.minimaxi.com/docs/coding-plan/codex-cli>

### What to prepare

- Codex CLI installed
- `MINIMAX_API_KEY` available in the shell that starts Codex and DeepScientist
- a working Codex profile in `~/.codex/config.toml`

### Codex-side setup

MiniMax's official page provides a real Codex custom-provider example. The profile name is yours to choose. Use `minimax` below as an example; if you already created `m27`, keep using `m27`.

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

Then:

```bash
export MINIMAX_API_KEY="..."
codex --profile minimax
```

### DeepScientist commands

```bash
ds doctor --codex-profile minimax
ds --codex-profile minimax
```

### Persistent runner config

```yaml
codex:
  enabled: true
  binary: codex
  config_dir: ~/.codex
  profile: minimax
  model: inherit
```

## GLM

GLM documents the Coding Plan as an OpenAI-compatible coding endpoint rather than a dedicated Codex login flow.

Official docs:

- <https://docs.bigmodel.cn/cn/coding-plan/tool/others>
- <https://docs.bigmodel.cn/cn/coding-plan/faq>

### Official provider values

- Base URL: `https://open.bigmodel.cn/api/coding/paas/v4`
- API key: your GLM Coding Plan key
- Model: `GLM-4.7` for the documented path, or `GLM-5` where supported

### Recommended Codex adaptation

GLM does not currently publish a separate Codex CLI page in the same style as MiniMax. The practical DeepScientist path is:

1. create a Codex profile in `~/.codex/config.toml` that points to the GLM coding endpoint above
2. make sure `codex --profile glm` works in a terminal first
3. run DeepScientist with the same profile

### DeepScientist commands

```bash
ds doctor --codex-profile glm
ds --codex-profile glm
```

### Persistent runner config

```yaml
codex:
  enabled: true
  binary: codex
  config_dir: ~/.codex
  profile: glm
  model: inherit
```

## Volcengine Ark

Volcengine Ark explicitly lists Codex CLI as a supported coding tool.

Official doc:

- <https://www.volcengine.com/docs/82379/1925114?lang=zh>

### Official provider values

- OpenAI-compatible Base URL: `https://ark.cn-beijing.volces.com/api/coding/v3`
- Supported coding models: `doubao-seed-code-preview-latest`, `ark-code-latest`
- Use the Coding Plan key and the exact Coding Plan endpoint

### Recommended Codex adaptation

Create a Codex profile that targets the Ark coding endpoint and test it directly first:

```bash
codex --profile ark
```

Then start DeepScientist with the same profile:

```bash
ds doctor --codex-profile ark
ds --codex-profile ark
```

### Persistent runner config

```yaml
codex:
  enabled: true
  binary: codex
  config_dir: ~/.codex
  profile: ark
  model: inherit
```

## Alibaba Bailian

Bailian documents Coding Plan as an OpenAI-compatible coding endpoint. It requires the Coding Plan-specific key and endpoint, not the generic platform endpoint.

Official docs:

- <https://help.aliyun.com/zh/model-studio/other-tools-coding-plan>
- <https://help.aliyun.com/zh/model-studio/coding-plan-faq>

### Official provider values

- OpenAI-compatible Base URL: `https://coding.dashscope.aliyuncs.com/v1`
- API key: Coding Plan-specific key, typically `sk-sp-...`
- Model: choose a Coding Plan-supported model from the current Bailian overview

### Recommended Codex adaptation

Create a Codex profile that points to the Bailian Coding Plan endpoint and test it directly first:

```bash
codex --profile bailian
```

Then start DeepScientist with the same profile:

```bash
ds doctor --codex-profile bailian
ds --codex-profile bailian
```

### Persistent runner config

```yaml
codex:
  enabled: true
  binary: codex
  config_dir: ~/.codex
  profile: bailian
  model: inherit
```

## Troubleshooting checklist

If a provider-backed profile still fails in DeepScientist:

1. run `codex --profile <name>` manually first
2. confirm the provider API key is present in the same shell
3. confirm the provider-specific Base URL is the Coding Plan endpoint, not the generic API endpoint
4. keep DeepScientist runner `model: inherit` unless you need an explicit override
5. run `ds doctor --codex-profile <name>`
6. only after that run `ds --codex-profile <name>`
