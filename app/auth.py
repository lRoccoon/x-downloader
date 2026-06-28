from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from . import config

_signer = TimestampSigner(config.SECRET_KEY)
COOKIE_NAME = "x_dl_session"


def auth_enabled() -> bool:
    return bool(config.APP_PASSWORD)


def make_token() -> str:
    return _signer.sign(b"authenticated").decode()


def verify_token(token: str | None) -> bool:
    if not auth_enabled():
        return True
    if not token:
        return False
    try:
        _signer.unsign(token, max_age=config.SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def check_password(password: str) -> bool:
    return auth_enabled() and password == config.APP_PASSWORD
