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

    # 飞书自建应用
    app_id: str = ""
    app_secret: str = ""
    chat_id: str = ""

    # 数据源清单与状态
    sources_file: str = "sources.yaml"
    state_file: str = "state/seen.json"
    health_file: str = "state/health.json"

    # 卡片转发按钮 + 多维表格归档（同微博项目）
    forward_enabled: bool = False
    forward_chat_id: str = ""
    bitable_url: str = ""
    bitable_table_name: str = "转发归档"
    bitable_sync_interval_seconds: int = Field(default=600, gt=0)
    card_store_file: str = "state/cards.json"
    forwarded_file: str = "state/forwarded.json"

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
    send_retry_attempts: int = Field(default=3, gt=0)
    image_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    # 单源连续失败到阈值则本轮提前结束
    upstream_failure_threshold: int = Field(default=3, gt=0)
    upstream_error_rest_seconds: int = Field(default=300, gt=0)

    # DeepSeek 内容分类（车圈热点/产品发布/谍照申报/市场数据/资本市场/
    # 出海信息/政策监管/行业观察/广告/汽车无关）。广告、汽车无关和非中国内容默认不推送。
    classification_enabled: bool = True
    drop_offtopic: bool = True
    drop_ads: bool = True
    drop_non_china: bool = True
    # 软文/通稿（promo）不推实时卡片，只落日报池
    promo_to_digest_only: bool = True

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    classify_timeout: float = Field(default=15.0, gt=0)

    # 日报聚合：网站新闻聚成事件，每个事件下挂微博讨论
    digest_enabled: bool = False
    digest_dir: str = "state/digest"
    digest_state_file: str = "state/digest.json"
    # 发送前整副卡片落盘；部分发送失败后重试从断点续发，防止重复卡片
    digest_outbox_file: str = "state/digest_outbox.json"
    # 每期日报同时写一篇飞书文档（完整版档案），链接放进首卡。
    # 需要应用权限 docx:document；开链接分享还需要 drive 权限。
    digest_doc_enabled: bool = True
    digest_doc_folder_token: str = ""  # 空 = 应用自己的云空间根目录
    digest_doc_domain: str = "https://feishu.cn"
    digest_doc_chat_id: str = ""  # 空 = 不单独发文档链接卡片
    digest_send_time: str = "08:00"  # 北京时间
    # 覆盖窗口：早晨发选 yesterday（前一自然日全天）；晚上发选 today（当天）。
    digest_cover: str = "yesterday"
    digest_intro_enabled: bool = True
    digest_llm_timeout: float = Field(default=90.0, gt=0)
    # max_tokens 含思考部分（deepseek-v4-flash 的 reasoning 计入 completion）。
    # 只是上限、按实际生成计费，宽松设置纯为防截断
    digest_cluster_max_tokens: int = Field(default=16000, gt=0)
    digest_pick_max_tokens: int = Field(default=4000, gt=0)
    digest_intro_max_tokens: int = Field(default=4000, gt=0)

    # 详讯精修：每个详讯事件一次 LLM 调用，产出标题与中英对照缩写（软依赖）
    digest_detail_enabled: bool = True
    digest_detail_max_tokens: int = Field(default=8000, gt=0)
    digest_detail_concurrency: int = Field(default=4, gt=0)
    digest_detail_full_text_max_chars: int = Field(default=4000, gt=0)
    # 详讯配图（网站封面图 + 微博图）写进飞书文档，需要 drive 上传权限；
    # 图片大小上限复用 image_max_bytes
    digest_doc_images_enabled: bool = True
    digest_max_images_per_event: int = Field(default=4, ge=0)

    # 账号池：人工筛选的独立博主，日报生成时批量抓当天时间线（软依赖）
    digest_pool_enabled: bool = True
    digest_pool_file: str = "pool.yaml"
    # 池内微博按批分类，减少调用次数；<=1 退回逐条
    pool_classify_batch_size: int = 20

    # 微博搜索补充（软依赖，只搜池子没覆盖的事件）
    digest_weibo_enabled: bool = True
    digest_weibo_window_hours: int = Field(default=48, gt=0)
    digest_weibo_candidates: int = Field(default=12, gt=0)
    # 刚官宣的事件互动量普遍为 0，粉丝量兜底，否则最新鲜的事件反而没讨论
    digest_weibo_min_engagement: int = Field(default=5, ge=0)
    digest_weibo_min_followers: int = Field(default=100_000, ge=0)
    digest_weibo_delay_min_seconds: float = Field(default=3.0, ge=0)
    digest_weibo_delay_max_seconds: float = Field(default=6.0, ge=0)

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
        if self.forward_enabled and not self.forward_chat_id.strip():
            raise ValueError("forward_chat_id is required when forward_enabled is true")
        if self.digest_weibo_delay_min_seconds > self.digest_weibo_delay_max_seconds:
            raise ValueError(
                "digest_weibo_delay_min_seconds must not exceed digest_weibo_delay_max_seconds"
            )
        hour, _, minute = self.digest_send_time.partition(":")
        try:
            if not 0 <= int(hour) <= 23 or not 0 <= int(minute or 0) <= 59:
                raise ValueError
        except ValueError:
            raise ValueError(
                f"digest_send_time must be HH:MM in 24h form: {self.digest_send_time!r}"
            ) from None
        if self.digest_cover not in ("yesterday", "today"):
            raise ValueError(
                f"digest_cover must be 'yesterday' or 'today': {self.digest_cover!r}"
            )
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
