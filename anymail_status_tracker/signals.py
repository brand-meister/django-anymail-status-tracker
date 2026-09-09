import logging
import uuid

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.dispatch import receiver
from django.utils import timezone

from anymail.exceptions import AnymailInvalidAddress
from anymail.signals import post_send, tracking
from anymail.utils import parse_single_address

from anymail_status_tracker.models import MailDelivery, MailDeliveryEvent
from anymail_status_tracker.settings import (
    ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID,
    ANYMAIL_STATUS_TRACKER_LOG_TRACKING_EVENT,
)


logger = logging.getLogger("anymail_status_tracker")

NO_MESSAGE_ID_PREFIX = "NO_MESSAGE_ID"

# Namespace for deterministic ids of the baseline event written by post_send, so
# that a re-fired post_send for the same delivery is a no-op.
POST_SEND_EVENT_NAMESPACE = uuid.UUID("6f1b2a0e-3c4d-4e5f-8a9b-0c1d2e3f4a5b")


def _normalize_recipient(recipient):
    """Return bare addr-spec, matching Anymail post_send recipient keys."""
    if not recipient:
        return recipient
    try:
        return parse_single_address(recipient).addr_spec
    except AnymailInvalidAddress:
        return recipient


def _normalize_message_id(message_id) -> str:
    """
    ESP message ids are opaque strings; some backends (e.g. Anymail's test
    backend) use ints and failed bulk entries may have none at all. The natural
    key needs a non-empty, unique string.
    """
    if message_id is None or message_id == "":
        return f"{NO_MESSAGE_ID_PREFIX}-{uuid.uuid4()}"
    return str(message_id)


def _json_safe(esp_event):
    if hasattr(esp_event, "dict"):
        # Django QueryDict (some ESP webhook parsers)
        return dict(esp_event.lists())
    return esp_event


def _serialize_tracking_event(event, esp_name):
    return {
        "esp_name": esp_name,
        "event_type": event.event_type,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "event_id": event.event_id,
        "message_id": event.message_id,
        "recipient": event.recipient,
        "metadata": event.metadata or {},
        "reject_reason": event.reject_reason,
        "description": event.description,
        "mta_response": event.mta_response,
        "user_agent": event.user_agent,
        "click_url": event.click_url,
        "tags": event.tags,
        "esp_event": _json_safe(event.esp_event),
    }


def _store_event(event_row: MailDeliveryEvent):
    """
    Insert the event, or return the already stored one if the same notification
    was seen before (ESP redelivery, re-fired signal). Returns (event_row, created).
    """
    try:
        with transaction.atomic():
            event_row.save(force_insert=True)
        return event_row, True
    except IntegrityError:
        existing = MailDeliveryEvent.objects.get(esp_name=event_row.esp_name, event_id=event_row.event_id)
        return existing, False


@receiver(post_send)
def handle_post_send(sender, message, status, esp_name, **kwargs):
    """
    Record the send: one MailDelivery identity row per recipient plus a baseline
    event carrying the status the ESP returned (usually "queued").

    This runs inside whatever transaction the caller used for message.send().
    It must never raise: Anymail re-raises receiver exceptions from send(), and
    that would roll back the caller's (possibly large) transaction for a mail
    that the ESP has already accepted.

    Nothing here needs to wait for, or be reconciled with, tracking webhooks:
    the state is derived from the event log at read time, so events that
    arrive before this transaction commits simply count once it is visible.
    """
    deliveries = []
    try:
        now = timezone.now()
        for recipient, recipient_status in status.recipients.items():
            key = {
                "esp_name": esp_name,
                "message_id": _normalize_message_id(recipient_status.message_id),
                "recipient": _normalize_recipient(recipient),
            }
            delivery, _created = MailDelivery.objects.get_or_create(**key)
            deliveries.append(delivery)
            _store_event(
                MailDeliveryEvent(
                    **key,
                    event_id=str(uuid.uuid5(POST_SEND_EVENT_NAMESPACE, "|".join(key.values()))),
                    event_type=recipient_status.status or MailDelivery.STATE_UNKNOWN,
                    timestamp=now,
                    description="Accepted by ESP on send",
                )
            )
    except Exception:
        logger.exception(
            "Failed to record MailDelivery for %s message %s",
            esp_name,
            getattr(status, "message_id", None),
        )

    # Expose the created records on the message so callers can retrieve them
    # after a plain message.send() (mirrors Anymail's message.anymail_status).
    message.mail_deliveries = deliveries


def _build_event_row(event, esp_name) -> MailDeliveryEvent:
    return MailDeliveryEvent(
        esp_name=esp_name,
        message_id=_normalize_message_id(event.message_id),
        recipient=_normalize_recipient(event.recipient),
        event_id=str(event.event_id or uuid.uuid4()),
        event_type=event.event_type,
        timestamp=event.timestamp,
        metadata=event.metadata or {},
        tags=list(event.tags or []),
        reject_reason=event.reject_reason,
        description=event.description,
        mta_response=event.mta_response,
        user_agent=event.user_agent,
        click_url=event.click_url,
        esp_event=_json_safe(event.esp_event) or {},
    )


def _log_admin_action(delivery: MailDelivery, previous_state: str):
    try:
        LogEntry.objects.create(
            user_id=ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID,
            content_type_id=ContentType.objects.get_for_model(delivery).pk,
            object_id=delivery.pk,
            object_repr=str(delivery)[:200],
            action_flag=CHANGE,
            change_message=f"Status updated from {previous_state} to {delivery.state}",
        )
    except IntegrityError:
        logger.error(
            "Error creating log entry for delivery %s: Wrongly configured ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID",
            delivery.id,
        )


@receiver(tracking)
def handle_tracking_event(sender, event, esp_name, **kwargs):
    """
    Append a tracking webhook to the event log. That is all that is needed: the
    MailDelivery state is derived from the log, so this handler never reads
    for update, never waits and never touches the MailDelivery table.

    Infrastructure errors (database unavailable, ...) are allowed to propagate:
    Anymail then answers the webhook with a 5xx and the ESP redelivers later.
    """
    if ANYMAIL_STATUS_TRACKER_LOG_TRACKING_EVENT:
        logger.info(
            "Tracking event from %s for message %s recipient %s",
            esp_name,
            event.message_id,
            event.recipient,
            extra={"tracking_event": _serialize_tracking_event(event, esp_name)},
        )

    event_row = _build_event_row(event, esp_name)
    deliveries = MailDelivery.objects.for_event(event_row)

    # Only needed for the optional admin log; costs one query when enabled.
    before = deliveries.with_state().first() if ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID else None

    event_row, created = _store_event(event_row)
    if not created:
        logger.info(
            "Duplicate tracking notification %s from %s for message %s; already recorded",
            event_row.event_id,
            esp_name,
            event_row.message_id,
        )
        return

    if not deliveries.exists():
        # Either the send is still inside an uncommitted transaction (the event
        # counts as soon as the row is visible) or the mail was sent by something
        # that does not use this tracker. Both are expected; the event is kept.
        logger.info(
            "No delivery (yet) for %s message %s recipient %s; kept %s event as orphan",
            esp_name,
            event_row.message_id,
            event_row.recipient,
            event_row.event_type,
        )
        return

    if before is not None:
        after = deliveries.with_state().first()
        if after is not None and after.state != before.state:
            _log_admin_action(after, before.state)
