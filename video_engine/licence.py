"""Licence rules shared by asset management, the credits.txt parser and the R14 gate."""
import re

LICENCE_ALLOW = {
    "publicdomain", "pd", "cc0", "cc010", "ccby", "ccby20", "ccby25", "ccby30", "ccby40", "ccbysa", "ccbysa20",
    "ccbysa25", "ccbysa30", "ccbysa40", "pexelslicense", "pexels", "pixabaylicense", "pixabay", "unsplashlicense",
    "unsplash", "mixkitlicense", "mixkit", "mit", "own", "generated", "epidemicsoundlicence", "epidemicsoundlicense",
}
OWN_LICENCES = {"own", "generated"}
# The credit line written for an Epidemic Sound file ends with this phrase and this URL. British and American
# spellings of "licence" both normalise onto the allowlist; any other wording does not.
EPIDEMIC_LICENCE = "Epidemic Sound licence"
EPIDEMIC_URL = "https://www.epidemicsound.com"
_EPIDEMIC_NORMS = {"epidemicsoundlicence", "epidemicsoundlicense"}
_EPIDEMIC_URLS = {EPIDEMIC_URL, "https://epidemicsound.com"}

# Spelling variants of the allowed Creative Commons licences after norm_licence: CC BY / CC BY-SA with a version and
# an optional edition or jurisdiction port ("CC BY 4.0 International", "CC BY-SA 3.0 IGO", "CC BY 3.0 DE") and CC0
# ("CC0 1.0 Universal"). NonCommercial and NoDerivatives variants never match.
_CC_FREE = re.compile(r"^ccby(?:sa)?(?:\d{2}(?:international|intl|unported|generic|igo|[a-z]{2})?)?$")
_CC_ZERO = re.compile(r"^cc0(?:10)?(?:universal)?$")
_CC_RESTRICTED = ("nc", "nd")


def norm_licence(value: str) -> str:
    return re.sub(r"[\s_.\-]", "", value.lower())


def epidemic_licence(value: str) -> bool:
    """True only for the Epidemic Sound credit phrase (licence or license)."""
    return norm_licence(value) in _EPIDEMIC_NORMS


def epidemic_url(value: str) -> bool:
    """True when `value` is the Epidemic Sound site URL, with or without a trailing slash."""
    return value.strip().rstrip("/") in _EPIDEMIC_URLS


def licence_allowed(value: str) -> bool:
    """True for an allow-listed licence or a spelling variant of CC0, CC BY or CC BY-SA; false for every CC
    NonCommercial / NoDerivatives licence and anything unknown. Epidemic Sound is the credit phrase only."""
    n = norm_licence(value)
    if n in LICENCE_ALLOW or _CC_ZERO.match(n):
        return True
    return bool(_CC_FREE.match(n)) and not n.endswith(_CC_RESTRICTED)


def is_licence_field(value: str) -> bool:
    """A credits.txt field that names a licence: an allowed one or any CC BY variant (NC/ND included, so R14 can
    reject it by name instead of reporting an unknown licence)."""
    n = norm_licence(value)
    return licence_allowed(value) or n.startswith("ccby")
