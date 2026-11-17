"""Config flow: OAuth2 authorization_code + PKCE.

Flow shape:
  step_user          → ask for the Skylight email and password, sign in on
                       Skylight's hosted page over HTTP (password_login) and
                       exchange the resulting code. Nothing to copy.
  step_browser       → fallback when that sign-in hits something unexpected:
                       show the authorize URL as a link, and the user pastes
                       back the full callback URL or just the ?code=... value.
  step_pick_frame    → if the account has >1 frame, choose which one.
  step_reauth / step_reconfigure → same as step_user, email prefilled.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import urllib.parse as _up
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    SkylightAPI,
    SkylightAPIError,
    SkylightAuthError,
    exchange_authorization_code,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_FINGERPRINT,
    CONF_FRAME_ID,
    CONF_FRAME_NAME,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    OAUTH_SCOPE,
    SCAN_INTERVAL_OPTIONS,
)
from .password_login import (
    SkylightInvalidCredentials,
    SkylightLoginError,
    async_fetch_authorization_code,
)

_LOGGER = logging.getLogger(__name__)

CONF_CODE = "code"


def _pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge) for RFC 7636 S256 PKCE."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _authorize_url(challenge: str) -> str:
    qs = _up.urlencode(
        {
            "client_id": OAUTH_CLIENT_ID,
            "response_type": "code",
            "scope": OAUTH_SCOPE,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "login",
        }
    )
    return f"{OAUTH_AUTHORIZE_URL}?{qs}"


def _extract_code(raw: str) -> str | None:
    """Accept either a raw code or the full callback URL and return the code."""
    raw = raw.strip()
    if not raw:
        return None
    if "://" in raw or raw.startswith("/"):
        try:
            qs = _up.parse_qs(_up.urlparse(raw).query)
        except ValueError:
            return None
        code = qs.get("code", [None])[0]
        return code
    # Strip a stray leading "code=" if the user pasted the fragment.
    if raw.lower().startswith("code="):
        return raw.split("=", 1)[1]
    return raw


class SkylightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """OAuth authorization_code + PKCE config flow."""

    VERSION = 2

    def __init__(self) -> None:
        self._verifier: str = ""
        self._challenge: str = ""
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._device_fingerprint: str = ""
        self._frames: list[dict] = []
        self._reauth_entry: ConfigEntry | None = None
        self._email: str = ""

    # ── Primary user step: email + password ──────────────────────────────

    def _ensure_pkce(self) -> str:
        """Authorize URL for this flow; PKCE is generated once per flow."""
        if not self._verifier:
            self._verifier, self._challenge = _pkce_pair()
        return _authorize_url(self._challenge)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        email = self._email

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            # Each attempt gets a fresh PKCE pair: a code is only ever issued
            # for the challenge it was requested with.
            self._verifier = ""
            auth_url = self._ensure_pkce()
            # Skylight ties the sign-in to the pending authorization with its
            # session cookie, and HA's shared session keeps no cookies, so use
            # a throwaway session with its own jar on HA's connector.
            session = async_create_clientsession(
                self.hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar()
            )
            try:
                code = await async_fetch_authorization_code(
                    session,
                    email=email,
                    password=user_input[CONF_PASSWORD],
                    authorize_url=auth_url,
                    redirect_uri=OAUTH_REDIRECT_URI,
                )
            except SkylightInvalidCredentials:
                errors["base"] = "invalid_auth"
            except SkylightLoginError as err:
                # Never log the password; the error text doesn't contain it.
                _LOGGER.warning(
                    "Signing in to Skylight directly failed (%s); "
                    "falling back to signing in with a browser", err
                )
                self._email = email
                return await self.async_step_browser()
            except (aiohttp.ClientError, TimeoutError):
                _LOGGER.warning("Couldn't reach Skylight's sign-in page", exc_info=True)
                errors["base"] = "cannot_connect"
            else:
                self._email = email
                result = await self._async_finish_with_code(code, errors)
                if result is not None:
                    return result
                if errors.get("base") == "code_rejected":
                    # Rejected straight after Skylight issued it: not something
                    # the user can fix by retyping, so offer the browser route.
                    return await self.async_step_browser()
            finally:
                session.detach()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=email or vol.UNDEFINED): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
                    ),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD, autocomplete="current-password"
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # ── Fallback: sign in in a browser and paste the code ────────────────

    async def async_step_browser(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        auth_url = self._ensure_pkce()

        if user_input is not None:
            code = _extract_code(user_input.get(CONF_CODE, ""))
            if not code:
                errors[CONF_CODE] = "invalid_code"
            else:
                result = await self._async_finish_with_code(code, errors)
                if result is not None:
                    return result

        return self.async_show_form(
            step_id="browser",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            description_placeholders={"auth_url": auth_url},
            errors=errors,
        )

    async def _async_finish_with_code(
        self, code: str, errors: dict[str, str]
    ) -> FlowResult | None:
        """Exchange ``code`` for tokens and move on; None (with errors) to re-show."""
        session = async_get_clientsession(self.hass)
        # New fingerprint per install so multiple HA instances don't collide.
        self._device_fingerprint = secrets.token_hex(16)
        try:
            tokens = await exchange_authorization_code(
                session,
                code=code,
                code_verifier=self._verifier,
                device_fingerprint=self._device_fingerprint,
            )
        except SkylightAuthError as err:
            _LOGGER.warning("OAuth code exchange failed: %s", err)
            errors["base"] = "code_rejected"
            return None
        except SkylightAPIError:
            _LOGGER.exception("Skylight OAuth token endpoint error")
            errors["base"] = "cannot_connect"
            return None

        self._access_token = tokens["access_token"]
        self._refresh_token = tokens["refresh_token"]

        if self._reauth_entry is not None:
            return await self._finish_reauth()

        api = SkylightAPI(
            session=session,
            access_token=self._access_token,
            refresh_token=self._refresh_token,
            device_fingerprint=self._device_fingerprint,
        )
        try:
            self._frames = await api.get_frames()
        except SkylightAPIError:
            _LOGGER.exception("Failed to enumerate frames")
            errors["base"] = "cannot_connect"
            return None

        if not self._frames:
            errors["base"] = "no_frames"
            return None
        if len(self._frames) == 1:
            return await self._create_entry(self._frames[0])
        return await self.async_step_pick_frame()

    # ── Frame selection ─────────────────────────────────────────────────

    async def async_step_pick_frame(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        configured_ids = {
            e.data.get(CONF_FRAME_ID) for e in self._async_current_entries()
        }
        available = [f for f in self._frames if f["id"] not in configured_ids]
        if not available:
            return self.async_abort(reason="all_frames_configured")

        if user_input is not None:
            frame_id = user_input[CONF_FRAME_ID]
            frame = next((f for f in available if f["id"] == frame_id), None)
            if frame:
                return await self._create_entry(frame)

        options = {f["id"]: f["name"] for f in available}
        return self.async_show_form(
            step_id="pick_frame",
            data_schema=vol.Schema({vol.Required(CONF_FRAME_ID): vol.In(options)}),
        )

    async def _create_entry(self, frame: dict) -> FlowResult:
        await self.async_set_unique_id(f"skylight_frame_{frame['id']}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=frame["name"],
            data={
                CONF_ACCESS_TOKEN: self._access_token,
                CONF_REFRESH_TOKEN: self._refresh_token,
                CONF_DEVICE_FINGERPRINT: self._device_fingerprint,
                CONF_FRAME_ID: frame["id"],
                CONF_FRAME_NAME: frame["name"],
                # Only to prefill the email when signing in again. The password
                # is never stored.
                CONF_EMAIL: self._email,
            },
        )

    # ── Reauth ──────────────────────────────────────────────────────────

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is not None:
            self._email = self._reauth_entry.data.get(CONF_EMAIL, "")
        return await self.async_step_user()

    async def _finish_reauth(self) -> FlowResult:
        entry = self._reauth_entry
        assert entry is not None
        new_data = {
            **entry.data,
            CONF_ACCESS_TOKEN: self._access_token,
            CONF_REFRESH_TOKEN: self._refresh_token,
            CONF_DEVICE_FINGERPRINT: self._device_fingerprint,
        }
        if self._email:
            new_data[CONF_EMAIL] = self._email
        self.hass.config_entries.async_update_entry(entry, data=new_data)
        await self.hass.config_entries.async_reload(entry.entry_id)
        return self.async_abort(reason="reauth_successful")

    # ── Reconfigure ─────────────────────────────────────────────────────

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is not None:
            self._email = self._reauth_entry.data.get(CONF_EMAIL, "")
        return await self.async_step_user()

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> SkylightOptionsFlow:
        return SkylightOptionsFlow(config_entry)


class SkylightOptionsFlow(config_entries.OptionsFlow):
    """Tune how often each Skylight resource is polled."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        # Stored privately rather than as self.config_entry: assigning that
        # attribute is deprecated in current HA, but accepting the entry here
        # still works across versions.
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        key, default=options.get(key, default)
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    )
                    for key, default in SCAN_INTERVAL_OPTIONS.items()
                }
            ),
        )
