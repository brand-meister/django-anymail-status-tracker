from django.conf import settings


ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID = getattr(settings, "ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID", None)

# Log each Anymail tracking event (normalized fields + raw esp_event).
# Useful when shipping logs to an external service (e.g. Sentry) for production debugging.
ANYMAIL_STATUS_TRACKER_LOG_TRACKING_EVENT = getattr(
    settings,
    "ANYMAIL_STATUS_TRACKER_LOG_TRACKING_EVENT",
    False,
)

# In-process retries when a tracking webhook arrives before post_send has committed
# the MailDelivery row. Total wait with defaults: 0.2 + 0.5 + 1.0 = 1.7s.
ANYMAIL_STATUS_TRACKER_TRACKING_RETRY_DELAYS = getattr(
    settings,
    "ANYMAIL_STATUS_TRACKER_TRACKING_RETRY_DELAYS",
    (0.2, 0.5, 1.0),
)
