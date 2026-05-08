"""Built-in vision-model registry exposed via GET /models."""
from __future__ import annotations

from .base import ProviderConfig

# Order matters: the first vendor block is shown first in dropdowns.
BUILTIN_MODELS: list[ProviderConfig] = [
    # ---- Google Gemini (native video understanding) ----------------------
    ProviderConfig(
        id="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        vendor="google",
        kind="gemini",
        model_id="gemini-2.5-flash",
        supports_native_video=True,
        max_images=16,
        json_schema_mode="schema",
        api_key_env="GEMINI_API_KEY",
        notes="原生多模态视频理解 + 严格 schema, 默认快速档.",
    ),
    ProviderConfig(
        id="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        vendor="google",
        kind="gemini",
        model_id="gemini-2.5-pro",
        supports_native_video=True,
        max_images=16,
        json_schema_mode="schema",
        api_key_env="GEMINI_API_KEY",
        notes="原生多模态高质量档.",
    ),
    # ---- 智谱 GLM (default per user) ------------------------------------
    ProviderConfig(
        id="glm-4.6v",
        display_name="智谱 GLM-4.6V",
        vendor="zhipu",
        kind="openai_compat",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model_id="glm-4.6v",
        supports_native_video=False,
        max_images=8,
        json_schema_mode="object",
        api_key_env="ZHIPU_API_KEY",
        notes="智谱旗舰视觉版 (用户默认).",
    ),
    ProviderConfig(
        id="glm-4v-plus",
        display_name="智谱 GLM-4V Plus",
        vendor="zhipu",
        kind="openai_compat",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model_id="glm-4v-plus",
        supports_native_video=False,
        max_images=8,
        json_schema_mode="object",
        api_key_env="ZHIPU_API_KEY",
        notes="备用 (4.6V 不可用时切这个).",
    ),
    ProviderConfig(
        id="glm-4.1v-thinking-flash",
        display_name="智谱 GLM-4.1V Thinking Flash",
        vendor="zhipu",
        kind="openai_compat",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model_id="glm-4.1v-thinking-flash",
        supports_native_video=False,
        max_images=8,
        json_schema_mode="object",
        api_key_env="ZHIPU_API_KEY",
        notes="带思维链的视觉版.",
    ),
    # ---- OpenAI ----------------------------------------------------------
    ProviderConfig(
        id="gpt-4o",
        display_name="GPT-4o",
        vendor="openai",
        kind="openai_compat",
        base_url="https://api.openai.com/v1",
        model_id="gpt-4o",
        supports_native_video=False,
        max_images=8,
        json_schema_mode="schema",
        api_key_env="OPENAI_API_KEY",
        notes="OpenAI 旗舰多模态 + strict json_schema.",
    ),
    ProviderConfig(
        id="gpt-4o-mini",
        display_name="GPT-4o mini",
        vendor="openai",
        kind="openai_compat",
        base_url="https://api.openai.com/v1",
        model_id="gpt-4o-mini",
        supports_native_video=False,
        max_images=8,
        json_schema_mode="schema",
        api_key_env="OPENAI_API_KEY",
        notes="便宜 fallback.",
    ),
    # ---- 阿里 通义千问 DashScope ----------------------------------------
    ProviderConfig(
        id="qwen-vl-max",
        display_name="通义千问 VL Max",
        vendor="dashscope",
        kind="openai_compat",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_id="qwen-vl-max",
        supports_native_video=False,
        max_images=8,
        json_schema_mode="object",
        api_key_env="DASHSCOPE_API_KEY",
        notes="通义最强视觉档.",
    ),
    ProviderConfig(
        id="qwen2.5-vl-72b-instruct",
        display_name="Qwen2.5-VL 72B",
        vendor="dashscope",
        kind="openai_compat",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_id="qwen2.5-vl-72b-instruct",
        supports_native_video=False,
        max_images=8,
        json_schema_mode="object",
        api_key_env="DASHSCOPE_API_KEY",
        notes="开源权重档.",
    ),
    # ---- DeepSeek -------------------------------------------------------
    ProviderConfig(
        id="deepseek-vl2",
        display_name="DeepSeek-VL2",
        vendor="deepseek",
        kind="openai_compat",
        base_url="https://api.deepseek.com/v1",
        model_id="deepseek-vl2",
        supports_native_video=False,
        max_images=6,
        json_schema_mode="object",
        api_key_env="DEEPSEEK_API_KEY",
        notes="DeepSeek 视觉版.",
    ),
    # ---- Kimi / Moonshot ------------------------------------------------
    ProviderConfig(
        id="moonshot-v1-128k-vision-preview",
        display_name="Kimi Moonshot Vision (128k)",
        vendor="moonshot",
        kind="openai_compat",
        base_url="https://api.moonshot.cn/v1",
        model_id="moonshot-v1-128k-vision-preview",
        supports_native_video=False,
        max_images=6,
        json_schema_mode="object",
        api_key_env="MOONSHOT_API_KEY",
        notes="Kimi Vision Preview, 长上下文.",
    ),
]

MODELS_BY_ID: dict[str, ProviderConfig] = {m.id: m for m in BUILTIN_MODELS}

DEFAULT_MODEL_ID = "gemini-2.5-flash"


def find_model(model_id: str) -> ProviderConfig | None:
    return MODELS_BY_ID.get(model_id)
