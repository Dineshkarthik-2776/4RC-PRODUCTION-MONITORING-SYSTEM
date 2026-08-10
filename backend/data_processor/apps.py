from django.apps import AppConfig

class DataProcessorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'data_processor'

    def ready(self):
        # This line is essential. It imports and registers your signals.
        import data_processor.signals