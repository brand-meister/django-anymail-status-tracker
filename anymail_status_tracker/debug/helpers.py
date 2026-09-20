import json
import uuid
from datetime import datetime, timezone

from django.core.mail import EmailMessage
from django.test import RequestFactory

from anymail.webhooks.amazon_ses import AmazonSESTrackingWebhookView

from anymail_status_tracker.debug.data import DEFAULT_EMAIL, EVENT_BUILDERS
from anymail_status_tracker.models import MailDelivery


SES_ESP_NAME = "Amazon SES"


def send_test_email(email=DEFAULT_EMAIL):
    """Send a test email via the configured backend and return the message_id.

    The post_send signal will automatically create a MailDelivery record.
    Requires an Anymail email backend to be configured.
    """
    message = EmailMessage(
        subject="Test Email",
        body="This is a test email for webhook debugging.",
        from_email="sender@example.com",
        to=[email],
    )
    message.send()
    if not hasattr(message, "anymail_status") or message.anymail_status is None:
        raise RuntimeError("No anymail_status on message. Ensure an Anymail email backend is configured.")
    return message.anymail_status.message_id


def simulate_sns_event(event_type, email=DEFAULT_EMAIL, message_id=None, sns_message_id=None, create_delivery=None):
    """Simulate an SNS webhook event that triggers the tracking signal.

    When ``message_id`` is None, a fresh id is generated and a self-contained
    Amazon SES ``MailDelivery`` is created for it (unless ``create_delivery`` is
    False / ``--orphan``). When ``message_id`` is provided, the event targets
    that existing delivery and no new row is created by default — otherwise a
    second row under ``esp_name="Amazon SES"`` would appear next to whatever
    ``post_send`` already wrote (e.g. Console).

    Pass the same ``sns_message_id`` twice to simulate an SNS redelivery; the
    second call must be a no-op.

    Returns ``(message_id, sns_message_id)``.
    """
    if create_delivery is None:
        create_delivery = message_id is None

    build_event = EVENT_BUILDERS[event_type]
    ses_event = build_event(email, message_id=message_id)
    message_id = ses_event["mail"]["messageId"]

    if create_delivery:
        # Identity row only; its state is derived from the events we are about to log.
        MailDelivery.objects.get_or_create(esp_name=SES_ESP_NAME, message_id=message_id, recipient=email)

    sns_message_id = sns_message_id or str(uuid.uuid4())
    payload = {
        "Type": "Notification",
        "MessageId": sns_message_id,
        "Timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
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
    return message_id, sns_message_id
