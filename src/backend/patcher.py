"""
TikUtils backend: patcher.

The MP4 patching core lives in the vendored tiktok-lossless-upload
submodule and is imported here as ``patcher_core``. This module only keeps
the app-facing encode settings manager.
"""

import json
import logging
from pathlib import Path

try:
    from .patcher_core import (
        DEFAULTS,
        ENCODE_DEFAULTS,
        encode_for_tiktok,
        get_default_config,
        get_patcher_config,
        load_patcher_config,
        patch_mp4,
        patch_video,
        remux_for_tiktok,
    )
except ImportError as exc:
    raise ImportError(
        "patcher core is missing; run: "
        "git submodule update --init --depth 1"
    ) from exc

log = logging.getLogger("TikUtils.patcher")


class ConfigManager:
    """Persist the small set of user-facing encode settings."""

    def __init__(self):
        self.config_dir = Path.home() / ".local" / "share" / "net.buwryy.TikUtils"
        self.config_path = self.config_dir / "settings.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.default_config = dict(ENCODE_DEFAULTS)
        self.config = dict(self.default_config)
        self.load()

    def load(self):
        if not self.config_path.exists():
            return
        try:
            with open(self.config_path, "r") as handle:
                saved = json.load(handle)
            for key, value in saved.items():
                if key in self.default_config:
                    self.config[key] = value
            log.debug("loaded config from %s", self.config_path)
        except Exception as exc:
            log.warning("failed to load config: %s", exc)

    def save(self, new_config):
        filtered = {
            key: value for key, value in new_config.items()
            if key in self.default_config
        }
        with open(self.config_path, "w") as handle:
            json.dump(filtered, handle, indent=2)
        self.config = filtered
        log.info("configuration saved to disk")

    def get_defaults(self):
        return dict(self.default_config)


__all__ = [
    "ConfigManager",
    "DEFAULTS",
    "ENCODE_DEFAULTS",
    "encode_for_tiktok",
    "get_default_config",
    "get_patcher_config",
    "load_patcher_config",
    "patch_mp4",
    "patch_video",
    "remux_for_tiktok",
]
