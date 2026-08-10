"""
WSGI config for backend project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os
import logging
import logging.config
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

# Initialize Django and apply logging manually for Waitress
try:
    import django
    django.setup()
    from django.conf import settings
    logging.config.dictConfig(settings.LOGGING)
    logging.getLogger(__name__).info("✅ Django logging initialized for Waitress.")
except Exception as e:
    print(f"⚠️ Logging initialization failed: {e}")

application = get_wsgi_application()
