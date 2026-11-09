"""
Django settings untuk project biodata-app.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


# ============ KEAMANAN ============
DEBUG = os.getenv('DEBUG', 'True').strip().lower() in {'1', 'true', 'yes'}
SECRET_KEY = os.getenv('SECRET_KEY', '').strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'local-only-insecure-key-change-before-deploy'
    else:
        raise RuntimeError('SECRET_KEY must be set when DEBUG=False.')
ALLOWED_HOSTS = [host.strip() for host in os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',') if host.strip()]
if not DEBUG and (not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS):
    raise RuntimeError('Set explicit ALLOWED_HOSTS when DEBUG=False.')

# === Enkripsi field ===
FIELD_ENCRYPTION_KEY = os.getenv('FIELD_ENCRYPTION_KEY', '')
SALT_KEY = os.getenv('SALT_KEY', '')

if not FIELD_ENCRYPTION_KEY:
    if not DEBUG:
        raise RuntimeError('FIELD_ENCRYPTION_KEY must be set in production.')
    import warnings
    warnings.warn('FIELD_ENCRYPTION_KEY kosong! Field terenkripsi tidak akan berfungsi.',
                  RuntimeWarning)
if not SALT_KEY:
    if not DEBUG:
        raise RuntimeError('SALT_KEY must be set in production.')
    import warnings
    warnings.warn('SALT_KEY kosong! django-fernet-encrypted-fields akan crash saat save. '
                  'Tambahkan SALT_KEY=<64 hex chars> ke file .env', RuntimeWarning)


# ============ APPS ============
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'widget_tweaks',
    'peserta',
]

AUTH_USER_MODEL = 'peserta.Admin'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'peserta.context_processors.notifikasi_count',  # ← cukup di sini
            ],
        },
    },
]

WSGI_APPLICATION = 'project.wsgi.application'


# ============ DATABASE ============
DB_ENGINE = os.getenv('DB_ENGINE', 'sqlite').strip().lower()
if not DEBUG and DB_ENGINE not in {'postgres', 'postgresql'}:
    raise RuntimeError('Production requires DB_ENGINE=postgres; SQLite is for local development only.')
if DB_ENGINE in {'postgres', 'postgresql'}:
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', ''),
        'USER': os.getenv('DB_USER', ''),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '60')),
        'OPTIONS': {'sslmode': os.getenv('DB_SSLMODE', 'prefer' if DEBUG else 'require')},
    }}
    if not DATABASES['default']['NAME'] or not DATABASES['default']['USER']:
        raise RuntimeError('DB_NAME and DB_USER are required for PostgreSQL.')
elif DB_ENGINE in {'sqlite', 'sqlite3'}:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'OPTIONS': {
                'timeout': 20,
                'transaction_mode': 'IMMEDIATE',
            },
        },
    }
else:
    raise RuntimeError(f'Unsupported DB_ENGINE: {DB_ENGINE}')


# ============ PASSWORD VALIDATORS ============
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ============ I18N ============
LANGUAGE_CODE = 'id-id'
TIME_ZONE = 'Asia/Jakarta'
USE_I18N = True
USE_TZ = True


# ============ STATIC & MEDIA ============
STATIC_URL = '/static/'    # ← pakai leading slash (lebih aman)
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
PRIVATE_MEDIA_ROOT = Path(os.getenv('PRIVATE_MEDIA_ROOT', BASE_DIR / 'private_media')).resolve()
if PRIVATE_MEDIA_ROOT == MEDIA_ROOT.resolve() or MEDIA_ROOT.resolve() in PRIVATE_MEDIA_ROOT.parents:
    raise RuntimeError('PRIVATE_MEDIA_ROOT must be outside MEDIA_ROOT and its public serving tree.')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ============ EMAIL (fix: EMAIL_BACKEND, bukan MAILERS) ============
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
DEFAULT_FROM_EMAIL = 'noreply@biodata-app.local'


# ============ SESSION & CSRF ============
SESSION_COOKIE_AGE = 1800                    # 30 menit idle timeout
# SESSION_SAVE_EVERY_REQUEST = True          # ❌ DIHAPUS — hanya simpan saat ada perubahan
SESSION_EXPIRE_AT_BROWSER_CLOSE = True       # logout saat browser ditutup
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False                 # perlu untuk AJAX
X_FRAME_OPTIONS = 'DENY'
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if origin.strip()]
SECURE_SSL_REDIRECT = not DEBUG and os.getenv('SECURE_SSL_REDIRECT', 'True').lower() in {'1', 'true', 'yes'}
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '31536000' if not DEBUG else '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG and os.getenv('SECURE_HSTS_INCLUDE_SUBDOMAINS', 'False').lower() in {'1', 'true', 'yes'}
SECURE_HSTS_PRELOAD = not DEBUG and os.getenv('SECURE_HSTS_PRELOAD', 'False').lower() in {'1', 'true', 'yes'}
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
if os.getenv('USE_X_FORWARDED_HOST', 'False').lower() in {'1', 'true', 'yes'}:
    USE_X_FORWARDED_HOST = True
if os.getenv('SECURE_PROXY_SSL_HEADER', 'False').lower() in {'1', 'true', 'yes'}:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# ============ FILE UPLOAD ============
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_PERMISSIONS = 0o640   # file upload hanya bisa dibaca owner+group


# ============ LOGIN ============
LOGIN_URL = '/panel/login/'                           # ← custom admin login
LOGIN_REDIRECT_URL = '/panel/'                        # setelah login admin
LOGOUT_REDIRECT_URL = '/panel/login/'



# ============ DJANGO-AXES (rate limit login admin) ============
# Opsional. Kalau belum install django-axes, comment dulu.
# Install: pip install django-axes
#
# INSTALLED_APPS += ['axes']
# MIDDLEWARE += ['axes.middleware.AxesMiddleware']
# AUTHENTICATION_BACKENDS = [
#     'axes.backends.AxesStandaloneBackend',
#     'django.contrib.auth.backends.ModelBackend',
# ]
# AXES_FAILURE_LIMIT = 5
# AXES_COOLOFF_TIME = 1


# ============ LOGGING ============
# project/settings.py
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'filters': {
        # Filter untuk abaikan request Chrome DevTools
        'skip_chrome_devtools': {
            '()': 'django.utils.log.CallbackFilter',
            'callback': lambda record: (
                'com.chrome.devtools.json' not in record.getMessage()
            ),
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'filters': ['skip_chrome_devtools'],
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
            'filters': ['skip_chrome_devtools'],
        },
    },
}


# ============ CACHES (FILE-BASED) ============
IS_WINDOWS = os.name == 'nt'

if IS_WINDOWS:
    # Untuk Development di Windows lokal
    CACHE_LOCATION = os.path.join(BASE_DIR, 'tmp_cache')
    os.makedirs(CACHE_LOCATION, exist_ok=True)  # Buat folder jika belum ada
else:
    # Untuk Production di Linux (PythonAnywhere)
    CACHE_LOCATION = os.getenv('CACHE_LOCATION', '/tmp/django_cache')

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': CACHE_LOCATION,
        'TIMEOUT': 300,  # 5 menit
        'OPTIONS': {
            'MAX_ENTRIES': 500,
            'CULL_FREQUENCY': 3,
        }
    }
}
