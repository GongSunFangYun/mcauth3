"""Unit tests for mcauth3.

The whole auth chain is exercised against mocked HTTP (nothing here touches the
network). We patch the `_post` / `_get` seams on the MCMSA class and drive the
methods through their success and failure paths.
"""
import json
from unittest import mock

import pytest
import requests

from mcauth3 import mcauth3 as mcauth3_mod
from mcauth3 import (
    MCMSA,
    MCAuthError,
    OAuthError,
    AuthTimeoutError,
    XboxAuthError,
)


def make_response(status_code, payload=None):
    """Build a real requests.Response with the given status and JSON body."""
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = json.dumps(payload).encode("utf-8") if payload is not None else b""
    return resp


def device_data(expires_in=900, interval=5):
    return {
        "user_code": "ABC12345",
        "device_code": "device_code_abc",
        "verification_uri": "https://www.microsoft.com/link",
        "expires_in": expires_in,
        "interval": interval,
        "message": "To sign in, use a web browser...",
    }


def xbl_data():
    return {"Token": "xbl_token", "DisplayClaims": {"xui": [{"uhs": "uhs_123"}]}}


# ---------------------------------------------------------------------------
# Constructor & exception hierarchy
# ---------------------------------------------------------------------------

def test_default_client_id_is_builtin():
    assert MCMSA().client_id == mcauth3_mod.CLIENT_ID


def test_custom_client_id_override():
    assert MCMSA(client_id="my-custom-id").client_id == "my-custom-id"


def test_exception_hierarchy():
    for exc in (OAuthError, AuthTimeoutError, XboxAuthError):
        assert issubclass(exc, MCAuthError)
    # network errors are deliberately NOT library errors
    assert not issubclass(requests.exceptions.RequestException, MCAuthError)


# ---------------------------------------------------------------------------
# start_auth
# ---------------------------------------------------------------------------

def test_start_auth_posts_device_code_endpoint():
    auth = MCMSA()
    payload = {
        "user_code": "ABC12345",
        "device_code": "dc",
        "verification_uri": "https://www.microsoft.com/link",
        "expires_in": 900,
        "interval": 5,
    }
    with mock.patch.object(MCMSA, "_post", return_value=make_response(200, payload)) as m_post:
        result = auth.start_auth()

    assert result["user_code"] == "ABC12345"
    m_post.assert_called_once()
    data = m_post.call_args.kwargs["data"]
    assert data["client_id"] == mcauth3_mod.CLIENT_ID
    assert data["scope"] == mcauth3_mod.SCOPE


# ---------------------------------------------------------------------------
# poll_microsoft_token
# ---------------------------------------------------------------------------

def test_poll_returns_token_on_first_ok():
    auth = MCMSA()
    token = {"access_token": "at", "refresh_token": "rt"}
    with mock.patch.object(MCMSA, "_post", return_value=make_response(200, token)):
        assert auth.poll_microsoft_token(device_data()) == token


def test_poll_survives_pending_then_succeeds():
    auth = MCMSA()
    pending = make_response(400, {"error": "authorization_pending"})
    ok = make_response(200, {"access_token": "at", "refresh_token": "rt"})
    with mock.patch.object(MCMSA, "_post", side_effect=[pending, ok]), \
         mock.patch.object(mcauth3_mod.time, "sleep") as m_sleep:
        result = auth.poll_microsoft_token(device_data())
    assert result["access_token"] == "at"
    m_sleep.assert_called_once()


def test_poll_fails_fast_on_denied():
    auth = MCMSA()
    denied = make_response(400, {
        "error": "authorization_denied",
        "error_description": "the user said no",
    })
    with mock.patch.object(MCMSA, "_post", return_value=denied) as m_post, \
         mock.patch.object(mcauth3_mod.time, "sleep"):
        with pytest.raises(OAuthError) as exc:
            auth.poll_microsoft_token(device_data())
    assert "the user said no" in str(exc.value)
    # terminal error must not be retried for the full device-code lifetime
    assert m_post.call_count == 1


def test_poll_raises_on_expired_token():
    auth = MCMSA()
    expired = make_response(400, {"error": "expired_token"})
    with mock.patch.object(MCMSA, "_post", return_value=expired), \
         mock.patch.object(mcauth3_mod.time, "sleep"):
        with pytest.raises(OAuthError):
            auth.poll_microsoft_token(device_data())


def test_poll_times_out_when_user_never_verifies():
    auth = MCMSA()
    pending = make_response(400, {"error": "authorization_pending"})
    # expires_in 5 / interval 5 => exactly one attempt, then give up
    with mock.patch.object(MCMSA, "_post", return_value=pending), \
         mock.patch.object(mcauth3_mod.time, "sleep"):
        with pytest.raises(AuthTimeoutError):
            auth.poll_microsoft_token(device_data(expires_in=5, interval=5))


def test_poll_slow_down_bumps_interval():
    auth = MCMSA()
    slow = make_response(400, {"error": "slow_down"})
    ok = make_response(200, {"access_token": "at"})
    with mock.patch.object(MCMSA, "_post", side_effect=[slow, ok]), \
         mock.patch.object(mcauth3_mod.time, "sleep") as m_sleep:
        auth.poll_microsoft_token(device_data())
    # slow_down sleeps once, with the bumped interval (5 + 5 = 10)
    m_sleep.assert_called_once_with(10)


def test_poll_raises_network_error_on_last_attempt():
    auth = MCMSA()
    with mock.patch.object(MCMSA, "_post",
                           side_effect=requests.exceptions.ConnectionError("boom")):
        with pytest.raises(requests.exceptions.ConnectionError):
            auth.poll_microsoft_token(device_data(expires_in=5, interval=5))


# ---------------------------------------------------------------------------
# refresh_auth
# ---------------------------------------------------------------------------

def test_refresh_auth_uses_refresh_token_grant_and_passes_new_token():
    auth = MCMSA()
    refreshed = {"access_token": "new_at", "refresh_token": "new_rt"}
    canned = {"tokens": {"minecraft_access_token": "mc"}, "profile": {"name": "P"}}

    with mock.patch.object(MCMSA, "_post", return_value=make_response(200, refreshed)) as m_post, \
         mock.patch.object(MCMSA, "_exchange_microsoft_token", return_value=canned) as m_exchange:
        result = auth.refresh_auth("old_rt")

    assert result is canned
    data = m_post.call_args.kwargs["data"]
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == "old_rt"
    assert data["client_id"] == mcauth3_mod.CLIENT_ID
    m_exchange.assert_called_once_with("new_at", "new_rt")


def test_refresh_auth_keeps_old_token_when_not_rotated():
    auth = MCMSA()
    refreshed = {"access_token": "new_at"}  # Microsoft may not always rotate
    with mock.patch.object(MCMSA, "_post", return_value=make_response(200, refreshed)), \
         mock.patch.object(MCMSA, "_exchange_microsoft_token") as m_exchange:
        auth.refresh_auth("old_rt")
    m_exchange.assert_called_once_with("new_at", "old_rt")


def test_refresh_auth_rejects_invalid_token():
    auth = MCMSA()
    err = make_response(400, {
        "error": "invalid_grant",
        "error_description": "The refresh token is not valid",
    })
    with mock.patch.object(MCMSA, "_post", return_value=err):
        with pytest.raises(OAuthError) as exc:
            auth.refresh_auth("dead_token")
    assert "invalid_grant" in str(exc.value)


# ---------------------------------------------------------------------------
# auth_xbox_live
# ---------------------------------------------------------------------------

def test_xbox_live_tries_prefixed_ticket_first():
    auth = MCMSA()
    with mock.patch.object(MCMSA, "_post",
                           return_value=make_response(200, xbl_data())) as m_post:
        assert auth.auth_xbox_live("ms_token")["Token"] == "xbl_token"
    assert m_post.call_args.kwargs["json"]["Properties"]["RpsTicket"] == "d=ms_token"


def test_xbox_live_falls_back_to_plain_token():
    auth = MCMSA()
    rejected = make_response(401, {})
    accepted = make_response(200, xbl_data())
    with mock.patch.object(MCMSA, "_post", side_effect=[rejected, accepted]) as m_post:
        assert auth.auth_xbox_live("ms_token")["Token"] == "xbl_token"
    assert m_post.call_count == 2
    assert m_post.call_args.kwargs["json"]["Properties"]["RpsTicket"] == "ms_token"


def test_xbox_live_raises_when_both_formats_fail():
    auth = MCMSA()
    with mock.patch.object(MCMSA, "_post", return_value=make_response(401, {})):
        with pytest.raises(XboxAuthError):
            auth.auth_xbox_live("ms_token")


def test_xbox_live_network_error_propagates():
    auth = MCMSA()
    with mock.patch.object(MCMSA, "_post",
                           side_effect=requests.exceptions.ConnectionError("net")):
        with pytest.raises(requests.exceptions.ConnectionError):
            auth.auth_xbox_live("ms_token")


# ---------------------------------------------------------------------------
# auth_xsts
# ---------------------------------------------------------------------------

def test_xsts_success():
    auth = MCMSA()
    with mock.patch.object(MCMSA, "_post", return_value=make_response(200, {"Token": "xsts_token"})):
        assert auth.auth_xsts("xbl_token")["Token"] == "xsts_token"


def test_xsts_maps_known_error_code_to_readable_message():
    auth = MCMSA()
    err = make_response(400, {"XErr": 2148916235, "Message": "raw server text"})
    with mock.patch.object(MCMSA, "_post", return_value=err):
        with pytest.raises(XboxAuthError) as exc:
            auth.auth_xsts("xbl_token")
    assert "banned" in str(exc.value)


def test_xsts_unknown_code_keeps_raw_message():
    auth = MCMSA()
    err = make_response(400, {"XErr": 999999, "Message": "Something went wrong"})
    with mock.patch.object(MCMSA, "_post", return_value=err):
        with pytest.raises(XboxAuthError) as exc:
            auth.auth_xsts("xbl_token")
    assert "Something went wrong" in str(exc.value)


# ---------------------------------------------------------------------------
# login_minecraft / get_minecraft_pf
# ---------------------------------------------------------------------------

def test_login_minecraft_builds_identity_token():
    auth = MCMSA()
    with mock.patch.object(MCMSA, "_post",
                           return_value=make_response(200, {"access_token": "mc", "expires_in": 86400})) as m_post:
        result = auth.login_minecraft("uhs_123", "xsts_456")
    assert result["access_token"] == "mc"
    assert m_post.call_args.kwargs["json"]["identityToken"] == "XBL3.0 x=uhs_123;xsts_456"


def test_profile_success():
    auth = MCMSA()
    profile = {"id": "uuid", "name": "Player", "skins": []}
    with mock.patch.object(MCMSA, "_get", return_value=make_response(200, profile)) as m_get:
        assert auth.get_minecraft_pf("mc_token") == profile
    assert m_get.call_args.kwargs["headers"]["Authorization"] == "Bearer mc_token"


def test_profile_404_raises_friendly_error():
    auth = MCMSA()
    with mock.patch.object(MCMSA, "_get", return_value=make_response(404)):
        with pytest.raises(MCAuthError) as exc:
            auth.get_minecraft_pf("mc_token")
    assert "no Minecraft profile" in str(exc.value)


# ---------------------------------------------------------------------------
# _exchange_microsoft_token / finish_auth
# ---------------------------------------------------------------------------

def test_exchange_runs_full_chain_and_builds_result():
    auth = MCMSA()
    with mock.patch.object(MCMSA, "auth_xbox_live", return_value=xbl_data()), \
         mock.patch.object(MCMSA, "auth_xsts", return_value={"Token": "xsts1"}), \
         mock.patch.object(MCMSA, "login_minecraft",
                           return_value={"access_token": "mc1", "expires_in": 86400}), \
         mock.patch.object(MCMSA, "get_minecraft_pf",
                           return_value={"id": "uuid", "name": "Player"}):
        result = auth._exchange_microsoft_token("ms_at", "ms_rt")

    assert result["profile"]["name"] == "Player"
    tokens = result["tokens"]
    assert tokens["microsoft_access_token"] == "ms_at"
    assert tokens["microsoft_refresh_token"] == "ms_rt"
    assert tokens["xbl_token"] == "xbl_token"
    assert tokens["xsts_token"] == "xsts1"
    assert tokens["minecraft_access_token"] == "mc1"
    assert tokens["expires_in"] == 86400


def test_finish_auth_wires_poll_to_exchange():
    auth = MCMSA()
    token_response = {"access_token": "at", "refresh_token": "rt"}
    canned = {"tokens": {}, "profile": {"name": "P"}}
    with mock.patch.object(MCMSA, "poll_microsoft_token", return_value=token_response), \
         mock.patch.object(MCMSA, "_exchange_microsoft_token", return_value=canned) as m_exchange:
        assert auth.finish_auth(device_data()) is canned
    m_exchange.assert_called_once_with("at", "rt")
