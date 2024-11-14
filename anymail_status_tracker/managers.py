from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import Manager
from django.utils.module_loading import import_string

from anymail.backends.base import AnymailBaseBackend


class MailDeliveryManager(Manager):
    def create_message(
        self,
        message: EmailMessage,
        fail_silently: bool = False,
        fake_delivery: bool = False,
    ):
        assert isinstance(message, EmailMessage)
        assert isinstance(message.connection, AnymailBaseBackend)

        if settings.DEBUG and getattr(settings, "ANYMAIL_STATUS_TRACKER_DEBUG_BACKEND", None):
            debug_backend = import_string(settings.ANYMAIL_STATUS_TRACKER_DEBUG_BACKEND)
            message.connection = debug_backend()

        if not fake_delivery:
            message.send(fail_silently=fail_silently)

        deliveries = []
        for recipient in message.recipients():
            deliveries.append(
                self.model(
                    recipient=recipient,
                    message_id=message.extra_headers.get("message_id", "NO_MESSAGE_ID"),
                    state=self.model.STATE_DELIVERED if fake_delivery else self.model.STATE_SENT,
                )
            )
        return self.bulk_create(deliveries)
