from typing import Optional
import asyncio
import logging
import pickle

import httpx
import bs4

from . import urls


class AsyncClient(httpx.AsyncClient):
    """
    An async HTTP(S) client specialized for PoliTo servers.

    You must call login(...) after the creation of the instance.
    """

    event = asyncio.Lock()

    def __init__(self, *args, **kwargs):
        event_hooks = kwargs.pop("event_hooks", {})
        _initkwargs = {
            "timeout": httpx.Timeout(10.0),
            "follow_redirects": True,
            "event_hooks": {
                "request": [
                    self._catch_forced_redirect,
                    *event_hooks.get("request", []),
                ],
                "response": [
                    self._handle_throttling,

                    # The following conditions will create GET
                    # redirects that will be converted to POST
                    # request by catch_forced_redirect()
                    self._handle_login_request,
                    self._handle_exipiring_password,
                    self._handle_sso_request,

                    self._check_auth,
                    self._savecookies,

                    # Log requests info in debugging logger
                    self._debugging_logger,

                    *event_hooks.get("response", []),
                ],
            }
        }

        super().__init__(*args, **{**kwargs, **_initkwargs})

    async def send(self, *args, **kwargs):
        async with self.event:
            return await super().send(*args, **kwargs)


    async def signin(
        self,
        username: str,
        password: str,
        cookie_path: Optional[str] = None
    ) -> None:
        """
        Set username, password and load cookies (if possible).
        """

        self._username = username
        self._password = password
        self._cookie_path = cookie_path

        # Loads nothing if cookie_path hasn't been set
        self._load_cookies()

        # Send the first request:
        # - If cookies are valid, nothing will happen.
        # - If cookies are invalid, expired or haven't been loaded, the custom
        #   HTTP client will login, and some more cookies we need to complete
        #   login from the main page, without reading the body
        homepage = urls.did/"pls/portal30/sviluppo.pagina_studente_2016.main"
        async with self.stream("GET", homepage):
            pass

    @property
    def username(self):
        """Return username or raise an error if it hasn't been set yet"""
        if not hasattr(self, "_username"):
            raise LoginError("You must login first.")
        return self._username

    @property
    def password(self):
        """Return password or raise an error if it hasn't been set yet"""
        if not hasattr(self, "_password"):
            raise LoginError("You must login first.")
        return self._password

    def _load_cookies(self) -> None:
        """
        Load cookies from the `cookies` file
        """
        # Don't save cookies if cookie path hasn't been setted
        if self._cookie_path is None:
            return

        try:
            with open(self._cookie_path, "rb") as f:
                jar_cookies = pickle.load(f)
        except FileNotFoundError:
            logging.warning("Cookie file not found")
            return

        # Load cookies into session
        for domain, pc in jar_cookies.items():
            for path, c in pc.items():
                for k, v in c.items():
                    self.cookies.set(k, v.value, domain=domain, path=path)

        self.cookies.jar.clear_expired_cookies()


    @staticmethod
    def _redirect_to(response: httpx.Response, url: str):
        """
        Convert the response to a redirect to the given url
        """
        response.status_code = 302
        response.headers["Location"] = url

    async def _catch_forced_redirect(self, request):
        """
        Transform GET request to POST
        """
        url = request.url
        if url.copy_with(params=None) == urls.login and url.params:
            self._get_to_post_redirect(request)
        elif url.path == "/Shibboleth.sso/SAML2/POST":
            self._get_to_post_redirect(request)

    async def _handle_throttling(self, response):
        """
        Sometimes the server throttle us answering with 502 status
        code because of too many requests, we'll retry in 5 seconds
        """
        if response.status_code == 502:
            # The server is throttling us because of
            # too many requests, retry in 5 seconds
            logging.info(
                "%s has been throttled, retrying in 5 seconds...", response.url)
            await asyncio.sleep(5)
            self._redirect_to(response, response.url)

    async def _savecookies(self, _):
        """
        Dump cookies in a file
        """
        # TODO: saving cookies on a file each time we make a
        #       request could cause some performance issues
        if self._cookie_path is not None:
            with open(self._cookie_path, "wb") as cookies:
                pickle.dump(self.cookies.jar._cookies, cookies)

    @staticmethod
    def _get_to_post_redirect(request):
        """
        Transform the GET redirect request created by handle_js_redirect()
        to a POST request using GET parameters as data
        """
        request.method = "POST"
        params = dict(request.url.params)
        # Remove parameters from url
        request.url = request.url.copy_with(params=None)
        # Load params in body request
        headers, request.stream = httpx._content.encode_request(data=params)
        request._prepare(headers)

    async def _handle_login_request(self, response):
        """
        This function handles the requests of the IDP to auth again.

        We'll create a GET redirect that will be transformed
        to a POST request by catch_forced_redirect()
        """
        # Server is asking us to login again
        if response.url == urls.loginpage:
            params = {
                "j_username": self.username,
                "j_password": self.password,
            }
            url = httpx.URL(urls.login, params=params)
            self._redirect_to(response, str(url))

    async def _handle_exipiring_password(self, response):
        """
        Handle expiring password alert after login
        """
        if response.url.path == "/Chpass/chpassservlet/main.htm":
            logging.info("Password is expiring soon")
            params = {
                "j_username": self.username,
                "j_password": self.password,
                "p_username": self.username,
                "p_locale": "it",
                "j_bypassScad": "S",
            }
            url = httpx.URL(urls.login, params=params)
            self._redirect_to(response, str(url))

    async def _handle_sso_request(self, response):
        """
        Handle Single Server Auth request that the PoliTo servers love so much
        """
        if response.is_success:
            if response.url.path == "/idp/profile/SAML2/Redirect/SSO":
                logging.info("SSO AUTH REQUEST")
                # Extract SSO params
                content = await response.aread()
                page = bs4.BeautifulSoup(content, "html.parser")

                form = page.find("form")
                params = {
                    i["name"]: i["value"]
                    for i in form.find_all("input") if "name" in i.attrs
                }

                url = httpx.URL(form["action"], params=params)

                # We pass the POST parameters as URL parameters
                # This will be converted to a stream in a request event hook
                self._redirect_to(response, str(url))

    @staticmethod
    async def _check_auth(response):
        """
        Check that auth request is successful
        If it's not, clean credentials.
        """
        if not response.is_redirect and response.url == urls.login:
            page = bs4.BeautifulSoup(await response.aread(), "html.parser")
            error = page.find("span", {"id": "loginerror"}).text
            logging.error(error)
            raise LoginError(error)

    @staticmethod
    async def _debugging_logger(response):
        logging.debug(
            "%s %s %s\n%s",
            response.url, response.status_code, response.history,
            response.headers
        )


class LoginError(Exception):
    pass
