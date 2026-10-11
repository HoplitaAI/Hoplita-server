
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _env_bool(name, default=False):
    v = _env(name)
    if not v:
        return default
    return v.lower() in ("1", "true", "yes", "on", "si", "sí")


def _env_list(name, default=()):
    v = _env(name)
    return [x.strip() for x in v.split(",") if x.strip()] if v else list(default)


def _env_int(name, default):
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


DEBUG = _env_bool("HOPLITA_DEBUG", False)

SECRET_KEY = _env("HOPLITA_SECRET_KEY") or (
    "django-insecure-DEV-ONLY-do-not-use-outside-development" if DEBUG else "")

DATA_UPLOAD_MAX_MEMORY_SIZE = _env_int("HOPLITA_MAX_BODY_BYTES",
                                       32 * 1024 * 1024)

ALLOWED_HOSTS = _env_list("HOPLITA_ALLOWED_HOSTS",
                          ["localhost", "127.0.0.1"] if DEBUG else [])
CSRF_TRUSTED_ORIGINS = _env_list("HOPLITA_CSRF_ORIGINS")

if _env_bool("HOPLITA_BEHIND_PROXY", True):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    SECURE_HSTS_SECONDS = _env_int("HOPLITA_HSTS_SECONDS", 0)



INSTALLED_APPS = [
    'daphne',
    'channels',
    'rest_framework',
    'rest_framework.authtoken',
    'federation',
    'unfold',
    'unfold.contrib.filters',
    'unfold.contrib.forms',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_otp',
    'django_otp.plugins.otp_totp',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_otp.middleware.OTPMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'hoplitafed.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'hoplitafed.wsgi.application'
ASGI_APPLICATION = 'hoplitafed.asgi.application'

REDIS_URL = _env("HOPLITA_REDIS_URL")
_POSTGRES = bool(_env("HOPLITA_DB_NAME"))

if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [{"address": REDIS_URL,
                           "socket_connect_timeout": 0.5,
                           "socket_timeout": 8.0,
                           "socket_keepalive": True,
                           "health_check_interval": 15}],
                "capacity": _env_int("HOPLITA_CHANNEL_CAPACITY", 1500),
                "expiry": 30,
            },
        },
    }
elif _POSTGRES:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "federation.pg_layer.PgChannelLayer",
            "CONFIG": {
                "capacity": _env_int("HOPLITA_CHANNEL_CAPACITY", 1500),
                "expiry": 30,
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    }

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "socket_connect_timeout": 0.5,
                "socket_timeout": 0.5,
            },
        },
    }
elif _POSTGRES:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "hoplita_cache",
        },
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "hoplita-fed",
        },
    }



if _env("HOPLITA_DB_NAME"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _env("HOPLITA_DB_NAME"),
            "USER": _env("HOPLITA_DB_USER", "hoplita"),
            "PASSWORD": _env("HOPLITA_DB_PASSWORD"),
            "HOST": _env("HOPLITA_DB_HOST", "127.0.0.1"),
            "PORT": _env("HOPLITA_DB_PORT", "5432"),
            "CONN_MAX_AGE": _env_int("HOPLITA_DB_CONN_MAX_AGE", 0),
            "CONN_HEALTH_CHECKS": True,
        }
    }
    if _env_bool("HOPLITA_DB_POOL", False):
        DATABASES["default"].setdefault("OPTIONS", {})["pool"] = {
            "min_size": _env_int("HOPLITA_DB_POOL_MIN", 1),
            "max_size": _env_int("HOPLITA_DB_POOL_MAX", 8),
        }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
                "timeout": 20,
            },
        }
    }



AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]



LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}


REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "federation.throttles.TierRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"tier": "100000/min"},
}

if _env("HOPLITA_EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = _env("HOPLITA_EMAIL_HOST")
    EMAIL_PORT = _env_int("HOPLITA_EMAIL_PORT", 25)
    EMAIL_USE_TLS = _env_bool("HOPLITA_EMAIL_USE_TLS", False)
    EMAIL_HOST_USER = _env("HOPLITA_EMAIL_USER")
    EMAIL_HOST_PASSWORD = _env("HOPLITA_EMAIL_PASSWORD")
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = _env("HOPLITA_EMAIL_FROM", "no-reply@localhost")

FED_OPEN_REGISTRATION = _env_bool("HOPLITA_OPEN_REGISTRATION", False)

FED_EXPOSE_DEV_PIN = _env_bool("HOPLITA_EXPOSE_DEV_PIN", DEBUG)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": _env("HOPLITA_LOG_LEVEL", "INFO")},
}


UNFOLD = {
    "SITE_TITLE": "HOPLITA FED",
    "SITE_HEADER": "HOPLITA · federation",
    "SITE_SYMBOL": "hub",
    "SHOW_HISTORY": True,
    "COLORS": {
        "primary": {
            "50": "236 253 245", "100": "209 250 229", "200": "167 243 208",
            "300": "110 231 183", "400": "52 211 153", "500": "16 185 129",
            "600": "5 150 105", "700": "4 120 87", "800": "6 95 70",
            "900": "6 78 59", "950": "2 44 34",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "navigation": [
            {
                "title": "Operation",
                "items": [
                    {"title": "Statistics", "icon": "monitoring",
                     "link": "/admin/stats/"},
                    {"title": "MOTD and notices", "icon": "campaign",
                     "link": "/admin/federation/servernotice/"},
                ],
            },
            {
                "title": "Users and quotas",
                "items": [
                    {"title": "Users (editable quota)", "icon": "person",
                     "link": "/admin/federation/identity/"},
                    {"title": "Quota groups", "icon": "inventory",
                     "link": "/admin/federation/quotatier/"},
                    {"title": "Django accounts", "icon": "manage_accounts",
                     "link": "/admin/auth/user/"},
                ],
            },
            {
                "title": "Federation",
                "items": [
                    {"title": "Contacts", "icon": "group",
                     "link": "/admin/federation/contact/"},
                    {"title": "Envelopes", "icon": "mail",
                     "link": "/admin/federation/envelope/"},
                    {"title": "Public folders", "icon": "folder_shared",
                     "link": "/admin/federation/publicfolder/"},
                    {"title": "Banned emails", "icon": "block",
                     "link": "/admin/federation/bannedemail/"},
                ],
            },
        ],
    },
}
