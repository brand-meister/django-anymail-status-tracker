from django.core.management import call_command

import pytest

from anymail_status_tracker.debug import helpers as debug_helpers
from anymail_status_tracker.debug.data import get_delivery_event
from anymail_status_tracker.models import MailDelivery

from .conftest import RECIPIENT, fire_post_send, post_sns_event


pytestmark = pytest.mark.django_db


def get_delivery(message_id, recipient=RECIPIENT):
    return MailDelivery.objects.get(message_id=message_id, recipient=recipient)


def test_delivery_event_updates_derived_state(ses_message_id):
    fire_post_send(ses_message_id)

    post_sns_event("Delivery", ses_message_id)

    delivery = get_delivery(ses_message_id)
    assert delivery.state == MailDelivery.STATE_DELIVERED
    assert delivery.success is True
    assert delivery.state_timestamp is not None
    event = delivery.latest_event
    assert event.mta_response == "250 ok: Message 64111812 accepted"
    assert event.esp_event["notificationType"] == "Delivery"


def test_bounce_event_details_are_on_the_latest_event(ses_message_id):
    fire_post_send(ses_message_id)

    post_sns_event("Bounce", ses_message_id)

    delivery = get_delivery(ses_message_id)
    assert delivery.state == MailDelivery.STATE_BOUNCED
    assert delivery.success is False
    event = delivery.latest_event
    assert event.reject_reason == MailDelivery.REJECT_REASON_BOUNCED
    assert event.description == "Permanent: General"
    assert event.mta_response == "smtp; 550 5.1.1 User unknown"


def test_tracking_event_matches_display_name_recipient(ses_message_id):
    fire_post_send(ses_message_id)

    post_sns_event("Delivery", ses_message_id, email=f"Some Name <{RECIPIENT}>")

    assert get_delivery(ses_message_id).state == MailDelivery.STATE_DELIVERED


def test_tracking_event_only_affects_matching_recipient(ses_message_id):
    fire_post_send(ses_message_id, recipients=("a@example.com", "b@example.com"))

    post_sns_event("Delivery", ses_message_id, email="a@example.com")

    assert get_delivery(ses_message_id, "a@example.com").state == MailDelivery.STATE_DELIVERED
    assert get_delivery(ses_message_id, "b@example.com").state == MailDelivery.STATE_QUEUED


def test_debug_helpers_simulate_and_send(settings):
    # Local debug helpers omit Basic auth; clear webhook secret for this path.
    settings.ANYMAIL = {}
    settings.EMAIL_BACKEND = "anymail.backends.test.EmailBackend"

    message_id = debug_helpers.send_test_email(RECIPIENT)
    assert MailDelivery.objects.filter(message_id=str(message_id)).exists()

    fresh_id, sns_id = debug_helpers.simulate_sns_event("Delivery")
    assert MailDelivery.objects.filter(message_id=fresh_id, esp_name="Amazon SES").exists()
    assert get_delivery(fresh_id).state == MailDelivery.STATE_DELIVERED

    debug_helpers.simulate_sns_event("Delivery", message_id=fresh_id, sns_message_id=sns_id)
    assert MailDelivery.objects.filter(message_id=fresh_id).count() == 1

    orphan_id, _ = debug_helpers.simulate_sns_event("Open", create_delivery=False)
    assert not MailDelivery.objects.filter(message_id=orphan_id).exists()

    assert get_delivery_event()["mail"]["messageId"]  # exercises _generate_message_id


def test_send_test_email_requires_anymail_backend(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    with pytest.raises(RuntimeError, match="No anymail_status"):
        debug_helpers.send_test_email(RECIPIENT)


def test_simulate_sns_event_management_command(settings):
    settings.ANYMAIL = {}
    call_command("simulate_sns_event", event="Delivery", repeat=2)
    call_command("simulate_sns_event", event="Bounce", orphan=True)

    assert MailDelivery.objects.filter(esp_name="Amazon SES").count() == 1
    assert MailDelivery.objects.get().state == MailDelivery.STATE_DELIVERED
