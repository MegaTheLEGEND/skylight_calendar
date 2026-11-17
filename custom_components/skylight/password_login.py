"""Sign in to Skylight with an email and password, without a browser.

Skylight has no password grant: its only sign-in is the hosted page that the
OAuth authorize URL sends a browser to. This module does what the browser does,
over plain HTTP:

1. GET the authorize URL. Skylight redirects to its sign-in form
   (``/auth/session/new``), a server-rendered Rails form with a CSRF token.
2. POST the email, password and token to the form's action (``/auth/session``).
3. Follow redirects back through ``/oauth/authorize`` until Skylight redirects
   to the registered redirect URI, and read ``code`` from that URL. The redirect
   URI itself is never fetched.

The caller exchanges the code exactly as for a code the user pasted, so PKCE,
token storage and refresh are unchanged.

The password is only sent to Skylight's own sign-in form, over HTTPS, and is
never stored or logged.

Kept free of Home Assistant imports so the offline tests can load it directly.
"""

from __future__ import annotations

from html.parser import HTMLParser
import logging
import urllib.parse as _up

import aiohttp

_LOGGER = logging.getLogger(__name__)

_MAX_REDIRECTS = 10
_TIMEOUT = aiohttp.ClientTimeout(total=20)
_SESSION_PATH = "/auth/session"
_HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "User-Agent": "HomeAssistant-Skylight",
}


class SkylightLoginError(Exception):
    """Sign-in didn't go the way Skylight's page normally does.

    The page may have changed, or asked for something we can't answer (a
    captcha, a consent screen). Callers fall back to signing in in a browser.
    """


class SkylightInvalidCredentials(SkylightLoginError):
    """Skylight showed its sign-in form again: wrong email or password."""


class _Form:
    def __init__(self, action: str, method: str) -> None:
        self.action = action
        self.method = method
        self.inputs: dict[str, str] = {}
        self.types: set[str] = set()


class _FormParser(HTMLParser):
    """Collect every <form> with its action, method and named inputs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[_Form] = []
        self._current: _Form | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: (v or "") for k, v in attrs}
        if tag == "form":
            self._current = _Form(attr.get("action", ""), attr.get("method", "get").lower())
            self.forms.append(self._current)
        elif tag == "input" and self._current is not None:
            self._current.types.add(attr.get("type", "text").lower())
            if attr.get("name"):
                self._current.inputs[attr["name"]] = attr.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None


def _sign_in_form(html: str) -> _Form | None:
    """The form that posts to /auth/session with a password field, if any."""
    parser = _FormParser()
    parser.feed(html)
    for form in parser.forms:
        path = _up.urlparse(form.action).path
        if path.rstrip("/") == _SESSION_PATH and "password" in form.types:
            return form
    return None


def _code_from(location: str, redirect_uri: str) -> str | None:
    """The code from a redirect to ``redirect_uri``, or None if it isn't one."""
    target = _up.urlparse(location)
    expected = _up.urlparse(redirect_uri)
    if (target.scheme, target.netloc, target.path) != (
        expected.scheme,
        expected.netloc,
        expected.path,
    ):
        return None
    query = _up.parse_qs(target.query)
    if "error" in query:
        raise SkylightLoginError(f"Skylight refused the sign-in: {query['error'][0]}")
    code = query.get("code", [None])[0]
    if not code:
        raise SkylightLoginError("Skylight redirected back without a code")
    return code


async def _follow(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    redirect_uri: str,
    **kwargs,
) -> tuple[str | None, str, str]:
    """Request ``url`` and follow redirects by hand.

    Returns ``(code, final_url, body)``. ``code`` is set as soon as a redirect
    points at ``redirect_uri``, which is never requested. Otherwise the final
    page's URL and HTML are returned.
    """
    headers = _HEADERS | kwargs.pop("headers", {})
    for _ in range(_MAX_REDIRECTS):
        async with session.request(
            method,
            url,
            allow_redirects=False,
            timeout=_TIMEOUT,
            headers=headers,
            **kwargs,
        ) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    raise SkylightLoginError(f"HTTP {resp.status} without a Location")
                location = _up.urljoin(str(resp.url), location)
                code = _code_from(location, redirect_uri)
                if code:
                    return code, location, ""
                # As a browser does, a 303 (and a 302 after a POST) becomes a GET.
                if resp.status in (302, 303):
                    method = "GET"
                    kwargs.pop("data", None)
                    headers = _HEADERS
                url = location
                continue
            body = await resp.text()
            return None, str(resp.url), body
    raise SkylightLoginError("Too many redirects during sign-in")


async def async_fetch_authorization_code(
    session: aiohttp.ClientSession,
    *,
    email: str,
    password: str,
    authorize_url: str,
    redirect_uri: str,
) -> str:
    """Sign in on Skylight's hosted page and return the authorization code.

    ``session`` must keep cookies (an ``aiohttp.CookieJar``): Skylight links the
    sign-in to the pending authorization through its session cookie.

    Raises SkylightInvalidCredentials when Skylight shows the sign-in form
    again, SkylightLoginError for anything unexpected, and lets aiohttp's
    connection errors through.
    """
    try:
        code, page_url, html = await _follow(session, "GET", authorize_url, redirect_uri)
        if code:
            # Already signed in (shouldn't happen with prompt=login).
            return code

        form = _sign_in_form(html)
        if form is None or form.method != "post":
            raise SkylightLoginError(f"No sign-in form at {_up.urlparse(page_url).path}")

        fields = dict(form.inputs)
        fields["email"] = email
        fields["password"] = password
        action = _up.urljoin(page_url, form.action)
        origin = "{0.scheme}://{0.netloc}".format(_up.urlparse(action))

        code, page_url, html = await _follow(
            session,
            "POST",
            action,
            redirect_uri,
            data=fields,
            headers={"Origin": origin, "Referer": page_url},
        )
    except aiohttp.ClientResponseError as err:
        raise SkylightLoginError(f"HTTP {err.status} during sign-in") from err

    if code:
        return code
    if _sign_in_form(html) is not None:
        raise SkylightInvalidCredentials("Skylight showed the sign-in form again")
    raise SkylightLoginError(
        f"Sign-in ended on an unexpected page: {_up.urlparse(page_url).path}"
    )
