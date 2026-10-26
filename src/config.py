"""配置加载:内置默认 <- config.yaml <- 环境变量 DAILYLOG_API_KEY(与 daily-log 共用变量名)"""
import copy
import os
from pathlib import Path

import yaml

DEFAULTS = {
    "api": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": "",
        "vision_model": "glm-4v-flash",
        "text_model": "glm-4-flash",
    },
    "processing": {
        "max_image_px": 1024,
        "jpeg_quality": 85,
        "call_interval_sec": 0.3,
        "video_ext": [".mp4", ".mov", ".avi", ".mkv", ".3gp"],
    },
    "shots": {
        "interval_sec": 30,      # 固定切段间隔(原始素材长镜头的颗粒度)
        "merge_hash_dist": 6,    # 相邻段 dHash 距离小于该值则合并(静止段)
        "frame_max_px": 1024,
        "transition_threshold": 0.4,  # 场景检测阈值(0-1,越小越灵敏)
        "snap_ratio": 0.35,      # 切分边界向转场吸附的最大距离(占间隔比例)
    },
    "asr": {
        "model": "small",
        "language": "zh",
        "device": "auto",
        "initial_prompt": "以下是普通话的句子。",
    },
    "paths": {"data_dir": "data", "sheets_dir": "sheets"},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(root: Path, config_path: Path | None = None) -> dict:
    if config_path is None:
        config_path = root / "config.yaml"
    cfg = DEFAULTS
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = _merge(DEFAULTS, yaml.safe_load(f) or {})
    env_key = os.environ.get("DAILYLOG_API_KEY", "").strip()
    if env_key:
        cfg["api"]["api_key"] = env_key
    return cfg


def data_dir(root: Path, cfg: dict) -> Path:
    return root / cfg["paths"]["data_dir"]


def sheets_dir(root: Path, cfg: dict) -> Path:
    return root / cfg["paths"]["sheets_dir"]
