import uuid

from django.db import models
from django.db.models import Case, IntegerField, OuterRef, Value, When
from django.db.models.functions import Coalesce

from anymail_status_tracker.models.mail_delivery import MailDelivery


# Deterministic ids for the baseline event written by post_send. Prefixed so
# ranked() can demote them regardless of the status the ESP returned on send
# (queued, sent, failed, ...). SNS MessageIds are bare UUIDs and never match.
POST_SEND_EVENT_ID_PREFIX = "post_send:"
POST_SEND_EVENT_NAMESPACE = uuid.UUID("6f1b2a0e-3c4d-4e5f-8a9b-0c1d2e3f4a5b")


def post_send_event_id(esp_name: str, message_id: str, recipient: str) -> str:
    """Stable event_id for the post_send baseline of one delivery."""
    return POST_SEND_EVENT_ID_PREFIX + str(
        uuid.uuid5(POST_SEND_EVENT_NAMESPACE, "|".join((esp_name, message_id, recipient)))
    )


class MailDeliveryEventQuerySet(models.QuerySet):
    def for_key(self, esp_name, message_id, recipient):
        return self.filter(esp_name=esp_name, message_id=message_id, recipient=recipient)

    def for_delivery(self, delivery: MailDelivery):
        return self.for_key(delivery.esp_name, delivery.message_id, delivery.recipient)

    def with_effective_timestamp(self):
        """Annotate ``_effective``: the ESP timestamp, or when we received the event if the ESP gave none."""
        return self.annotate(_effective=Coalesce("timestamp", "received_at"))

    def chronological(self):
        return self.with_effective_timestamp().order_by("_effective", "received_at", "pk")

    def ranked(self):
        """
        Order so that the first event is the one defining the delivery's state:

        1. terminal negative events (bounced, complained, rejected, failed) first,
        2. then any ESP tracking event before the post_send baseline,
        3. then latest effective timestamp first,
        4. then latest received / highest pk (deterministic tie-break).
        """
        return (
            self.with_effective_timestamp()
            .annotate(
                _terminal=Case(
                    When(event_type__in=MailDelivery.TERMINAL_NEGATIVE_STATES, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                ),
                _not_baseline=Case(
                    When(event_id__startswith=POST_SEND_EVENT_ID_PREFIX, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
            )
            .order_by("-_terminal", "-_not_baseline", "-_effective", "-received_at", "-pk")
        )

    def orphans(self):
        """Events for which no MailDelivery exists (mail sent by something else)."""
        matching = MailDelivery.objects.filter(
            esp_name=OuterRef("esp_name"),
            message_id=OuterRef("message_id"),
            recipient=OuterRef("recipient"),
        )
        return self.annotate(_has_delivery=models.Exists(matching)).filter(_has_delivery=False)


class MailDeliveryEvent(models.Model):
    """
    Append-only log of everything known about a delivery: the initial status
    returned by the ESP on send (written by post_send) and every tracking
    notification received afterwards.

    Intentionally has no foreign key to MailDelivery: an event may (and in
    practice does) arrive before the corresponding MailDelivery row is
    committed. Events are matched to deliveries via the natural key
    (esp_name, message_id, recipient) instead.
    """

    esp_name = models.CharField(max_length=64)
    message_id = models.CharField(max_length=255)
    recipient = models.EmailField()

    event_id = models.CharField(
        max_length=255,
        help_text="Unique notification id from the ESP (e.g. SNS MessageId); used for idempotency",
    )
    event_type = models.CharField(max_length=32, choices=MailDelivery.DELIVERY_STATES)
    timestamp = models.DateTimeField(null=True, blank=True, help_text="Event timestamp reported by the ESP")
    received_at = models.DateTimeField(auto_now_add=True)

    metadata = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=list, blank=True)
    reject_reason = models.CharField(max_length=32, choices=MailDelivery.REJECT_REASONS, null=True, blank=True)
    description = models.CharField(max_length=255, null=True, blank=True)
    mta_response = models.TextField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, null=True, blank=True)
    click_url = models.URLField(null=True, blank=True)
    esp_event = models.JSONField(default=dict, blank=True)

    objects = MailDeliveryEventQuerySet.as_manager()

    class Meta:
        verbose_name = "Mail Delivery Event"
        verbose_name_plural = "Mail Delivery Events"
        ordering = ("received_at", "pk")
        indexes = [
            models.Index(
                fields=("esp_name", "message_id", "recipient"),
                name="maildeliveryevent_key_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("esp_name", "event_id"),
                name="maildeliveryevent_unique_esp_event_id",
            ),
        ]

    def __str__(self):
        return f"{self.recipient} ({self.get_event_type_display()} {self.effective_timestamp})"

    @property
    def effective_timestamp(self):
        return self.timestamp or self.received_at

    @property
    def is_terminal_negative(self) -> bool:
        return self.event_type in MailDelivery.TERMINAL_NEGATIVE_STATES

    @property
    def subject(self) -> str | None:
        """Subject line from the ESP payload, if the event carried mail headers."""
        return subject_from_esp_event(self.esp_event)


def subject_from_esp_event(esp_event) -> str | None:
    """
    Pull the Subject out of an ESP tracking payload.

    Amazon SES puts it on ``mail.commonHeaders.subject`` and again in
    ``mail.headers`` as ``{"name": "Subject", "value": "..."}``. Other shapes
    with a top-level ``headers`` list are handled the same way.
    """
    if not isinstance(esp_event, dict):
        return None

    mail = esp_event.get("mail")
    if isinstance(mail, dict):
        common = mail.get("commonHeaders")
        if isinstance(common, dict):
            subject = common.get("subject")
            if subject:
                return subject
        subject = _subject_from_headers(mail.get("headers"))
        if subject:
            return subject

    return _subject_from_headers(esp_event.get("headers"))


def _subject_from_headers(headers) -> str | None:
    if not isinstance(headers, list):
        return None
    for header in headers:
        if isinstance(header, dict) and str(header.get("name", "")).lower() == "subject":
            value = header.get("value")
            return value or None
    return None
