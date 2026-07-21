import logging

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.dispatch import receiver

from anymail.signals import post_send, tracking

from anymail_status_tracker.models import MailDelivery
from anymail_status_tracker.settings import ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID


logger = logging.getLogger("anymail_status_tracker")


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


@receiver(tracking)
def handle_tracking_event(sender, event, esp_name, **kwargs):
    try:
        delivery = MailDelivery.objects.get(message_id=event.message_id, recipient=event.recipient)
    except MailDelivery.DoesNotExist:
        logger.error("No delivery found for message %s and recipient %s", event.message_id, event.recipient)
        return
    except MailDelivery.MultipleObjectsReturned:
        logger.error("Multiple deliveries found for message %s and recipient %s", event.message_id, event.recipient)
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
