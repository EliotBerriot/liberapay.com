# coding: utf8

from __future__ import absolute_import, division, print_function, unicode_literals

from base64 import b64encode
import json

from pando.exceptions import MalformedBody, UnknownBodyType
from pando.http.request import Request
from pando.http.response import Response

from liberapay.constants import SESSION
from liberapay.security import csrf
from liberapay.testing import Harness


class Tests(Harness):

    def setUp(self):
        Harness.setUp(self)
        self.client.website.canonical_scheme = 'https'
        self.client.website.canonical_host = 'example.com'
        self._canonical_domain = self.client.website.canonical_domain
        self.client.website.canonical_domain = b'.example.com'

    def tearDown(self):
        Harness.tearDown(self)
        website = self.client.website
        website.canonical_scheme = website.env.canonical_scheme
        website.canonical_host = website.env.canonical_host
        website.canonical_domain = self._canonical_domain

    def test_canonize_canonizes(self):
        response = self.client.GxT("/",
                                   HTTP_HOST=b'example.com',
                                   HTTP_X_FORWARDED_PROTO=b'http',
                                   )
        assert response.code == 302
        assert response.headers[b'Location'] == b'https://example.com/'
        assert response.headers[b'Cache-Control'] == b'public, max-age=86400'

    def test_no_cookies_over_http(self):
        """
        We don't want to send cookies over HTTP, especially not CSRF and
        session cookies, for obvious security reasons.
        """
        alice = self.make_participant('alice')
        redirect = self.client.GET("/",
                                   auth_as=alice,
                                   HTTP_X_FORWARDED_PROTO=b'http',
                                   HTTP_HOST=b'example.com',
                                   raise_immediately=False,
                                   )
        assert redirect.code == 302
        assert not redirect.headers.cookie

    def test_early_failures_dont_break_everything(self):
        old_from_wsgi = Request.from_wsgi
        def broken_from_wsgi(*a, **kw):
            raise Response(400)
        try:
            Request.from_wsgi = classmethod(broken_from_wsgi)
            assert self.client.GET("/", raise_immediately=False).code == 400
        finally:
            Request.from_wsgi = old_from_wsgi

    def test_i18n_subdomain_works(self):
        r = self.client.GET(
            '/',
            HTTP_X_FORWARDED_PROTO=b'https', HTTP_HOST=b'fr.example.com',
            raise_immediately=False,
        )
        assert r.code == 200
        assert '<html lang="fr">' in r.text
        assert 'À propos' in r.text

    def test_i18n_subdomain_is_redirected_to_https(self):
        r = self.client.GET(
            '/',
            HTTP_X_FORWARDED_PROTO=b'http', HTTP_HOST=b'en.example.com',
            raise_immediately=False,
        )
        assert r.code == 302
        assert not r.headers.cookie
        assert r.headers[b'Location'] == b'https://en.example.com/'

    def test_csrf_cookie_properties(self):
        r = self.client.GET(
            '/',
            HTTP_X_FORWARDED_PROTO=b'https', HTTP_HOST=b'en.example.com',
            csrf_token=None, raise_immediately=False,
        )
        assert r.code == 200
        cookie = r.headers.cookie[csrf.CSRF_TOKEN]
        assert cookie[str('domain')] == str('.example.com')
        assert cookie[str('expires')][-4:] == str(' GMT')
        assert cookie[str('path')] == str('/')
        assert cookie[str('secure')] is True


class Tests2(Harness):

    def test_basic_auth_works_and_doesnt_return_a_session_cookie(self):
        alice = self.make_participant('alice')
        password = 'password'
        alice.update_password(password)
        auth_header = b'Basic ' + b64encode(('%s:%s' % (alice.id, password)).encode('ascii'))
        r = self.client.GET('/', HTTP_AUTHORIZATION=auth_header)
        assert r.code == 200
        assert SESSION not in r.headers.cookie

    def test_basic_auth_malformed_header_returns_400(self):
        auth_header = b'Basic ' + b64encode(b'bad')
        r = self.client.GxT('/', HTTP_AUTHORIZATION=auth_header)
        assert r.code == 400
        assert r.text == 'Malformed "Authorization" header'

    def test_basic_auth_bad_userid_returns_401(self):
        auth_header = b'Basic ' + b64encode(b'admin:admin')
        r = self.client.GxT('/', HTTP_AUTHORIZATION=auth_header)
        assert r.code == 401

    def test_basic_auth_no_password_returns_401(self):
        alice = self.make_participant('alice')
        assert alice.id == 1
        auth_header = b'Basic ' + b64encode(b'1:')
        r = self.client.GxT('/', HTTP_AUTHORIZATION=auth_header)
        assert r.code == 401

    def test_accept_header_is_respected(self):
        r = self.client.GET('/about/stats', HTTP_ACCEPT=b'application/json')
        assert r.headers[b'Content-Type'] == b'application/json; charset=UTF-8'
        json.loads(r.text)

    def test_error_spt_works(self):
        r = self.client.POST('/', csrf_token=False, raise_immediately=False)
        assert r.code == 403

    def test_cors_is_not_allowed_by_default(self):
        r = self.client.GET('/')
        assert b'Access-Control-Allow-Origin' not in r.headers

    def test_cors_is_allowed_for_assets(self):
        r = self.client.GET('/assets/jquery.min.js')
        assert r.code == 200
        assert r.headers[b'Access-Control-Allow-Origin'] == b'*'

    def test_caching_of_assets(self):
        r = self.client.GET('/assets/jquery.min.js')
        assert r.headers[b'Cache-Control'] == b'public, max-age=3600'
        assert b'Vary' not in r.headers
        assert not r.headers.cookie

    def test_caching_of_assets_with_etag(self):
        r = self.client.GET(self.client.website.asset('jquery.min.js'))
        assert r.headers[b'Cache-Control'] == b'public, max-age=31536000'
        assert b'Vary' not in r.headers
        assert not r.headers.cookie

    def test_caching_of_simplates(self):
        r = self.client.GET('/')
        assert r.headers[b'Cache-Control'] == b'no-cache'
        assert b'Vary' not in r.headers

    def test_no_csrf_cookie(self):
        r = self.client.POST('/', csrf_token=False, raise_immediately=False)
        assert r.code == 403
        assert "Bad CSRF cookie" in r.text
        assert csrf.CSRF_TOKEN in r.headers.cookie

    def test_no_csrf_cookie_unknown_method_on_asset(self):
        r = self.client.hit('UNKNOWN', '/assets/base.css', csrf_token=False,
                            raise_immediately=False)
        assert r.code == 200  # this should be a 405, that's a "bug" in aspen

    def test_bad_csrf_cookie(self):
        r = self.client.POST('/', csrf_token='bad_token', raise_immediately=False)
        assert r.code == 403
        assert "Bad CSRF cookie" in r.text
        assert r.headers.cookie[csrf.CSRF_TOKEN].value != 'bad_token'

    def test_csrf_cookie_set_for_most_requests(self):
        r = self.client.GET('/')
        assert csrf.CSRF_TOKEN in r.headers.cookie

    def test_no_csrf_cookie_set_for_assets(self):
        r = self.client.GET('/assets/base.css')
        assert csrf.CSRF_TOKEN not in r.headers.cookie

    def test_sanitize_token_passes_through_good_token(self):
        token = 'ddddeeeeaaaaddddbbbbeeeeeeeeffff'
        assert csrf._sanitize_token(token) == token

    def test_sanitize_token_rejects_overlong_token(self):
        token = 'ddddeeeeaaaaddddbbbbeeeeeeeefffff'
        assert csrf._sanitize_token(token) is None

    def test_sanitize_token_rejects_underlong_token(self):
        token = 'ddddeeeeaaaaddddbbbbeeeeeeeefff'
        assert csrf._sanitize_token(token) is None

    def test_sanitize_token_rejects_goofy_token(self):
        token = 'ddddeeeeaaaadddd bbbbeeeeeeeefff'
        assert csrf._sanitize_token(token) is None

    def test_malformed_body(self):
        with self.assertRaises(MalformedBody):
            self.client.POST('/', body=b'a', content_type=b'application/json')

    def test_unknown_body_type(self):
        with self.assertRaises(UnknownBodyType):
            self.client.POST('/', body=b'x', content_type=b'unknown/x')

    def test_non_dict_body(self):
        r = self.client.POST('/', body=b'[]', content_type=b'application/json')
        assert r.code == 200
