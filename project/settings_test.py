"""Isolated settings for the Django test suite (never use for deployment)."""

import os
from pathlib import Path

os.environ['DEBUG'] = 'True'
os.environ['SECRET_KEY'] = 'test-only-django-secret-key-never-use-in-production'
os.environ['FIELD_ENCRYPTION_KEY'] = 'test-only-field-encryption-placeholder'
os.environ['SALT_KEY'] = 'test-only-salt-key-for-encrypted-fields'
os.environ['DB_ENGINE'] = 'sqlite'
os.environ['ALLOWED_HOSTS'] = 'testserver,localhost'

from .settings import *  # noqa: E402,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'biodata-app-test-cache',
    },
}

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PRIVATE_MEDIA_ROOT = Path(BASE_DIR) / '.test-private-media'
RATELIMIT_ENABLE = False
