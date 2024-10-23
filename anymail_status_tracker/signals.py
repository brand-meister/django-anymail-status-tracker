from django.dispatch import receiver

from anymail.signals import post_send, tracking

from anymail_status_tracker.models import MailDelivery


@receiver(post_send)
def handle_post_send(sender, message, status, esp_name, **kwargs):
    for _, recipient_status in status.recipients.items():
        MailDelivery.objects.create(
            message_id=recipient_status.message_id,
            state=recipient_status.status,
            esp_name=esp_name,
        )


@receiver(tracking)
def handle_tracking_event(sender, event, esp_name, **kwargs):
    MailDelivery.objects.filter(message_id=event.message_id, recipient=event.recipient).update(
        esp_name=esp_name,
        state=event.event_type,
        timestamp=event.timestamp,
        metadata=event.metadata,
        reject_reason=event.reject_reason,
        description=event.description,
        mta_response=event.mta_response,
        user_agent=event.user_agent,
        click_url=event.click_url,
        esp_event=event.esp_event,
    )
