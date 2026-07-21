from django.conf import settings


ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID = getattr(settings, "ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID", None)

# In-process retries when a tracking webhook arrives before post_send has committed
# the MailDelivery row. Total wait with defaults: 0.2 + 0.5 + 1.0 = 1.7s.
ANYMAIL_STATUS_TRACKER_TRACKING_RETRY_DELAYS = getattr(
    settings,
    "ANYMAIL_STATUS_TRACKER_TRACKING_RETRY_DELAYS",
    (0.2, 0.5, 1.0),
)
