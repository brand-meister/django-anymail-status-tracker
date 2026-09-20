import base64
import json
import uuid
from datetime import datetime, timezone

from django.core.mail import EmailMessage
from django.test import RequestFactory

import pytest
from anymail.message import AnymailRecipientStatus, AnymailStatus
from anymail.signals import post_send
from anymail.webhooks.amazon_ses import AmazonSESTrackingWebhookView

from anymail_status_tracker.debug.data import EVENT_BUILDERS


SES = "Amazon SES"
RECIPIENT = "recipient@example.com"


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def fire_post_send(message_id, recipients=(RECIPIENT,), esp_name=SES, status="queued"):
    """
    Emit Anymail's post_send signal exactly as a real ESP backend would after a
    successful API call, without going through any email backend.
    """
    message = EmailMessage(subject="Test", body="Body", from_email="sender@example.com", to=list(recipients))
    anymail_status = AnymailStatus()
    anymail_status.set_recipient_status(
        {recipient: AnymailRecipientStatus(message_id=message_id, status=status) for recipient in recipients}
    )
    message.anymail_status = anymail_status
    post_send.send(sender=object, message=message, status=anymail_status, esp_name=esp_name)
    return message


def post_sns_event(event_type, message_id, email=RECIPIENT, timestamp=None, sns_message_id=None):
    """
    Deliver an SES notification through Anymail's SES tracking webhook view,
    the same way SNS would. Returns the SNS MessageId used (Anymail's event_id).
    """
    ses_event = EVENT_BUILDERS[event_type](email, message_id=message_id)
    sns_message_id = sns_message_id or str(uuid.uuid4())
    payload = {
        "Type": "Notification",
        "MessageId": sns_message_id,
        "Timestamp": _iso(timestamp or datetime.now(tz=timezone.utc)),
        "Message": json.dumps(ses_event),
    }
    request = RequestFactory().post(
        "/mail-webhooks/amazon-ses/tracking/",
        data=json.dumps(payload),
        content_type="text/plain; charset=UTF-8",
        HTTP_X_AMZ_SNS_MESSAGE_TYPE="Notification",
        HTTP_X_AMZ_SNS_MESSAGE_ID=sns_message_id,
        HTTP_AUTHORIZATION="Basic " + base64.b64encode(b"user:pass").decode(),
    )
    response = AmazonSESTrackingWebhookView().dispatch(request)
    assert response.status_code == 200, response.content
    return sns_message_id


@pytest.fixture
def ses_message_id():
    return f"0107{uuid.uuid4().hex[:12]}-{uuid.uuid4()}-000000"
