import json
import uuid

from django.core.mail import EmailMessage
from django.test import RequestFactory

from anymail.webhooks.amazon_ses import AmazonSESTrackingWebhookView

from anymail_status_tracker.debug.data import DEFAULT_EMAIL, EVENT_BUILDERS
from anymail_status_tracker.models import MailDelivery


def send_test_email(email=DEFAULT_EMAIL):
    """Send a test email via the configured backend and return the message_id.

    The post_send signal will automatically create a MailDelivery record.
    """
    message = EmailMessage(
        subject="Test Email",
        body="This is a test email for webhook debugging.",
        from_email="sender@example.com",
        to=[email],
    )
    message.send()
    return message.anymail_status.message_id


def simulate_sns_event(event_type, email=DEFAULT_EMAIL, message_id=None):
    """Simulate an SNS webhook event that triggers the tracking signal.

    When message_id is provided (e.g. from send_test_email), the existing
    MailDelivery created by the post_send signal is reused.
    When message_id is None, a self-contained MailDelivery is created.
    """
    build_event = EVENT_BUILDERS[event_type]
    ses_event = build_event(email, message_id=message_id)

    if message_id is None:
        MailDelivery.objects.get_or_create(
            message_id=ses_event["mail"]["messageId"],
            defaults={"recipient": email},
        )

    sns_message_id = str(uuid.uuid4())
    payload = {
        "Type": "Notification",
        "MessageId": sns_message_id,
        "Message": json.dumps(ses_event),
    }
    request = RequestFactory().post(
        "",
        data=json.dumps(payload),
        content_type="text/plain; charset=UTF-8",
        HTTP_X_AMZ_SNS_MESSAGE_TYPE="Notification",
        HTTP_X_AMZ_SNS_MESSAGE_ID=sns_message_id,
    )
    AmazonSESTrackingWebhookView().dispatch(request)
