"""
The timezone all llm_brain dates and times are in: `timezone:` in config.yaml
(an IANA name, e.g. "Europe/Berlin"), else the system's own timezone.
"""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"


def tz_name() -> str:
    try:
        configured = (yaml.safe_load(CONFIG_PATH.read_text()) or {}).get("timezone")
        if configured:
            return configured
    except OSError:
        pass
    if os.environ.get("TZ"):
        return os.environ["TZ"]
    link = os.path.realpath("/etc/localtime")  # macOS/Linux: …/zoneinfo/Area/City
    return link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else "UTC"


TZ_NAME = tz_name()
TZ = ZoneInfo(TZ_NAME)
