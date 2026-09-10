import uuid

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import models
from django.db.models import OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.module_loading import import_string

from anymail.backends.base import AnymailBaseBackend


FAKE_ESP_NAME = "Fake"

_UNSET = object()


class MailDeliveryQuerySet(models.QuerySet):
    """
    MailDelivery rows carry no status of their own; everything is derived from
    MailDeliveryEvent. Use ``with_state()`` to annotate the current state in a
    single query (one correlated subquery per row) before rendering lists.
    """

    def _winning_event(self):
        from anymail_status_tracker.models.mail_delivery_event import MailDeliveryEvent

        return MailDeliveryEvent.objects.for_key(
            OuterRef("esp_name"), OuterRef("message_id"), OuterRef("recipient")
        ).ranked()

    def with_state(self):
        """
        Annotate ``state``, ``state_timestamp`` and ``latest_event_id`` with the
        event that currently defines each delivery (see MailDeliveryEventQuerySet.ranked).
        Deliveries without any event get ``state="unknown"``.
        """
        winner = self._winning_event()
        return self.annotate(
            state=Coalesce(
                Subquery(winner.values("event_type")[:1]),
                Value(MailDelivery.STATE_UNKNOWN),
                output_field=models.CharField(),
            ),
            state_timestamp=Subquery(winner.values("_effective")[:1], output_field=models.DateTimeField()),
            latest_event_id=Subquery(winner.values("pk")[:1], output_field=models.BigIntegerField()),
        )

    def for_event(self, event):
        """Deliveries matching an event's natural key (0 or 1 rows)."""
        return self.filter(esp_name=event.esp_name, message_id=event.message_id, recipient=event.recipient)

    def filter_state(self, *states):
        return self.with_state().filter(state__in=states)

    def exclude_state(self, *states):
        return self.with_state().exclude(state__in=states)

    def create_message(
        self,
        message: EmailMessage,
        fail_silently: bool = False,
        fake_delivery: bool = False,
    ):
        from anymail_status_tracker.models.mail_delivery_event import MailDeliveryEvent

        assert isinstance(message, EmailMessage)
        assert message.connection is None or isinstance(message.connection, AnymailBaseBackend)

        if settings.DEBUG and getattr(settings, "ANYMAIL_STATUS_TRACKER_DEBUG_BACKEND", None):
            debug_backend = import_string(settings.ANYMAIL_STATUS_TRACKER_DEBUG_BACKEND)
            message.connection = debug_backend()

        if not fake_delivery:
            # Real sends go through Anymail, which fires the post_send signal.
            # handle_post_send creates the MailDelivery records and attaches them
            # to message.mail_deliveries, so we just forward those.
            # Reset first so a failed/silent send cannot return deliveries from a
            # previous send on the same EmailMessage instance.
            message.mail_deliveries = []
            message.send(fail_silently=fail_silently)
            return getattr(message, "mail_deliveries", [])

        # fake_delivery skips send() and therefore never fires post_send, so the
        # records still have to be created explicitly here. Every fake send needs
        # its own message_id unless the caller supplied one via extra_headers.
        message_id = message.extra_headers.get("message_id") or f"fake-{uuid.uuid4()}"
        now = timezone.now()
        deliveries = self.bulk_create(
            [
                self.model(esp_name=FAKE_ESP_NAME, recipient=recipient, message_id=message_id)
                for recipient in message.recipients()
            ]
        )
        MailDeliveryEvent.objects.bulk_create(
            [
                MailDeliveryEvent(
                    esp_name=FAKE_ESP_NAME,
                    message_id=message_id,
                    recipient=delivery.recipient,
                    event_id=f"fake:{uuid.uuid4()}",
                    event_type=MailDelivery.STATE_DELIVERED,
                    timestamp=now,
                    description="Fake delivery (not sent)",
                )
                for delivery in deliveries
            ]
        )
        message.mail_deliveries = deliveries
        return deliveries


class MailDelivery(models.Model):
    """
    Identity of one e-mail to one recipient, as sent by this application.

    Deliberately holds no status columns: the current state, its timestamp and
    all ESP details are derived from the MailDeliveryEvent log (see
    ``MailDeliveryQuerySet.with_state`` and the properties below), so the two
    tables can never disagree.
    """

    # Not all ESPs will have all of these states
    STATE_QUEUED = "queued"
    STATE_SENT = "sent"
    STATE_REJECTED = "rejected"
    STATE_FAILED = "failed"
    STATE_BOUNCED = "bounced"
    STATE_DEFERRED = "deferred"
    STATE_DELIVERED = "delivered"
    STATE_AUTORESPONDED = "autoresponded"
    STATE_OPENED = "opened"
    STATE_CLICKED = "clicked"
    STATE_COMPLAINED = "complained"
    STATE_UNSUBSCRIBED = "unsubscribed"
    STATE_SUBSCRIBED = "subscribed"
    STATE_UNKNOWN = "unknown"

    DELIVERY_STATES = (
        (STATE_QUEUED, "Queued"),
        (STATE_SENT, "Sent"),
        (STATE_REJECTED, "Rejected"),
        (STATE_FAILED, "Failed"),
        (STATE_BOUNCED, "Bounced"),
        (STATE_DEFERRED, "Deferred"),
        (STATE_DELIVERED, "Delivered"),
        (STATE_AUTORESPONDED, "Autoresponded"),
        (STATE_OPENED, "Opened"),
        (STATE_CLICKED, "Clicked"),
        (STATE_COMPLAINED, "Complained"),
        (STATE_UNSUBSCRIBED, "Unsubscribed"),
        (STATE_SUBSCRIBED, "Subscribed"),
        (STATE_UNKNOWN, "Unknown"),
    )

    REJECT_REASON_INVALID = "invalid"
    REJECT_REASON_BOUNCED = "bounced"
    REJECT_REASON_TIMED_OUT = "timed_out"
    REJECT_REASON_BLOCKED = "blocked"
    REJECT_REASON_SPAM = "spam"
    REJECT_REASON_REJECTED = "rejected"
    REJECT_REASON_UNSUBSCRIBED = "unsubscribed"
    REJECT_REASON_OTHER = "other"

    REJECT_REASONS = (
        (REJECT_REASON_INVALID, "Invalid"),
        (REJECT_REASON_BOUNCED, "Bounced"),
        (REJECT_REASON_TIMED_OUT, "Timed Out"),
        (REJECT_REASON_BLOCKED, "Blocked"),
        (REJECT_REASON_SPAM, "Spam"),
        (REJECT_REASON_REJECTED, "Rejected"),
        (REJECT_REASON_UNSUBSCRIBED, "Unsubscribed"),
        (REJECT_REASON_OTHER, "Other"),
    )

    # Once a delivery reaches one of these states, later "soft" events
    # (opened, clicked, delivered, ...) do not change it. Tracking events from
    # the ESP are not guaranteed to arrive in order.
    TERMINAL_NEGATIVE_STATES = frozenset(
        (
            STATE_REJECTED,
            STATE_FAILED,
            STATE_BOUNCED,
            STATE_COMPLAINED,
        )
    )

    # States in which the outcome is not known yet (success is None).
    PENDING_STATES = frozenset((STATE_QUEUED, STATE_SENT, STATE_DEFERRED, STATE_UNKNOWN))

    esp_name = models.CharField(max_length=64, help_text="Name of the ESP")
    # 300 = 255 (historical ESP ids) + "#dup-<uuid4>" from 0003's duplicate rewrite.
    message_id = models.CharField(max_length=300)
    recipient = models.EmailField()
    sent_at = models.DateTimeField(auto_now_add=True)

    objects = MailDeliveryQuerySet.as_manager()

    # Set by MailDeliveryQuerySet.with_state(); see the property setters below.
    _annotated_state = _UNSET
    _annotated_state_timestamp = _UNSET
    latest_event_id = None

    class Meta:
        verbose_name = "Mail Delivery"
        verbose_name_plural = "Mail Deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=("esp_name", "message_id", "recipient"),
                name="maildelivery_unique_esp_message_recipient",
            ),
        ]

    def __str__(self):
        return f"{self.recipient} ({self.get_state_display()} {self.state_timestamp})"

    # --- derived status -------------------------------------------------------

    @property
    def events(self):
        """All events logged for this delivery, oldest first."""
        from anymail_status_tracker.models.mail_delivery_event import MailDeliveryEvent

        return MailDeliveryEvent.objects.for_delivery(self).chronological()

    @cached_property
    def latest_event(self):
        """
        The event that defines the current state (see MailDeliveryEventQuerySet.ranked),
        or None if nothing has been logged yet. One query; cached on the instance.
        """
        from anymail_status_tracker.models.mail_delivery_event import MailDeliveryEvent

        if self.latest_event_id is not None:
            return MailDeliveryEvent.objects.filter(pk=self.latest_event_id).first()
        return MailDeliveryEvent.objects.for_delivery(self).ranked().first()

    @property
    def state(self) -> str:
        if self._annotated_state is not _UNSET:
            return self._annotated_state
        event = self.latest_event
        return event.event_type if event is not None else self.STATE_UNKNOWN

    @state.setter
    def state(self, value):
        # Populated by with_state(); Django assigns annotations via setattr.
        self._annotated_state = value

    @property
    def state_timestamp(self):
        if self._annotated_state_timestamp is not _UNSET:
            return self._annotated_state_timestamp
        event = self.latest_event
        return event.effective_timestamp if event is not None else None

    @state_timestamp.setter
    def state_timestamp(self, value):
        self._annotated_state_timestamp = value

    def get_state_display(self) -> str:
        return dict(self.DELIVERY_STATES).get(self.state, self.state)

    @property
    def success(self) -> bool | None:
        state = self.state
        if state in self.PENDING_STATES:
            return None
        return state == self.STATE_DELIVERED

    def refresh_from_db(self, *args, **kwargs):
        super().refresh_from_db(*args, **kwargs)
        self._annotated_state = _UNSET
        self._annotated_state_timestamp = _UNSET
        self.latest_event_id = None
        self.__dict__.pop("latest_event", None)
