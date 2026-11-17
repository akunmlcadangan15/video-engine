"""Identity-preserving resolver alias for the public profile package."""
import sys
from video_engine import profiles
sys.modules[__name__] = profiles
