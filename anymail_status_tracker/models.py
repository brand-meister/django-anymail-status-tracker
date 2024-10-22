
from django.db import models


class MailDelivery(models.Model):

    # Not all ESPs will have all of these states
    STATE_QUEUED = 'queued'
    STATE_SENT = 'sent'
    STATE_REJECTED = 'rejected'
    STATE_FAILED = 'failed'
    STATE_BOUNCED = 'bounced'
    STATE_DEFERRED = 'deferred'
    STATE_DELIVERED = 'delivered'
    STATE_AUTORESPONDED = 'autoresponded'
    STATE_OPENED = 'opened'
    STATE_CLICKED = 'clicked'
    STATE_COMPLAINED = 'complained'
    STATE_UNSUBSCRIBED = 'unsubscribed'
    STATE_SUBSCRIBED = 'subscribed'
    STATE_UNKNOWN = 'unknown'

    DELIVERY_STATES = (
        (STATE_QUEUED, 'Queued'),
        (STATE_SENT, 'Sent'),
        (STATE_REJECTED, 'Rejected'),
        (STATE_FAILED, 'Failed'),
        (STATE_BOUNCED, 'Bounced'),
        (STATE_DEFERRED, 'Deferred'),
        (STATE_DELIVERED, 'Delivered'),
        (STATE_AUTORESPONDED, 'Autoresponded'),
        (STATE_OPENED, 'Opened'),
        (STATE_CLICKED, 'Clicked'),
        (STATE_COMPLAINED, 'Complained'),
        (STATE_UNSUBSCRIBED, 'Unsubscribed'),
        (STATE_SUBSCRIBED, 'Subscribed'),
        (STATE_UNKNOWN, 'Unknown'),
    )

    REJECT_REASON_INVALID = 'invalid'
    REJECT_REASON_BOUNCED = 'bounced'
    REJECT_REASON_TIMED_OUT = 'timed_out'
    REJECT_REASON_BLOCKED = 'blocked'
    REJECT_REASON_SPAM = 'spam'
    REJECT_REASON_REJECTED = 'rejected'
    REJECT_REASON_UNSUBSCRIBED = 'unsubscribed'
    REJECT_REASON_OTHER = 'other'

    REJECT_REASONS = (
        (REJECT_REASON_INVALID, 'Invalid'),
        (REJECT_REASON_BOUNCED, 'Bounced'),
        (REJECT_REASON_TIMED_OUT, 'Timed Out'),
        (REJECT_REASON_BLOCKED, 'Blocked'),
        (REJECT_REASON_SPAM, 'Spam'),
        (REJECT_REASON_REJECTED, 'Rejected'),
        (REJECT_REASON_UNSUBSCRIBED, 'Unsubscribed'),
        (REJECT_REASON_OTHER, 'Other'),
    )

    esp_name = models.CharField(max_length=64, help_text='Name of the ESP')
    state = models.CharField(max_length=32, choices=DELIVERY_STATES)
    message_id = models.CharField(max_length=255)

    timestamp = models.DateTimeField(null=True, blank=True, help_text='Timestamp of webhook notification')
    recipient = models.EmailField()
    metadata = models.JSONField(default=dict, blank=True)
    reject_reason = models.CharField(max_length=32, choices=REJECT_REASONS, null=True, blank=True)
    description = models.CharField(max_length=255, null=True, blank=True)
    mta_response = models.TextField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, null=True, blank=True)
    click_url = models.URLField(null=True, blank=True)
    esp_event = models.JSONField(default=dict, blank=True)

    sent_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Mail Delivery'
        verbose_name_plural = 'Mail Deliveries'

    def __str__(self):
        return f'{self.recipient} ({self.get_state_display()} {self.updated_at})'
