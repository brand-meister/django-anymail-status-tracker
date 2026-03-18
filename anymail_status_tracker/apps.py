from django.apps import AppConfig


class AnymailStatusTrackerConfig(AppConfig):
    name = "anymail_status_tracker"
    verbose_name = "Anymail Status Tracker"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # NOQA
