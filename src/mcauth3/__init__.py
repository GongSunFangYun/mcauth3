# The public surface of the package. Re-exporting the classes here means
# callers can do `from mcauth3 import MCMSA, OAuthError` without reaching into
# the implementation module. Every exception derives from MCAuthError, so
# people who just want to catch "any mcauth3 failure" need only one base class.
from .mcauth3 import (
    MCMSA,
    MCAuthError,
    OAuthError,
    AuthTimeoutError,
    XboxAuthError,
)

__version__ = "1.1.0"
__author__ = "GongSunFangYun"
