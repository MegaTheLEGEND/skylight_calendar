"""Email/password sign-in (password_login.py) against a fake Skylight.

Runs the real module over real HTTP against fake_skylight_auth, which models
the live sign-in page, its CSRF token and cookie, and the PKCE check at
/oauth/token. Needs aiohttp; skipped without it.

Run from the repository root: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

try:
    import aiohttp
    import fake_skylight_auth as fake_mod
except ImportError:  # pragma: no cover
    aiohttp = None

PKG = Path(__file__).resolve().parents[1] / "custom_components/skylight"


def load_password_login():
    name = "_skylight_password_login"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PKG / "password_login.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@unittest.skipIf(aiohttp is None, "aiohttp not installed")
class PasswordLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = load_password_login()
        self.fake = fake_mod.FakeSkylight()
        self.runner, self.base = await fake_mod.start(self.fake)
        self.verifier = "v" * 64
        self.url = fake_mod.authorize_url(self.base, fake_mod.challenge_for(self.verifier))
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()

    async def sign_in(self, email=fake_mod.EMAIL, password=fake_mod.PASSWORD, session=None):
        return await self.mod.async_fetch_authorization_code(
            session or self.session,
            email=email,
            password=password,
            authorize_url=self.url,
            redirect_uri=fake_mod.REDIRECT_URI,
        )

    async def exchange(self, code):
        async with self.session.post(f"{self.base}/oauth/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": fake_mod.REDIRECT_URI, "code_verifier": self.verifier,
        }) as resp:
            return resp.status, await resp.json()

    async def test_signs_in_and_returns_a_code_that_exchanges(self):
        code = await self.sign_in()
        self.assertEqual(await self.exchange(code), (200, {
            "access_token": "access-1", "refresh_token": "refresh-1", "token_type": "Bearer",
        }))

    async def test_posts_the_form_like_a_browser(self):
        await self.sign_in(email="Parent@Example.com")
        (posted,) = self.fake.posted
        self.assertEqual(set(posted) - {"_origin"}, {"authenticity_token", "email", "password"})
        self.assertEqual(posted["email"], "Parent@Example.com")
        self.assertEqual(posted["_origin"], self.base)
        # The redirect URI is only parsed, never requested.
        self.assertEqual(
            [m + " " + p.split("?")[0] for m, p in self.fake.log],
            ["GET /oauth/authorize", "GET /auth/session/new", "POST /auth/session", "GET /oauth/authorize"],
        )

    async def test_wrong_password(self):
        with self.assertRaises(self.mod.SkylightInvalidCredentials):
            await self.sign_in(password="nope")
        self.assertEqual(self.fake.codes, {})

    async def test_session_without_cookies_cannot_sign_in(self):
        # HA's shared session keeps no cookies; the CSRF token then fails.
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as no_cookies:
            with self.assertRaises(self.mod.SkylightLoginError) as ctx:
                await self.sign_in(session=no_cookies)
        self.assertNotIsInstance(ctx.exception, self.mod.SkylightInvalidCredentials)

    async def test_unexpected_pages_raise_login_error_for_fallback(self):
        for mode in ("no_form", "deny", "consent"):
            self.fake.mode = mode
            with self.subTest(mode=mode), self.assertRaises(self.mod.SkylightLoginError) as ctx:
                await self.sign_in()
            self.assertNotIsInstance(ctx.exception, self.mod.SkylightInvalidCredentials)

    async def test_password_never_in_error_text(self):
        for mode in ("no_form", "deny", "consent"):
            self.fake.mode = mode
            try:
                await self.sign_in()
            except self.mod.SkylightLoginError as err:
                self.assertNotIn(fake_mod.PASSWORD, str(err))

    def test_parser_finds_only_the_password_form(self):
        parse = self.mod._sign_in_form
        form = parse(fake_mod.FORM.format(token="t0k", flash=""))
        self.assertEqual((form.action, form.method), ("/auth/session", "post"))
        self.assertEqual(form.inputs["authenticity_token"], "t0k")
        self.assertIsNone(parse('<form action="/auth/session" method="post"><input name="email"></form>'))
        self.assertIsNone(parse('<form action="/search"><input type="password" name="p"></form>'))

    def test_code_only_from_the_exact_redirect_uri(self):
        code_from = self.mod._code_from
        uri = fake_mod.REDIRECT_URI
        self.assertEqual(code_from(f"{uri}?code=abc&state=x", uri), "abc")
        self.assertIsNone(code_from("https://ourskylight.com/welcome-not?code=abc", uri))
        self.assertIsNone(code_from("https://evil.example/welcome?code=abc", uri))
        self.assertIsNone(code_from("http://ourskylight.com/welcome?code=abc", uri))
        with self.assertRaises(self.mod.SkylightLoginError):
            code_from(f"{uri}?error=access_denied", uri)


if __name__ == "__main__":
    unittest.main()
