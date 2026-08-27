"""
mcauth3 - Minecraft Microsoft Authentication.

The whole library lives in this one file, because the entire auth flow is a
single straight-line chain. It looks like this:

    Microsoft (device code) -> Xbox Live -> XSTS -> Minecraft services -> profile

Every step hands a token to the next service, and each service vouches for the
one before it, until we end up holding a Minecraft access token and the
player's profile.

Along the way there are a few quirks that Microsoft never really documents
(the "d=" prefix below is the classic one). When a comment sounds like a war
story, that's usually a sign it exists because the real world forced us into
it - read those carefully.
"""

import time
import requests

# The built-in client ID. It's public on purpose: this is the *device-code*
# flow, so there is no client secret to hide. Users who registered their own
# Azure App can swap in their own ID via `MCMSA(client_id=...)`.
CLIENT_ID = "4a07b708-b86d-4365-a55f-f4f23ecb85ab"

# The scopes we ask for. "XboxLive.signin" is what makes the resulting token
# acceptable to Xbox Live. "offline_access" is the quietly important one: it's
# what lets Microsoft hand us a *refresh* token, so we can re-authenticate
# weeks later without ever bothering the user again.
SCOPE = "XboxLive.signin offline_access openid profile email"

# Every request goes through the session with this timeout. This used to be
# "configured" as `self.session.timeout = 30`, which silently did nothing -
# requests.Session has no such attribute - so now we pass it explicitly on
# every single call and mean it.
DEFAULT_TIMEOUT = 30

# The endpoints that make up this whole dance. The two Microsoft URLs handle
# the device-code flow; the Xbox/Minecraft ones trade the token forward, link
# by link, until we hold what the caller actually wants.
DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"

# XSTS returns HTTP 400 (not 401!) when something is wrong with the account,
# and the real reason lives in a numeric code buried in the response body.
# Without this map, users just saw a bare "400 Client Error" and had no clue
# what to fix. These are the codes you actually run into in the wild.
XSTS_ERROR_MESSAGES = {
    2148916233: "This Microsoft account has no Xbox Live profile. Sign up at xbox.com and try again.",
    2148916235: "This account is banned from Xbox Live.",
    2148916236: "This account requires adult verification.",
    2148916238: "This account is under 18 and requires parental consent.",
}


# A small exception hierarchy so callers can catch *specific* failures instead
# of hoping for a bare Exception. Everything the library throws on purpose
# derives from MCAuthError; the network errors raised by `requests` are
# deliberately NOT subclasses, so a caller can always tell "the library
# rejected this" apart from "the internet is down".
class MCAuthError(Exception):
    """Base class for all mcauth3 errors."""


class OAuthError(MCAuthError):
    """Raised when the OAuth token endpoint rejects a request.

    Covers both the device-code flow (user denied, code expired, ...) and
    refresh-token failures (invalid/expired refresh token, ...).
    """


class AuthTimeoutError(MCAuthError):
    """Raised when the user does not finish verification within the device-code lifetime."""


class XboxAuthError(MCAuthError):
    """Raised when the Xbox Live / XSTS authentication chain fails."""


class MCMSA:
    """One authenticator instance = one HTTP session + a client ID.

    Keep one instance per authentication session (or just reuse a single one).
    The instance owns a requests.Session, which keeps keep-alive connections
    and cookies alive across the many HTTP calls that make up one auth flow -
    creating a fresh instance per request would throw all of that away.
    """

    def __init__(self, client_id=None):
        # `client_id` is the OAuth2 name for what people call the "Azure App
        # ID". If the caller does not pass one we fall back to the built-in
        # public client ID, so `MCMSA()` keeps working with zero setup - and
        # anyone with their own App registration can override it.
        self.client_id = client_id or CLIENT_ID
        self.timeout = DEFAULT_TIMEOUT
        self.session = requests.Session()

    # Request helpers.
    # These two thin wrappers make sure EVERY request automatically carries
    # the timeout. This is what the old `self.session.timeout = 30` was
    # *supposed* to do but didn't - requests has no session-level timeout
    # attribute, so without these, a stalled connection would hang forever.
    def _post(self, url, **kwargs):
        return self.session.post(url, timeout=self.timeout, **kwargs)

    def _get(self, url, **kwargs):
        return self.session.get(url, timeout=self.timeout, **kwargs)

    def start_auth(self):
        # Step 1: ask Microsoft for a device code. What comes back is
        # everything the *user* needs (a short code to type at a URL) plus
        # everything *we* need to poll for their decision. Nothing here
        # requires a login - that's the whole point of the device-code flow.
        data = {
            "client_id": self.client_id,
            "scope": SCOPE,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }

        response = self._post(DEVICE_CODE_URL, data=data, headers=headers)
        response.raise_for_status()

        return response.json()

    def poll_microsoft_token(self, device_code_data):
        # Step 2: keep asking Microsoft "did the user approve yet?" until they
        # do, or until the device code dies. This is where the user does their
        # part - they open the verification URL and type the code that
        # start_auth() printed.
        #
        # Two things used to be broken here, and both are worth knowing:
        #
        #   1. The attempt count was hard-coded at 180. We now compute it from
        #      `expires_in`, so we stop polling the exact moment the device
        #      code stops being valid - there is no point asking about a dead
        #      code.
        #
        #   2. Real terminal errors (user denied, code expired) used to get
        #      swallowed by a catch-all `except`, retried for the full 15
        #      minutes, and finally reported as a confusing "timeout". Now
        #      those fail fast with the real reason.
        device_code = device_code_data['device_code']
        poll_interval = max(device_code_data.get('interval', 5), 5)
        expires_in = int(device_code_data.get('expires_in', 900))
        # We ask once per interval, so the budget is simply expiry / interval.
        max_attempts = max(1, int(expires_in / poll_interval))

        for attempt in range(max_attempts):
            data = {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": self.client_id,
                "device_code": device_code,
            }
            headers = {
                "Content-Type": "application/x-www-form-urlencoded"
            }

            try:
                response = self._post(TOKEN_URL, data=data, headers=headers)
            except requests.exceptions.RequestException:
                # Network hiccup - not the user's fault and not a verdict, so
                # keep polling. Only give up once we've burned every attempt.
                if attempt == max_attempts - 1:
                    raise
                time.sleep(poll_interval)
                continue

            if response.status_code == 200:
                # They said yes. This is the Microsoft access + refresh token
                # pair that the rest of the chain is built on.
                return response.json()

            try:
                error_data = response.json()
            except ValueError:
                raise MCAuthError(
                    f"Unexpected token response (HTTP {response.status_code})"
                )

            error = error_data.get('error')
            if error == 'authorization_pending':
                # Normal state - the user just has not finished yet. Sleep the
                # recommended interval and ask again.
                time.sleep(poll_interval)
            elif error == 'slow_down':
                # Microsoft thinks we are polling too fast. Bump the interval
                # by the 5 seconds their docs specify, then try again later.
                poll_interval += 5
                time.sleep(poll_interval)
            else:
                # authorization_denied / expired_token / bad_verification_code
                # ... These are final answers, not "wait longer". Fail fast so
                # the caller sees the real reason instead of a fake timeout.
                message = (
                    error_data.get('error_description') or error
                    or f"Unknown device-code error (HTTP {response.status_code})"
                )
                raise OAuthError(message)

        # The user never made it in time. Note this is different from them
        # saying "no" - that raises OAuthError above; this is pure staleness.
        raise AuthTimeoutError(
            "Authentication timeout - the user did not finish verification in time"
        )

    def refresh_auth(self, microsoft_refresh_token):
        """Exchange a Microsoft refresh token for a fresh access token, then
        re-run the full Xbox Live -> XSTS -> Minecraft -> profile chain.

        :param microsoft_refresh_token: The ``microsoft_refresh_token`` obtained
            from a previous :meth:`finish_auth` / :meth:`refresh_auth` call.
        :returns: The same ``{"tokens": ..., "profile": ...}`` structure as
            :meth:`finish_auth`.
        """
        # With a stored refresh token we can skip the entire device-code dance
        # and get a brand-new session in a single request - no user
        # interaction at all. This is what lets a launcher or a long-running
        # service stay logged in for months.
        #
        # One Microsoft quirk worth remembering: they ROTATE refresh tokens.
        # Every successful refresh hands back a new one, so callers should
        # store the new token each time - otherwise the old one quietly stops
        # working one day.
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": microsoft_refresh_token,
            "scope": SCOPE,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }

        response = self._post(TOKEN_URL, data=data, headers=headers)

        if response.status_code != 200:
            try:
                error_data = response.json()
            except ValueError:
                error_data = {}
            error = error_data.get('error') or f"HTTP {response.status_code}"
            description = (
                error_data.get('error_description')
                or "Failed to refresh the Microsoft token"
            )
            raise OAuthError(f"{error}: {description}")

        token_response = response.json()

        # Microsoft rotates the refresh token; if for some reason it is not
        # returned, keep the caller's existing one.
        new_refresh_token = token_response.get('refresh_token', microsoft_refresh_token)
        return self._exchange_microsoft_token(
            token_response['access_token'], new_refresh_token
        )

    def _exchange_microsoft_token(self, microsoft_access_token, microsoft_refresh_token=None):
        """Run the shared chain: Xbox Live -> XSTS -> Minecraft login -> profile.

        Used by both :meth:`finish_auth` (fresh device-code token) and
        :meth:`refresh_auth` (refreshed token).
        """
        # The meat of the whole library: take a *Microsoft* access token and
        # trade it forward, link by link, until we hold what the caller
        # actually cares about - a Minecraft access token and the player's
        # profile. Both finish_auth() and refresh_auth() funnel through here,
        # so a fix to the chain fixes both entry points at once.
        xbl_data = self.auth_xbox_live(microsoft_access_token)
        # The XBL response carries two things we need: the token itself, and
        # the user hash ("uhs") that identifies this account to the rest of
        # the chain. The uhs gets embedded in the identityToken later on.
        xbl_token = xbl_data['Token']
        user_hash = xbl_data['DisplayClaims']['xui'][0]['uhs']

        xsts_data = self.auth_xsts(xbl_token)
        xsts_token = xsts_data['Token']

        mc_token_data = self.login_minecraft(user_hash, xsts_token)
        mc_access_token = mc_token_data['access_token']

        profile = self.get_minecraft_pf(mc_access_token)

        return {
            "tokens": {
                "microsoft_access_token": microsoft_access_token,
                "microsoft_refresh_token": microsoft_refresh_token,
                "xbl_token": xbl_token,
                "xsts_token": xsts_token,
                "minecraft_access_token": mc_access_token,
                "expires_in": mc_token_data['expires_in'],
            },
            "profile": profile,
        }

    def finish_auth(self, device_code_data):
        # The interactive path: poll until the user approves, then hand the
        # fresh Microsoft token to the shared chain.
        token_response = self.poll_microsoft_token(device_code_data)
        return self._exchange_microsoft_token(
            token_response['access_token'],
            token_response.get('refresh_token'),
        )

    def auth_xbox_live(self, microsoft_access_token):
        # The first trade: Microsoft token -> Xbox Live token.
        #
        # Quirk: tokens minted by the DEVICE-CODE flow occasionally need a
        # literal "d=" stuck on the front before Xbox Live will accept them -
        # a known Microsoft oddity that nobody really explains. So we try the
        # prefixed form first, and only if Xbox Live rejects it at the HTTP
        # level do we fall back to the plain token.
        #
        # We deliberately fall through ONLY on HTTP errors. If the network is
        # down, a differently-prefixed token is not going to help - and
        # swallowing a network error here would make debugging a nightmare.
        attempts = [f"d={microsoft_access_token}", microsoft_access_token]
        http_errors = []

        for rps_ticket in attempts:
            request_body = {
                "Properties": {
                    "AuthMethod": "RPS",
                    "SiteName": "user.auth.xboxlive.com",
                    "RpsTicket": rps_ticket,
                },
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT",
            }
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            try:
                response = self._post(XBL_AUTH_URL, json=request_body, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.HTTPError as e:
                # Only fall through to the other ticket format on an HTTP-level
                # rejection; network errors propagate immediately.
                http_errors.append(str(e))

        # Both formats were rejected. Bundle the HTTP errors into one readable
        # message so the caller at least has something to go on.
        detail = " | ".join(http_errors) or "no response"
        raise XboxAuthError(
            f"Xbox Live authentication failed with both RPS ticket formats ({detail})"
        )

    def auth_xsts(self, xbl_token):
        # Trade 2: Xbox Live token -> XSTS token. XSTS is Microsoft's
        # "service token" layer; the RelyingParty says which service we are
        # asking for, and Minecraft's services live at this URL.
        #
        # Fun fact: when this call fails, the response is HTTP 400 (not 401),
        # and the actual reason is a numeric XErr code in the body - which is
        # exactly why we map it to something a human can read instead of
        # surfacing a bare status code.
        request_body = {
            "Properties": {
                "SandboxId": "RETAIL",
                "UserTokens": [xbl_token],
            },
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            response = self._post(XSTS_AUTH_URL, json=request_body, headers=headers)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            try:
                # noinspection PyUnboundLocalVariable
                error_data = response.json()
            except ValueError:
                raise XboxAuthError(f"XSTS authorization failed: {e}") from e
            xerr = error_data.get('XErr')
            message = (
                XSTS_ERROR_MESSAGES.get(xerr)
                or error_data.get('Message')
                or f"XSTS authorization failed (HTTP {response.status_code})"
            )
            raise XboxAuthError(message) from e

        return response.json()

    def login_minecraft(self, user_hash, xsts_token):
        # Trade 3: XSTS token -> Minecraft access token. The identityToken
        # format is the fixed "XBL3.0 x=<user_hash>;<xsts_token>" layout that
        # Minecraft's API expects. This is the first hop where we are talking
        # to Mojang's services rather than Microsoft's.
        request_body = {
            "identityToken": f"XBL3.0 x={user_hash};{xsts_token}",
        }
        headers = {
            "Content-Type": "application/json",
        }

        response = self._post(MC_LOGIN_URL, json=request_body, headers=headers)
        response.raise_for_status()
        return response.json()

    def get_minecraft_pf(self, minecraft_access_token):
        # Finally, with a valid Minecraft token we can fetch the player's
        # profile - name, UUID, skins and capes.
        #
        # A 404 here is the "sad trombone" case: the account is perfectly
        # healthy, it just has never owned Minecraft (or has no character), so
        # there is no profile to return. We catch it and say so plainly
        # instead of letting raise_for_status() produce a cryptic
        # "404 Client Error".
        headers = {
            "Authorization": f"Bearer {minecraft_access_token}",
        }

        response = self._get(PROFILE_URL, headers=headers)

        if response.status_code == 404:
            raise MCAuthError(
                "This Microsoft account has no Minecraft profile "
                "(not purchased, or no character created yet)"
            )
        response.raise_for_status()
        return response.json()
