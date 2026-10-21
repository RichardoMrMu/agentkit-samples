import os


def _is_byteplus() -> bool:
    provider = os.getenv("CLOUD_PROVIDER", "").strip().lower()
    return provider == "byteplus" or (
        not provider and bool(os.getenv("BYTEPLUS_REGION"))
    )


# 统一配置模型名称，优先使用环境变量，并按云厂商选择默认值
DEFAULT_MODEL_NAME = (
    "deepseek-v4-1-flash-260910" if _is_byteplus() else "doubao-seed-evolving"
)
MODEL_NAME = os.getenv("MODEL_AGENT_NAME", DEFAULT_MODEL_NAME)
