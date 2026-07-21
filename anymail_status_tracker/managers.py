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
        assert message.connection is None or isinstance(message.connection, AnymailBaseBackend)

        if settings.DEBUG and getattr(settings, "ANYMAIL_STATUS_TRACKER_DEBUG_BACKEND", None):
            debug_backend = import_string(settings.ANYMAIL_STATUS_TRACKER_DEBUG_BACKEND)
            message.connection = debug_backend()

        if not fake_delivery:
            # Real sends go through Anymail, which fires the post_send signal.
            # handle_post_send creates the MailDelivery records with the correct
            # ESP message_id and attaches them to message.mail_deliveries, so we
            # just forward those instead of creating (duplicate) records here.
            message.send(fail_silently=fail_silently)
            return getattr(message, "mail_deliveries", [])

        # fake_delivery skips send() and therefore never fires post_send, so the
        # records still have to be created explicitly here.
        deliveries = [
            self.model(
                recipient=recipient,
                message_id=message.extra_headers.get("message_id", "NO_MESSAGE_ID"),
                state=self.model.STATE_DELIVERED,
            )
            for recipient in message.recipients()
        ]
        message.mail_deliveries = self.bulk_create(deliveries)
        return message.mail_deliveries
