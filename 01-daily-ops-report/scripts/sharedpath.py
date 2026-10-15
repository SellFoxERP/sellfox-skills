"""定位 _shared 根目录的最小引导模块（sharedpath.py）。

为什么需要它：``config_loader`` 位于 ``_shared/scripts/``，而我们**必须先知道 shared_root
才能把它加入 sys.path**；此时还不能 import config_loader（先有鸡还是先有蛋）。
所以这里用最小解析直接读技能侧 ``config/paths.yaml`` 的 ``shared_root``。

解析优先级（与 config_loader 保持一致）：
  显式/环境变量 ``SELLFOX_SHARED_ROOT`` > 技能侧 ``config/paths.yaml`` 的 ``shared_root``
  > 兜底 ``<skill_root>/../_shared``。
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def shared_root(skill_root: str | Path) -> Path:
    """返回 _shared 的绝对路径。"""
    skill_root = Path(skill_root)
    env = os.environ.get("SELLFOX_SHARED_ROOT")
    if env:
        return Path(env).expanduser().resolve()

    paths_yaml = skill_root / "config" / "paths.yaml"
    if paths_yaml.exists():
        for line in paths_yaml.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*shared_root\s*:\s*(.+?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                value = m.group(1).strip().strip('"').strip("'")
                if value:
                    return Path(value).expanduser().resolve()

    bundled = skill_root / "_shared"
    return (bundled if bundled.is_dir() else skill_root.parent / "_shared").resolve()