import httpx


class BaseURL(httpx.URL):
    """
    A pathlib-like version of httpx.URL
    """

    def __truediv__(self, url):
        return BaseURL(self.join(url))


IDP = BaseURL("https://idp.polito.it/")
did = BaseURL("https://didattica.polito.it/")

loginpage = IDP/"idp/x509mixed-login"
login = IDP/"idp/Authn/X509Mixed/UserPasswordLogin"
