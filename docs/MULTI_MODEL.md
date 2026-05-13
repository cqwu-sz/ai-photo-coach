# 多模型 BYOK 指南

> ⚠️ **v17 (订阅与认证改造) 后此文档过时**
>
> - **客户端 BYOK 已下线**：iOS app 不再暴露模型选择/API Key 输入项，`ModelSettingsView` 仅在 `auth.role == "admin"` 时显示
> - **模型由后端中央化管理**：当前生效的 fast / high model 存在 `model_settings` 表，靠 `services/model_config.py` 30s 缓存，admin 通过 `PUT /admin/model` 切换，所有用户下个请求即生效
> - `analyze` 接口仍然接受 `model_id` / `model_api_key` / `model_base_url` 字段，但仅当 `ENABLE_BYOK=true` 时才生效（默认 false，生产关闭）
> - 历史的 11 个 vendor preset 仍保留，但用户不可见，仅给 admin 在 `/admin/model` 选项里选

下面是 PR8 之前的旧文档，仅供参考：

---

AI 摄影教练的视觉分析层是 vendor-agnostic 的。后端通过 `VisionProvider` 抽象暴露一组内置预设（11 个，覆盖 Google / OpenAI / 智谱 / 通义 / DeepSeek / Kimi），任何客户端都可以在每次 `/analyze` 请求里：

1. 沿用后端默认（默认 `gemini-2.5-flash`，operator 通过环境变量喂 fallback key）；
2. 临时切换到另一个内置预设（`model_id` 字段）；
3. 自带 API Key（`model_api_key` 字段，BYOK），后端绝不持久化；
4. 用自定义 `model_base_url` 走自建 OpenAI 兼容代理（如 OpenRouter、本地 vLLM 等）。

## 内置预设速查

| id | vendor | 类型 | JSON 模式 | 备注 |
| --- | --- | --- | --- | --- |
| `gemini-2.5-flash` | google | gemini | schema | 默认快速档，原生视频理解 |
| `gemini-2.5-pro` | google | gemini | schema | 高质量档 |
| `glm-4.6v` | zhipu | openai_compat | json_object | 用户首选，智谱旗舰视觉 |
| `glm-4v-plus` | zhipu | openai_compat | json_object | 备选 |
| `glm-4.1v-thinking-flash` | zhipu | openai_compat | json_object | 带思维链 |
| `gpt-4o` | openai | openai_compat | json_schema | 严格 JSON schema |
| `gpt-4o-mini` | openai | openai_compat | json_schema | 便宜 fallback |
| `qwen-vl-max` | dashscope | openai_compat | json_object | 通义旗舰 |
| `qwen2.5-vl-72b-instruct` | dashscope | openai_compat | json_object | 开源权重档 |
| `deepseek-vl2` | deepseek | openai_compat | json_object | DeepSeek 视觉 |
| `moonshot-v1-128k-vision-preview` | moonshot | openai_compat | json_object | Kimi Vision，长上下文 |

> **video vs. images**：`supports_native_video=true` 的 Provider（Gemini）会拿到全部关键帧；其它通用 OpenAI 兼容 Provider 会按 `max_images`（默认 8）抽样后做 base64 data URL 上传。所以 Gemini 在场景理解的连续性上仍然占优。

## API key 申请入口

| Vendor | 控制台 |
| --- | --- |
| Google Gemini | https://aistudio.google.com/app/apikey |
| OpenAI | https://platform.openai.com/api-keys |
| 智谱 GLM | https://open.bigmodel.cn/usercenter/apikeys |
| 阿里通义千问 | https://dashscope.console.aliyun.com/apiKey |
| DeepSeek | https://platform.deepseek.com/api_keys |
| Moonshot Kimi | https://platform.moonshot.cn/console/api-keys |

## 后端环境变量（operator-side fallback）

```
GEMINI_API_KEY=
OPENAI_API_KEY=
ZHIPU_API_KEY=
DASHSCOPE_API_KEY=
DEEPSEEK_API_KEY=
MOONSHOT_API_KEY=
DEFAULT_MODEL_ID=gemini-2.5-flash    # 全局默认
ENABLE_BYOK=true                     # 关掉则忽略客户端的 model_api_key
```

## 客户端怎么传

### Web (PWA)

* 设置抽屉（齿轮按钮）→ 选模型 → 填 key → 保存。`model_id`、`api_key`、`base_url` 都写在 `localStorage`，每次 `/analyze` 自动随表单字段发送。
* 抽屉里有"测试连通性"按钮，调用 `POST /models/test` 用最便宜的 1-token 调用验证 key 是否生效。

### iOS

* 首页右上齿轮 → `ModelSettingsView`。
* `model_id`、`base_url` 走 `UserDefaults`/`@AppStorage`；`api_key` 走 Keychain（`kSecAttrAccessibleAfterFirstUnlock`）。
* `EnvCaptureViewModel.stopAndAnalyze` 自动读 `ModelConfigStore.currentForRequest()` 写进 multipart body。

## API 速查

* `GET /models` —— 返回 `ModelsResponse { default_model_id, enable_byok, models: [...] }`，`models[].has_operator_key` 表示后端是否给该 vendor 配置了 fallback。
* `POST /models/test` —— body `{model_id, api_key?, base_url?}`，返回 `{ok, snippet, error}`，不消耗主分析配额。
* `POST /analyze` —— 已存在的 multipart 端点，新增可选字段：
    * `model_id`
    * `model_api_key`（永远不记日志）
    * `model_base_url`

## 安全模型

* 客户端 BYOK key **不离开设备**：浏览器在 `localStorage`，iOS 在 Keychain。
* 仅在 `/analyze` 这一次请求里以 multipart `model_api_key` 字段送给后端，后端只用它构造对 vendor 的请求，不持久化、不记 access log（`analyze_request` 日志只记 `byok_key_supplied: bool`）。
* 自定义 `base_url` 同上：仅当次生效。
* 关闭 BYOK：在后端 `ENABLE_BYOK=false` 即可，所有请求都强制走 operator 的 fallback key。

## 已知坑

* 智谱 / 通义 / DeepSeek / Moonshot 都只支持 `response_format: {type: "json_object"}`，结构约束完全靠 prompt + repair pass。如果出错频率高，建议切到 `gpt-4o` 或 `gemini-2.5-flash`（这俩支持 `json_schema` 硬约束）。
* 上传图片走的是 base64 data URL，单图大于 2 MB 容易触发 vendor 的 413；前端已经做压缩，仍可能出现，必要时 vendor 可调小 `max_images`。
* OpenRouter / Together / Anthropic 不在内置列表 —— 走"自定义"通道：`model_id` 填你的实际 model 名，`base_url` 填 `/v1` 兼容地址。
