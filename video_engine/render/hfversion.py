"""Compatibility alias for toolchain version discovery."""
import sys
from video_engine.core import hfversion
sys.modules[__name__] = hfversion
