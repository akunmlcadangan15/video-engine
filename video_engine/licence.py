"""Licence rules shared by asset management and the R14 gate."""
import re

LICENCE_ALLOW = {
    "publicdomain", "pd", "cc0", "cc010", "ccby", "ccby20", "ccby25", "ccby30", "ccby40", "ccbysa", "ccbysa20",
    "ccbysa25", "ccbysa30", "ccbysa40", "pexelslicense", "pexels", "pixabaylicense", "pixabay", "unsplashlicense",
    "unsplash", "mixkitlicense", "mixkit", "mit", "own", "generated",
}
OWN_LICENCES = {"own", "generated"}


def norm_licence(value: str) -> str:
    return re.sub(r"[\s_.\-]", "", value.lower())
