from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTO_NEWS_MONITOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        yaml_file=None,
        yaml_file_encoding="utf-8",
    )

    # 数据源清单与状态
    sources_file: str = "sources.yaml"
    state_file: str = "state/seen.json"
    health_file: str = "state/health.json"

    # 轮询节奏
    poll_interval_seconds: int = Field(default=3600, gt=0)
    # 去重是主信号；单个数据源一轮最多推送的新条目数，防列表异常时刷屏。
    max_new_pushes_per_cycle: int = Field(default=25, gt=0)
    seen_mids_per_account: int = Field(default=300, gt=0)
    # 详情页能提供精确发布时间时，首次发现超过此窗口的旧文章不再补推。
    max_article_age_hours: int = Field(default=72, gt=0)

    # 抓取节流
    source_delay_min_seconds: float = Field(default=1.0, ge=0)
    source_delay_max_seconds: float = Field(default=3.0, ge=0)
    request_timeout: float = Field(default=20.0, gt=0)
    request_retries: int = Field(default=3, ge=0)
    # 单源连续失败到阈值则本轮提前结束
    upstream_failure_threshold: int = Field(default=3, gt=0)
    upstream_error_rest_seconds: int = Field(default=300, gt=0)

    # DeepSeek 内容分类（车圈热点/产品发布/谍照申报/市场数据/资本市场/
    # 出海信息/政策监管/行业观察/广告/汽车无关）。广告、汽车无关和非中国内容默认不推送。
    classification_enabled: bool = True
    drop_offtopic: bool = True
    drop_ads: bool = True
    drop_non_china: bool = True

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    classify_timeout: float = Field(default=15.0, gt=0)

    # 逐条中译英，结果写进 digest 的 title_en/summary_en 供英文站与英文 RSS 使用。
    # 为什么要单独调一次而不与分类合并，见 src/translate.py 模块注释。
    translate_enabled: bool = True
    translate_max_tokens: int = Field(default=4000, gt=0)
    translate_timeout: float = Field(default=30.0, gt=0)

    # 归档目录：每条文章落成 state/digest/YYYY-MM-DD.jsonl，是网站与 RSS 的全部输入。
    digest_dir: str = "state/digest"

    log_level: str = "INFO"
    log_dir: str = "logs"
    console_log: bool = True

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Explicit values > environment > .env > YAML > defaults."""
        yaml_settings = YamlConfigSettingsSource(settings_cls)
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            yaml_settings,
            file_secret_settings,
        )

    @model_validator(mode="after")
    def validate_ranges_and_dependencies(self) -> Settings:
        if self.source_delay_min_seconds > self.source_delay_max_seconds:
            raise ValueError("source_delay_min_seconds must not exceed source_delay_max_seconds")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> Settings:
        yaml_path = Path(path)
        try:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"cannot read YAML config {yaml_path}: {exc}") from exc
        if raw is not None and not isinstance(raw, dict):
            raise ValueError(f"YAML config root must be an object: {yaml_path}")

        class YamlSettings(cls):
            model_config = SettingsConfigDict(**{**cls.model_config, "yaml_file": yaml_path})

        return YamlSettings()
