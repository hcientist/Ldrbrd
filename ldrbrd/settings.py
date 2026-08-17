"""Django settings for Ldrbrd.

Configuration comes from the environment (optionally via a .env file next to
manage.py).  See .env.example for the full list.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=()):
    value = os.environ.get(name)
    if value is None or value == "":
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1", "[::1]"])

# Public base URL of this Ldrbrd deployment.  Used to build the Canvas OIDC
# discovery document URL and the course join links handed out to students.
LDRBRD_BASE_URL = env("LDRBRD_BASE_URL", "http://localhost:8000").rstrip("/")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [LDRBRD_BASE_URL])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
    "accounts",
    "courses",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

# WhiteNoise ships in requirements-docker.txt, not requirements.txt: the dev
# server serves static files itself, but a DEBUG=false container needs someone
# to hand out the admin's CSS.  Wire it up only when it is actually installed
# so a bare local venv still boots.
try:
    import whitenoise  # noqa: F401
except ImportError:
    WHITENOISE_ENABLED = False
else:
    WHITENOISE_ENABLED = True
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

ROOT_URLCONF = "ldrbrd.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "ldrbrd.wsgi.application"
ASGI_APPLICATION = "ldrbrd.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": env("DJANGO_DB_PATH", BASE_DIR / "db.sqlite3"),
        "OPTIONS": {
            # Every public read bumps a usage counter for /top, so reads land on
            # the write path.  WAL lets readers and one writer coexist, and the
            # busy timeout stops concurrent gunicorn workers from failing fast
            # on a lock instead of waiting their turn.
            "timeout": int(env("DJANGO_DB_TIMEOUT", 20)),
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            "transaction_mode": "IMMEDIATE",
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SITE_ID = 1

# --------------------------------------------------------------------------
# Authentication -- Canvas (OIDC) only
# --------------------------------------------------------------------------

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# Canvas is the only way in.  No local username/password signup, no email
# verification flow -- accounts are created on first successful Canvas login.
SOCIALACCOUNT_ONLY = True
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_EMAIL_REQUIRED = False
SOCIALACCOUNT_STORE_TOKENS = env_bool("CANVAS_STORE_TOKENS", False)
SOCIALACCOUNT_ADAPTER = "accounts.adapters.CanvasSocialAccountAdapter"
ACCOUNT_ADAPTER = "accounts.adapters.LdrbrdAccountAdapter"
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_LOGIN_METHODS = {"username"}
ACCOUNT_SIGNUP_FIELDS = ["username*"]

# --------------------------------------------------------------------------
# Canvas OIDC wiring
# --------------------------------------------------------------------------
#
# Canvas does not publish an OpenID Connect discovery document -- only
# /login/oauth2/jwks returns JSON.  Ldrbrd therefore serves its own discovery
# shim at /.well-known/canvas-openid-configuration, built from CANVAS_BASE_URL
# (see accounts/views.py).
#
# We depend on the hcientist fork of django-allauth (branch
# overridable_oidc_conf) for two things the upstream OIDC provider lacks:
#
#   * "uid_field"     -- Canvas userinfo returns "id", not the OIDC-standard
#                        "sub", so the account ID field has to be selectable.
#   * "openid_config" -- per-app overrides merged over the fetched discovery
#                        document, so an operator can repoint any single
#                        endpoint (at a proxy, a staging Canvas, ...) without
#                        redeploying or editing the shim.

CANVAS_BASE_URL = env("CANVAS_BASE_URL", "https://canvas.instructure.com").rstrip("/")
CANVAS_PROVIDER_ID = env("CANVAS_PROVIDER_ID", "canvas")
CANVAS_PROVIDER_NAME = env("CANVAS_PROVIDER_NAME", "Canvas")

# Where the OIDC adapter fetches the discovery document from.  Defaults to this
# deployment's own shim.  Behind a proxy/container where LDRBRD_BASE_URL is not
# reachable from inside the app, point this at the loopback address instead.
CANVAS_OIDC_DISCOVERY_URL = env(
    "CANVAS_OIDC_DISCOVERY_URL",
    f"{LDRBRD_BASE_URL}/.well-known/canvas-openid-configuration",
)

# The fork's escape hatch: a JSON object merged *over* the discovery document.
# e.g. CANVAS_OPENID_CONFIG_OVERRIDES='{"token_endpoint":"https://proxy/token"}'
try:
    CANVAS_OPENID_CONFIG_OVERRIDES = json.loads(
        env("CANVAS_OPENID_CONFIG_OVERRIDES", "{}")
    )
except json.JSONDecodeError as exc:  # pragma: no cover - configuration error
    raise ValueError(
        f"CANVAS_OPENID_CONFIG_OVERRIDES is not valid JSON: {exc}"
    ) from exc

# Canvas developer keys that do not enforce scopes want an empty scope list.
# If yours enforces scopes, it needs at least: url:GET|/api/v1/users/:id
CANVAS_OAUTH_SCOPE = [s for s in env("CANVAS_OAUTH_SCOPE", "").split() if s]

SOCIALACCOUNT_PROVIDERS = {
    "openid_connect": {
        "OAUTH_PKCE_ENABLED": env_bool("CANVAS_OAUTH_PKCE", False),
        "SCOPE": CANVAS_OAUTH_SCOPE,
        "APPS": [
            {
                "provider_id": CANVAS_PROVIDER_ID,
                "name": CANVAS_PROVIDER_NAME,
                "client_id": env("CANVAS_CLIENT_ID", ""),
                "secret": env("CANVAS_CLIENT_SECRET", ""),
                "settings": {
                    "server_url": CANVAS_OIDC_DISCOVERY_URL,
                    # Canvas issues no id_token, so the userinfo endpoint is
                    # the only source of identity.
                    "fetch_userinfo": True,
                    # Canvas expects credentials in the POST body.
                    "token_auth_method": "client_secret_post",
                    # Fork feature: Canvas userinfo has "id", not "sub".
                    "uid_field": "id",
                    # Fork feature: merged over the discovery document.
                    "openid_config": CANVAS_OPENID_CONFIG_OVERRIDES,
                },
            }
        ],
    }
}

# --------------------------------------------------------------------------
# Ldrbrd behaviour
# --------------------------------------------------------------------------

# Maximum size, in bytes, of a single JSON document an app may store.
LDRBRD_MAX_PAYLOAD_BYTES = int(env("LDRBRD_MAX_PAYLOAD_BYTES", 1024 * 1024))

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
