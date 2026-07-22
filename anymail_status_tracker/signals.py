import logging
import time

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.dispatch import receiver

from anymail.exceptions import AnymailInvalidAddress
from anymail.signals import post_send, tracking
from anymail.utils import parse_single_address

from anymail_status_tracker.models import MailDelivery
from anymail_status_tracker.settings import (
    ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID,
    ANYMAIL_STATUS_TRACKER_TRACKING_RETRY_DELAYS,
)


logger = logging.getLogger("anymail_status_tracker")


def _normalize_recipient(recipient):
    """Return bare addr-spec, matching Anymail post_send recipient keys."""
    if not recipient:
        return recipient
    try:
        return parse_single_address(recipient).addr_spec
    except AnymailInvalidAddress:
        return recipient


@receiver(post_send)
def handle_post_send(sender, message, status, esp_name, **kwargs):
    deliveries = [
        MailDelivery.objects.create(
            message_id=recipient_status.message_id,
            state=recipient_status.status,
            esp_name=esp_name,
            recipient=recipient,
        )
        for recipient, recipient_status in status.recipients.items()
    ]
    # Expose the created records on the message so callers can retrieve them
    # after a plain message.send() (mirrors Anymail's message.anymail_status).
    message.mail_deliveries = deliveries


def _get_delivery_for_tracking_event(event, esp_name):
    """
    Look up the MailDelivery for a tracking webhook.

    Match on message_id + esp_name + recipient. Recipient is normalized to a
    bare addr-spec first: SES Send/Open/Click events often put
    ``Name <email>`` in ``mail.destination``, while Delivery/Bounce use a
    bare address — and post_send always stores ``addr_spec`` only.

    message_id alone is not unique for SES: one send shares one MessageId
    across all to/cc/bcc recipients.

    Retries in-process when the row is missing so a webhook that races ahead of
    an uncommitted post_send insert can still succeed. The webhook request simply
    waits through the short backoff delays.
    """
    recipient = _normalize_recipient(event.recipient)
    delays = tuple(ANYMAIL_STATUS_TRACKER_TRACKING_RETRY_DELAYS)
    attempts = len(delays) + 1

    for attempt in range(attempts):
        try:
            return MailDelivery.objects.get(
                message_id=event.message_id,
                esp_name=esp_name,
                recipient=recipient,
            )
        except MailDelivery.DoesNotExist:
            if attempt >= len(delays):
                logger.error(
                    "No delivery found for message %s, esp %s, and recipient %s after %s attempt(s)",
                    event.message_id,
                    esp_name,
                    recipient,
                    attempts,
                )
                return None
            delay = delays[attempt]
            logger.warning(
                "No delivery found for message %s, esp %s, and recipient %s; retrying in %.1fs (%s/%s)",
                event.message_id,
                esp_name,
                recipient,
                delay,
                attempt + 1,
                attempts,
            )
            time.sleep(delay)
        except MailDelivery.MultipleObjectsReturned:
            logger.error(
                "Multiple deliveries found for message %s, esp %s, and recipient %s",
                event.message_id,
                esp_name,
                recipient,
            )
            return None


@receiver(tracking)
def handle_tracking_event(sender, event, esp_name, **kwargs):
    delivery = _get_delivery_for_tracking_event(event, esp_name)
    if delivery is None:
        return

    previous_state = delivery.state

    delivery.esp_name = esp_name
    delivery.state = event.event_type
    delivery.timestamp = event.timestamp
    delivery.metadata = event.metadata or {}
    delivery.reject_reason = event.reject_reason
    delivery.description = event.description
    delivery.mta_response = event.mta_response
    delivery.user_agent = event.user_agent
    delivery.click_url = event.click_url
    delivery.esp_event = event.esp_event
    delivery.save()

    if ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID:
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
                "Error creating log entry for delivery %s: "
                "Wrongly configured ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID",
                delivery.id,
            )
