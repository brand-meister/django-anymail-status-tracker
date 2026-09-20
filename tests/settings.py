SECRET_KEY = "test-secret-key"
DEBUG = False
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "anymail_status_tracker",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "tests.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Anymail's test backend fires pre_send/post_send like a real ESP backend,
# assigns per-recipient AnymailRecipientStatus and never talks to the network.
# (Django's test runner overrides EMAIL_BACKEND with locmem; tests that need
# the Anymail backend set it explicitly via the `settings` fixture.)
EMAIL_BACKEND = "anymail.backends.test.EmailBackend"

ANYMAIL = {
    "WEBHOOK_SECRET": "user:pass",
}

USE_TZ = True
TIME_ZONE = "UTC"
