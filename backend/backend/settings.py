from pathlib import Path
import os
from rest_framework.authentication import TokenAuthentication

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file when python-dotenv is installed.
if load_dotenv is not None:
    load_dotenv(BASE_DIR / '.env')

# ============================================================
# CORE SETTINGS
# ============================================================
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-fallback-only-for-dev')
DEBUG = os.environ.get('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = ['*']
LOGIN_URL = '/api/auth/login/'

CORS_ORIGIN_ALLOW_ALL = True

# ============================================================
# APPLICATIONS
# ============================================================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',
    'user_authentication',
    'data_processor.apps.DataProcessorConfig',
    'corsheaders',
    'reports',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',  # put CORS before CommonMiddleware
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'backend.urls'

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

WSGI_APPLICATION = 'backend.wsgi.application'

# ============================================================
# DATABASE
# ============================================================

# PRODUCTION DATABASE
DATABASES = {
    'default': {
        'ENGINE': 'mssql',  # Requires mssql-django package
        'NAME': 'Dashboard',
        'USER': 'django_user',
        'PASSWORD': '4rc@jkti',
        'HOST': '192.168.230.101',  
        'PORT': '1433',
        'OPTIONS': {
            'driver': 'ODBC Driver 17 for SQL Server',
        },
    }
}


# LOCAL DATABASE
# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.mysql',
#         'NAME': 'dashboard',
#         'USER': 'root',
#         'PASSWORD': '0907',
#         'HOST': 'localhost',
#         'PORT': '3306',
#     }
# }

# dev-dk 
# DK local DB
# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.mysql',
#         'NAME': 'dashboard',
#         'USER': 'dinesh',
#         'PASSWORD': '2776',
#         'HOST': 'localhost',
#         'PORT': '3306',
#     }
# }


# ============================================================
# AUTH / REST FRAMEWORK
# ============================================================
AUTH_USER_MODEL = 'user_authentication.User'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
}

# ============================================================
# INTERNATIONALIZATION
# ============================================================
LANGUAGE_CODE = 'en-us'
# frontend
TIME_ZONE = 'Asia/Kolkata'
# DB
USE_I18N = True
# Use local naive datetimes everywhere (no UTC storage)
USE_TZ = False

# ============================================================
# STATIC FILES
# ============================================================
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# ============================================================
# CELERY CONFIG
# ============================================================
CELERY_BROKER_URL = 'redis://127.0.0.1:6379/0'
CELERY_RESULT_BACKEND = 'redis://127.0.0.1:6379/0'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Kolkata'

if os.name == 'nt':
    # Celery's prefork pool is not reliable on Windows; use solo so tracing
    # and worker startup stay stable on this platform.
    CELERY_WORKER_POOL = 'solo'
    CELERY_WORKER_CONCURRENCY = 1

from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'process-raw-data-every-5-minutes': {
        'task': 'process_changeover_data',
        'schedule': 300.0,
        'args': [False],
    },
    'sync-recipe-master-daily-at-7am': {
        'task': 'sync_recipe_master_from_bom',
        'schedule': crontab(hour=1, minute=30),
    },
}

# ============================================================
# LOGGING CONFIGURATION
# ============================================================
LOG_DIR = Path(os.environ.get('DJANGO_LOG_DIR', BASE_DIR / 'logs'))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE_PATH = LOG_DIR / 'django_backend.log'


def _can_write_log_file(path: Path) -> bool:
    try:
        with open(path, 'a', encoding='utf-8'):
            pass
        return True
    except OSError:
        return False


ENABLE_FILE_LOGGING = _can_write_log_file(LOG_FILE_PATH)
LOGGER_HANDLERS = ['console'] + (['file'] if ENABLE_FILE_LOGGING else [])

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} [{levelname}] {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': LOGGER_HANDLERS,
            'level': 'INFO',
            'propagate': True,
        },
        'django.server': {
            'handlers': LOGGER_HANDLERS,
            'level': 'INFO',
            'propagate': False,
        },
    },
}

if ENABLE_FILE_LOGGING:
    LOGGING['handlers']['file'] = {
        'level': 'INFO',
        'class': 'logging.FileHandler',
        'filename': str(LOG_FILE_PATH),
        'formatter': 'verbose',
    }