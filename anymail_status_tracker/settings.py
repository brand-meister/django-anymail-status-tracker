from django.conf import settings


ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID = getattr(settings, "ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID", 1)
