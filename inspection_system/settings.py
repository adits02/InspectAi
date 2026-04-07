from pathlib import Path
import cloudinary

# --------------------------------------------------
# BASE DIRECTORY
# --------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# --------------------------------------------------
# SECURITY
# --------------------------------------------------
SECRET_KEY = 'django-insecure-cdsx^-z5*#2i=op+hw523i=ka9=(-w^e!=n&i!xsq$urd4p(1g'
DEBUG = True
ALLOWED_HOSTS = []

# --------------------------------------------------
# DJANGO DATABASE (REQUIRED FOR MIGRATIONS)
# --------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# --------------------------------------------------
# MONGODB CONFIG (REQUIRED BY django-mongoengine)
# --------------------------------------------------
MONGODB_DATABASES = {
    'default': {
        'host': 'mongodb+srv://neolearn02_db_user:3phXJLGvCqwHxtWH@a13.drvtvwx.mongodb.net/a13?retryWrites=true&w=majority',
        'name': 'a13',
        'tls': True,
        'tlsAllowInvalidCertificates': True  # only for local testing
    }
}

# --------------------------------------------------
# CLOUDINARY
# --------------------------------------------------
cloudinary.config(
    cloud_name="dy2sdcfxy",
    api_key="492576988599259",
    api_secret="bFHhLUnSDPqFAztC0520NFWk94U",
)

# --------------------------------------------------
# APPLICATIONS
# --------------------------------------------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'django_mongoengine',
    'django_mongoengine.sessions',
    'django_mongoengine.mongo_auth',

    'cloudinary',
    'cloudinary_storage',

    'core',
    'institute',
    'inspector',
    'aicte_admin',
]

# --------------------------------------------------
# MIDDLEWARE
# --------------------------------------------------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# --------------------------------------------------
# URLS & TEMPLATES
# --------------------------------------------------
ROOT_URLCONF = 'inspection_system.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.template.context_processors.media',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'inspection_system.wsgi.application'

# --------------------------------------------------
# AUTHENTICATION & SESSIONS (MongoDB)
# --------------------------------------------------
AUTHENTICATION_BACKENDS = (
    'django_mongoengine.mongo_auth.backends.MongoEngineBackend',
    'django.contrib.auth.backends.ModelBackend',
)

SESSION_ENGINE = 'django_mongoengine.sessions'

# --------------------------------------------------
# PASSWORD VALIDATION
# --------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# --------------------------------------------------
# INTERNATIONALIZATION
# --------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# --------------------------------------------------
# STATIC & MEDIA
# --------------------------------------------------
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# --------------------------------------------------
# DEFAULTS
# --------------------------------------------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'options'
LOGIN_REDIRECT_URL = ''
LOGOUT_REDIRECT_URL = 'homepage'
