from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest

from anymail_status_tracker.admin.mail_delivery import MailDeliveryAdmin, _format_datetime_ms
from anymail_status_tracker.models import MailDelivery, MailDeliveryEvent

from .conftest import RECIPIENT, SES, fire_post_send, post_sns_event


pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client):
    user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
    client.force_login(user)
    return client


def test_delivery_change_page_lists_events(admin_client, ses_message_id):
    (delivery,) = fire_post_send(ses_message_id).mail_deliveries
    sns_id = post_sns_event("Delivery", ses_message_id)

    response = admin_client.get(reverse("admin:anymail_status_tracker_maildelivery_change", args=[delivery.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert sns_id in body
    assert "Test Email" in body  # Subject from SES mail headers
    assert ">Subject<" in body


def test_delivery_change_page_without_events(admin_client):
    delivery = MailDelivery.objects.create(esp_name=SES, message_id="bare", recipient=RECIPIENT)

    response = admin_client.get(reverse("admin:anymail_status_tracker_maildelivery_change", args=[delivery.pk]))

    assert response.status_code == 200
    assert "No events logged yet." in response.content.decode()


def test_delivery_change_page_tolerates_none_tags(admin_client):
    delivery = MailDelivery.objects.create(esp_name=SES, message_id="tagged", recipient=RECIPIENT)
    MailDeliveryEvent.objects.create(
        esp_name=SES,
        message_id=delivery.message_id,
        recipient=delivery.recipient,
        event_id="evt-none-tags",
        event_type=MailDelivery.STATE_DELIVERED,
        tags=[None, "campaign", None],
    )

    response = admin_client.get(reverse("admin:anymail_status_tracker_maildelivery_change", args=[delivery.pk]))

    assert response.status_code == 200
    assert "campaign" in response.content.decode()


def test_delivery_changelist_state_filter(admin_client, ses_message_id):
    fire_post_send(ses_message_id)
    post_sns_event("Delivery", ses_message_id)
    fire_post_send("still-queued")

    url = reverse("admin:anymail_status_tracker_maildelivery_changelist")
    assert admin_client.get(url).status_code == 200
    filtered = admin_client.get(url, {"state": MailDelivery.STATE_DELIVERED})

    assert [d.message_id for d in filtered.context["cl"].result_list] == [ses_message_id]


def test_event_admin_orphan_filter(admin_client, ses_message_id):
    fire_post_send(ses_message_id)
    post_sns_event("Delivery", ses_message_id)
    post_sns_event("Delivery", "orphan-message")

    url = reverse("admin:anymail_status_tracker_maildeliveryevent_changelist")
    assert admin_client.get(url).status_code == 200  # has_delivery unset → all rows
    orphans = admin_client.get(url, {"has_delivery": "no"})
    matched = admin_client.get(url, {"has_delivery": "yes"})

    assert list(orphans.context["cl"].result_list) == [MailDeliveryEvent.objects.get(message_id="orphan-message")]
    # post_send baseline event + the Delivery event both belong to the tracked delivery
    assert set(matched.context["cl"].result_list) == set(MailDeliveryEvent.objects.filter(message_id=ses_message_id))
    assert len(matched.context["cl"].result_list) == 2


def test_event_change_page_is_read_only(admin_client, ses_message_id):
    post_sns_event("Delivery", ses_message_id)
    event = MailDeliveryEvent.objects.get()

    response = admin_client.get(reverse("admin:anymail_status_tracker_maildeliveryevent_change", args=[event.pk]))

    assert response.status_code == 200
    assert "Orphan" in response.content.decode()
    assert admin_client.get(reverse("admin:anymail_status_tracker_maildeliveryevent_add")).status_code == 403


def test_event_change_page_links_matching_delivery(admin_client, ses_message_id):
    (delivery,) = fire_post_send(ses_message_id).mail_deliveries
    post_sns_event("Delivery", ses_message_id)
    event = MailDeliveryEvent.objects.filter(event_type=MailDelivery.STATE_DELIVERED).get()

    response = admin_client.get(reverse("admin:anymail_status_tracker_maildeliveryevent_change", args=[event.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Orphan" not in body
    assert reverse("admin:anymail_status_tracker_maildelivery_change", args=[delivery.pk]) in body


def test_format_datetime_ms_and_unsaved_event_log():
    assert _format_datetime_ms(None) == "-"
    admin = MailDeliveryAdmin(MailDelivery, None)
    assert admin.event_log(MailDelivery()) == "-"
