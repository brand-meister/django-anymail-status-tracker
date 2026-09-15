from django.core.mail import EmailMessage

import pytest

from anymail_status_tracker.models import MailDelivery, MailDeliveryEvent

from .conftest import RECIPIENT, SES, fire_post_send


pytestmark = pytest.mark.django_db


def test_post_send_creates_one_delivery_and_baseline_event_per_recipient(ses_message_id):
    recipients = ("a@example.com", "b@example.com")

    message = fire_post_send(ses_message_id, recipients=recipients)

    deliveries = MailDelivery.objects.filter(message_id=ses_message_id).order_by("recipient")
    assert [d.recipient for d in deliveries] == list(recipients)
    assert {d.esp_name for d in deliveries} == {SES}
    assert {d.state for d in deliveries} == {MailDelivery.STATE_QUEUED}
    assert {d.success for d in deliveries} == {None}
    assert sorted(d.pk for d in message.mail_deliveries) == sorted(d.pk for d in deliveries)

    events = MailDeliveryEvent.objects.filter(message_id=ses_message_id)
    assert events.count() == 2
    assert {e.event_type for e in events} == {MailDelivery.STATE_QUEUED}
    assert all(e.timestamp is not None for e in events)


def test_plain_django_send_through_anymail_backend_creates_delivery(settings):
    # Django's test runner swaps EMAIL_BACKEND for locmem; use Anymail's test backend.
    settings.EMAIL_BACKEND = "anymail.backends.test.EmailBackend"
    message = EmailMessage(subject="Hi", body="Body", from_email="sender@example.com", to=[RECIPIENT])

    message.send()

    delivery = MailDelivery.objects.get()
    assert delivery.recipient == RECIPIENT
    assert delivery.esp_name == "Test"
    assert delivery.message_id == str(message.anymail_status.message_id)
    assert delivery.state == MailDelivery.STATE_SENT  # Anymail's test backend reports "sent"
    assert message.mail_deliveries == [delivery]


def test_create_message_sends_and_returns_deliveries(settings):
    settings.EMAIL_BACKEND = "anymail.backends.test.EmailBackend"
    message = EmailMessage(subject="Hi", body="Body", from_email="sender@example.com", to=[RECIPIENT])

    deliveries = MailDelivery.objects.create_message(message)

    assert len(deliveries) == 1
    assert deliveries[0].recipient == RECIPIENT
    assert deliveries == message.mail_deliveries


def test_create_message_uses_debug_backend_when_configured(settings):
    settings.DEBUG = True
    settings.ANYMAIL_STATUS_TRACKER_DEBUG_BACKEND = "anymail.backends.test.EmailBackend"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    message = EmailMessage(subject="Hi", body="Body", from_email="sender@example.com", to=[RECIPIENT])

    (delivery,) = MailDelivery.objects.create_message(message)

    assert delivery.esp_name == "Test"
    assert delivery.state == MailDelivery.STATE_SENT


def test_delivery_without_any_event_is_unknown():
    delivery = MailDelivery.objects.create(esp_name=SES, message_id="m", recipient=RECIPIENT)

    assert delivery.state == MailDelivery.STATE_UNKNOWN
    assert delivery.get_state_display() == "Unknown"
    assert delivery.success is None
    assert delivery.state_timestamp is None
    assert delivery.latest_event is None
    assert MailDelivery.objects.with_state().get().state == MailDelivery.STATE_UNKNOWN
