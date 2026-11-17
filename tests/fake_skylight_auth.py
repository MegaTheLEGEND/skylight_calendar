"""In-process stand-in for Skylight's hosted sign-in and token endpoint.

Modelled on the live pages (Nov 2026): /oauth/authorize redirects a signed-out
browser to /auth/session/new, a server-rendered Rails form with a hidden
authenticity_token tied to the session cookie, email and password inputs, a
password-toggle button and a submit button named "button". A correct POST to
/auth/session redirects back to /oauth/authorize, which redirects to the
registered redirect URI with ?code=. A wrong password re-renders the form
(422). /oauth/token checks the PKCE verifier against the challenge.

Used by test_password_login.py; needs aiohttp.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse as up

from aiohttp import web

REDIRECT_URI = "https://ourskylight.com/welcome"
EMAIL = "parent@example.com"
PASSWORD = "correct horse battery staple"

FORM = """<!DOCTYPE html><html><head>
<meta name="csrf-param" content="authenticity_token" />
<meta name="csrf-token" content="{token}" /></head><body>
<h1>Log In</h1>{flash}
<form action="/auth/session" accept-charset="UTF-8" method="post">
<input type="hidden" name="authenticity_token" value="{token}" autocomplete="off" />
<label for="email">Email address</label><input type="email" name="email" id="email" />
<label for="password">Password</label><input type="password" name="password" id="password" />
<button type="button" class="goat-form-field__password-toggle" aria-label="Show password"></button>
<button name="button" type="submit">Log In</button>
</form><a href="/auth/passwords/new">Forgot password?</a></body></html>"""


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class FakeSkylight:
    """State and routes. ``mode`` switches in behaviour for edge-case tests."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.log: list[tuple[str, str]] = []
        self.posted: list[dict] = []
        self.mode = "normal"  # or: "no_form", "deny", "consent"

    def _session(self, request: web.Request) -> tuple[str, dict]:
        sid = request.cookies.get("_skylight_session")
        if sid not in self.sessions:
            sid = secrets.token_hex(8)
            self.sessions[sid] = {"token": secrets.token_urlsafe(16)}
        return sid, self.sessions[sid]

    @staticmethod
    def _with_cookie(resp: web.StreamResponse, sid: str) -> web.StreamResponse:
        resp.set_cookie("_skylight_session", sid, httponly=True, path="/")
        return resp

    async def authorize(self, request: web.Request) -> web.StreamResponse:
        self.log.append(("GET", request.path_qs))
        sid, sess = self._session(request)
        q = request.query
        if q.get("client_id") != "skylight-mobile" or q.get("redirect_uri") != REDIRECT_URI:
            return web.Response(status=400, text="The redirect uri included is not valid.")
        if not sess.get("user") or (q.get("prompt") == "login" and not sess.get("fresh")):
            sess["return_to"] = request.path_qs
            sess.pop("user", None)
            raise self._with_cookie(web.HTTPFound("/auth/session/new"), sid)
        sess.pop("fresh", None)
        if self.mode == "deny":
            raise web.HTTPFound(f"{REDIRECT_URI}?error=access_denied")
        if self.mode == "consent":
            return web.Response(content_type="text/html", text=(
                '<form action="/oauth/authorize" method="post">'
                '<input type="hidden" name="client_id" value="skylight-mobile">'
                '<button>Authorize</button></form>'))
        code = secrets.token_urlsafe(12)
        self.codes[code] = {"challenge": q.get("code_challenge"), "user": sess["user"]}
        raise web.HTTPFound(f"{REDIRECT_URI}?code={code}")

    async def login_form(self, request: web.Request) -> web.StreamResponse:
        self.log.append(("GET", request.path_qs))
        sid, sess = self._session(request)
        if self.mode == "no_form":
            return self._with_cookie(web.Response(content_type="text/html", text="<p>Down for maintenance</p>"), sid)
        return self._with_cookie(
            web.Response(content_type="text/html", text=FORM.format(token=sess["token"], flash="")), sid
        )

    async def login(self, request: web.Request) -> web.StreamResponse:
        self.log.append(("POST", request.path_qs))
        sid, sess = self._session(request)
        form = await request.post()
        self.posted.append({**form, "_origin": request.headers.get("Origin")})
        if form.get("authenticity_token") != sess["token"]:
            # Rails: InvalidAuthenticityToken.
            return web.Response(status=422, text="The change you wanted was rejected.")
        if form.get("email", "").lower() != EMAIL or form.get("password") != PASSWORD:
            return web.Response(
                status=422,
                content_type="text/html",
                text=FORM.format(token=sess["token"], flash="<p>Invalid email or password.</p>"),
            )
        sess["user"] = EMAIL
        sess["fresh"] = True
        raise web.HTTPFound(sess.pop("return_to", "/"))

    async def token(self, request: web.Request) -> web.Response:
        form = await request.post()
        entry = self.codes.pop(form.get("code"), None)
        if (
            entry is None
            or form.get("grant_type") != "authorization_code"
            or form.get("redirect_uri") != REDIRECT_URI
            or challenge_for(form.get("code_verifier", "")) != entry["challenge"]
        ):
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response(
            {"access_token": "access-1", "refresh_token": "refresh-1", "token_type": "Bearer"}
        )

    async def frames(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"data": [{"id": "4242", "type": "frame", "attributes": {"name": "Family"}}]}
        )

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/oauth/authorize", self.authorize)
        app.router.add_get("/auth/session/new", self.login_form)
        app.router.add_post("/auth/session", self.login)
        app.router.add_post("/oauth/token", self.token)
        app.router.add_get("/api/frames", self.frames)
        return app


async def start(fake: FakeSkylight) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def authorize_url(base: str, challenge: str) -> str:
    return f"{base}/oauth/authorize?" + up.urlencode({
        "client_id": "skylight-mobile",
        "response_type": "code",
        "scope": "everything",
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
    })
