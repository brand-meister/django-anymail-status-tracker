"""
Tests for the behaviour that motivated the event-log architecture: webhooks
racing ahead of post_send, SNS redeliveries, out-of-order events and handlers
that must never raise into the caller's transaction.
"""

from datetime import datetime, timedelta, timezone

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction

import pytest

from anymail_status_tracker import signals
from anymail_status_tracker.models import MailDelivery, MailDeliveryEvent

from .conftest import RECIPIENT, SES, fire_post_send, post_sns_event


pytestmark = pytest.mark.django_db

T0 = datetime(2026, 9, 4, 7, 20, 0, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def delivery(message_id, recipient=RECIPIENT):
    return MailDelivery.objects.get(esp_name=SES, message_id=message_id, recipient=recipient)


def annotated(message_id, recipient=RECIPIENT):
    return MailDelivery.objects.with_state().get(esp_name=SES, message_id=message_id, recipient=recipient)


# --- webhook arrives before the MailDelivery row exists -----------------------


def test_early_webhook_is_kept_as_orphan_without_error(ses_message_id, caplog):
    post_sns_event("Delivery", ses_message_id)

    assert not MailDelivery.objects.exists()
    event = MailDeliveryEvent.objects.get()
    assert event.message_id == ses_message_id
    assert event.event_type == MailDelivery.STATE_DELIVERED
    assert MailDeliveryEvent.objects.orphans().count() == 1
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_early_webhook_counts_as_soon_as_delivery_exists(ses_message_id):
    post_sns_event("Send", ses_message_id, timestamp=at(1))
    post_sns_event("Delivery", ses_message_id, timestamp=at(5))

    with transaction.atomic():
        fire_post_send(ses_message_id)
        # No reconciliation step needed: the state is derived on read.
        assert delivery(ses_message_id).state == MailDelivery.STATE_DELIVERED

    d = delivery(ses_message_id)
    assert d.state == MailDelivery.STATE_DELIVERED
    assert d.state_timestamp == at(5)
    assert d.latest_event.mta_response == "250 ok: Message 64111812 accepted"
    assert MailDeliveryEvent.objects.orphans().count() == 0
    assert [e.event_type for e in d.events] == [
        MailDelivery.STATE_SENT,
        MailDelivery.STATE_DELIVERED,
        MailDelivery.STATE_QUEUED,  # baseline written by post_send, timestamped "now"
    ]


def test_events_during_open_transaction_do_not_error(ses_message_id):
    with transaction.atomic():
        fire_post_send(ses_message_id)
        # Simulates SNS hitting the webhook while the batch transaction is open.
        post_sns_event("Delivery", ses_message_id, timestamp=at(5))

    assert delivery(ses_message_id).state == MailDelivery.STATE_DELIVERED


# --- SNS redelivery -----------------------------------------------------------


def test_redelivered_notification_is_recorded_once(ses_message_id):
    fire_post_send(ses_message_id)

    sns_id = post_sns_event("Delivery", ses_message_id, timestamp=at(5))
    post_sns_event("Delivery", ses_message_id, timestamp=at(5), sns_message_id=sns_id)
    post_sns_event("Delivery", ses_message_id, timestamp=at(5), sns_message_id=sns_id)

    assert MailDeliveryEvent.objects.filter(event_type=MailDelivery.STATE_DELIVERED).count() == 1
    assert delivery(ses_message_id).state == MailDelivery.STATE_DELIVERED


# --- ordering rules -----------------------------------------------------------


def test_newer_event_wins(ses_message_id):
    fire_post_send(ses_message_id)
    post_sns_event("Delivery", ses_message_id, timestamp=at(5))
    post_sns_event("Open", ses_message_id, timestamp=at(60))

    d = delivery(ses_message_id)
    assert d.state == MailDelivery.STATE_OPENED
    assert d.latest_event.user_agent == "Mozilla/5.0 (Example)"


def test_older_event_arriving_late_does_not_regress_state(ses_message_id):
    fire_post_send(ses_message_id)
    post_sns_event("Open", ses_message_id, timestamp=at(60))
    post_sns_event("Delivery", ses_message_id, timestamp=at(5))

    d = delivery(ses_message_id)
    assert d.state == MailDelivery.STATE_OPENED
    assert d.state_timestamp == at(60)


def test_soft_event_never_overrides_terminal_negative_state(ses_message_id):
    fire_post_send(ses_message_id)
    post_sns_event("Bounce", ses_message_id, timestamp=at(5))
    post_sns_event("Open", ses_message_id, timestamp=at(60))
    post_sns_event("Delivery", ses_message_id, timestamp=at(70))

    d = delivery(ses_message_id)
    assert d.state == MailDelivery.STATE_BOUNCED
    assert d.latest_event.reject_reason == MailDelivery.REJECT_REASON_BOUNCED
    assert d.success is False


def test_terminal_negative_event_wins_even_if_older_than_soft_state(ses_message_id):
    fire_post_send(ses_message_id)
    post_sns_event("Open", ses_message_id, timestamp=at(60))
    post_sns_event("Bounce", ses_message_id, timestamp=at(5))

    assert delivery(ses_message_id).state == MailDelivery.STATE_BOUNCED


def test_newer_terminal_negative_replaces_older_terminal_negative(ses_message_id):
    fire_post_send(ses_message_id)
    post_sns_event("Bounce", ses_message_id, timestamp=at(5))
    post_sns_event("Complaint", ses_message_id, timestamp=at(60))

    d = delivery(ses_message_id)
    assert d.state == MailDelivery.STATE_COMPLAINED
    assert d.latest_event.reject_reason == MailDelivery.REJECT_REASON_SPAM


def test_older_terminal_negative_does_not_replace_newer_terminal_negative(ses_message_id):
    fire_post_send(ses_message_id)
    post_sns_event("Complaint", ses_message_id, timestamp=at(60))
    post_sns_event("Bounce", ses_message_id, timestamp=at(5))

    assert delivery(ses_message_id).state == MailDelivery.STATE_COMPLAINED


def test_queued_baseline_never_beats_an_esp_event(ses_message_id):
    # The SES "Send" event is timestamped by SES; post_send's baseline by our clock,
    # which will usually be *later*. The baseline must still lose.
    post_sns_event("Send", ses_message_id, timestamp=at(-30))
    fire_post_send(ses_message_id)  # baseline timestamp = now, far in the future of at(-30)

    assert delivery(ses_message_id).state == MailDelivery.STATE_SENT


def test_event_without_timestamp_falls_back_to_received_at(ses_message_id):
    fire_post_send(ses_message_id)
    MailDeliveryEvent.objects.create(
        esp_name=SES,
        message_id=ses_message_id,
        recipient=RECIPIENT,
        event_id="no-ts",
        event_type=MailDelivery.STATE_DELIVERED,
        timestamp=None,
    )

    d = delivery(ses_message_id)
    assert d.state == MailDelivery.STATE_DELIVERED
    assert d.state_timestamp == d.latest_event.received_at


# ESP-side timestamps per event type; the parametrized sequences are *arrival* orders.
ESP_TIME = {"Send": 1, "Delivery": 5, "Bounce": 30, "Complaint": 40, "Open": 60}


@pytest.mark.parametrize(
    "arrival_order, expected",
    [
        (("Send", "Delivery", "Open"), MailDelivery.STATE_OPENED),
        (("Open", "Delivery", "Send"), MailDelivery.STATE_OPENED),
        (("Delivery", "Bounce", "Open"), MailDelivery.STATE_BOUNCED),
        (("Open", "Bounce"), MailDelivery.STATE_BOUNCED),
        (("Bounce", "Complaint"), MailDelivery.STATE_COMPLAINED),
        (("Complaint", "Bounce"), MailDelivery.STATE_COMPLAINED),
        ((), MailDelivery.STATE_QUEUED),
    ],
)
def test_annotation_and_property_agree(ses_message_id, arrival_order, expected):
    fire_post_send(ses_message_id)
    for event_type in arrival_order:
        post_sns_event(event_type, ses_message_id, timestamp=at(ESP_TIME[event_type]))

    plain = delivery(ses_message_id)
    ann = annotated(ses_message_id)
    assert plain.state == ann.state == expected
    assert plain.state_timestamp == ann.state_timestamp
    assert plain.latest_event.pk == ann.latest_event_id == ann.latest_event.pk


# --- queryset helpers ---------------------------------------------------------


def test_filter_state_and_exclude_state():
    fire_post_send("m-1", recipients=("a@example.com",))
    fire_post_send("m-2", recipients=("b@example.com",))
    fire_post_send("m-3", recipients=("c@example.com",))
    post_sns_event("Delivery", "m-1", email="a@example.com")
    post_sns_event("Bounce", "m-2", email="b@example.com")

    def recipients(qs):
        return sorted(qs.values_list("recipient", flat=True))

    assert recipients(MailDelivery.objects.filter_state(MailDelivery.STATE_DELIVERED)) == ["a@example.com"]
    assert recipients(MailDelivery.objects.filter_state(*MailDelivery.TERMINAL_NEGATIVE_STATES)) == ["b@example.com"]
    assert recipients(MailDelivery.objects.filter_state(MailDelivery.STATE_QUEUED)) == ["c@example.com"]
    assert recipients(MailDelivery.objects.exclude_state(MailDelivery.STATE_QUEUED)) == [
        "a@example.com",
        "b@example.com",
    ]


def test_with_state_needs_no_further_queries(ses_message_id, django_assert_num_queries):
    fire_post_send(ses_message_id)
    post_sns_event("Delivery", ses_message_id, timestamp=at(5))

    with django_assert_num_queries(1):
        d = MailDelivery.objects.with_state().get(message_id=ses_message_id)
        assert d.state == MailDelivery.STATE_DELIVERED
        assert d.state_timestamp == at(5)
        assert d.success is True
        assert d.get_state_display() == "Delivered"


def test_property_fallback_uses_one_cached_query(ses_message_id, django_assert_num_queries):
    fire_post_send(ses_message_id)
    post_sns_event("Delivery", ses_message_id, timestamp=at(5))
    d = MailDelivery.objects.get(message_id=ses_message_id)

    with django_assert_num_queries(1):
        assert d.state == MailDelivery.STATE_DELIVERED
        assert d.state_timestamp == at(5)
        assert d.latest_event.event_type == MailDelivery.STATE_DELIVERED
        assert d.success is True


def test_refresh_from_db_clears_derived_cache(ses_message_id):
    fire_post_send(ses_message_id)
    d = MailDelivery.objects.with_state().get(message_id=ses_message_id)
    assert d.state == MailDelivery.STATE_QUEUED

    post_sns_event("Delivery", ses_message_id, timestamp=at(5))
    assert d.state == MailDelivery.STATE_QUEUED  # stale annotation, by design
    d.refresh_from_db()
    assert d.state == MailDelivery.STATE_DELIVERED


# --- post_send robustness -----------------------------------------------------


def test_post_send_never_raises_into_the_caller(ses_message_id, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise IntegrityError("simulated")

    monkeypatch.setattr(type(MailDelivery.objects), "get_or_create", boom)

    message = fire_post_send(ses_message_id)  # must not raise

    assert message.mail_deliveries == []
    assert any("Failed to record MailDelivery" in r.getMessage() for r in caplog.records)


def test_post_send_twice_for_same_message_is_idempotent(ses_message_id):
    fire_post_send(ses_message_id)
    fire_post_send(ses_message_id)

    assert MailDelivery.objects.filter(message_id=ses_message_id).count() == 1
    assert MailDeliveryEvent.objects.filter(message_id=ses_message_id).count() == 1


def test_post_send_without_message_id_gets_unique_placeholder():
    m1 = fire_post_send(None)
    m2 = fire_post_send(None)

    ids = {m1.mail_deliveries[0].message_id, m2.mail_deliveries[0].message_id}
    assert len(ids) == 2
    assert all(i.startswith("NO_MESSAGE_ID-") for i in ids)


def test_post_send_normalizes_display_name_recipients(ses_message_id):
    fire_post_send(ses_message_id, recipients=(f"Some Name <{RECIPIENT}>",))

    assert delivery(ses_message_id).recipient == RECIPIENT


def test_natural_key_is_unique(ses_message_id):
    fire_post_send(ses_message_id)

    with pytest.raises(IntegrityError), transaction.atomic():
        MailDelivery.objects.create(esp_name=SES, message_id=ses_message_id, recipient=RECIPIENT)


# --- create_message(fake_delivery=True) ---------------------------------------


def test_fake_delivery_twice_for_same_recipient_does_not_collide():
    def msg():
        return EmailMessage(subject="Hi", body="Body", from_email="sender@example.com", to=[RECIPIENT])

    first = MailDelivery.objects.create_message(msg(), fake_delivery=True)
    second = MailDelivery.objects.create_message(msg(), fake_delivery=True)

    assert MailDelivery.objects.count() == 2
    assert first[0].message_id != second[0].message_id
    assert first[0].message_id.startswith("fake-")
    assert first[0].esp_name == "Fake"
    assert first[0].state == MailDelivery.STATE_DELIVERED
    assert first[0].success is True


def test_fake_delivery_honours_explicit_message_id():
    message = EmailMessage(
        subject="Hi",
        body="Body",
        from_email="sender@example.com",
        to=[RECIPIENT],
        headers={"message_id": "custom-id"},
    )

    (d,) = MailDelivery.objects.create_message(message, fake_delivery=True)

    assert d.message_id == "custom-id"


# --- admin log entries --------------------------------------------------------


def test_admin_log_entry_written_on_state_change(ses_message_id, monkeypatch):
    user = get_user_model().objects.create(username="tracker")
    monkeypatch.setattr(signals, "ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID", user.pk)
    fire_post_send(ses_message_id)

    post_sns_event("Delivery", ses_message_id, timestamp=at(5))
    post_sns_event("Delivery", ses_message_id, timestamp=at(4))  # older, does not change the state

    entries = LogEntry.objects.filter(user=user)
    assert entries.count() == 1
    assert entries.get().change_message == "Status updated from queued to delivered"


def test_admin_log_entry_integrity_error_is_logged(ses_message_id, monkeypatch, caplog):
    import logging

    user = get_user_model().objects.create(username="tracker")
    monkeypatch.setattr(signals, "ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID", user.pk)

    def boom(**kwargs):
        raise IntegrityError("fk")

    monkeypatch.setattr(LogEntry.objects, "create", boom)
    fire_post_send(ses_message_id)

    with caplog.at_level(logging.ERROR, logger="anymail_status_tracker"):
        post_sns_event("Delivery", ses_message_id, timestamp=at(5))

    assert any("Wrongly configured ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID" in r.getMessage() for r in caplog.records)


def test_tracking_event_info_log_and_querydict_esp_event(ses_message_id, monkeypatch, caplog):
    import logging

    from django.http import QueryDict

    from anymail.signals import AnymailTrackingEvent, tracking

    monkeypatch.setattr(signals, "ANYMAIL_STATUS_TRACKER_LOG_TRACKING_EVENT", True)
    fire_post_send(ses_message_id)

    event = AnymailTrackingEvent(
        event_type=MailDelivery.STATE_DELIVERED,
        timestamp=at(5),
        event_id="qd-1",
        message_id=ses_message_id,
        recipient=RECIPIENT,
        esp_event=QueryDict("k=1&k=2"),
    )
    with caplog.at_level(logging.INFO, logger="anymail_status_tracker"):
        tracking.send(sender=object, event=event, esp_name=SES)

    stored = MailDeliveryEvent.objects.get(event_id="qd-1")
    assert stored.esp_event == {"k": ["1", "2"]}
    assert any("Tracking event from" in r.getMessage() for r in caplog.records)


def test_normalize_recipient_edge_cases():
    assert signals._normalize_recipient("") == ""
    assert signals._normalize_recipient("not an email@@@") == "not an email@@@"


def test_tracking_uses_same_message_id_normalization_as_post_send():
    """Falsy ESP message ids (e.g. int 0 from Anymail's test backend) must not
    become "" in the event log while post_send stored "0" — that breaks the
    natural key and leaves the event orphaned."""
    from anymail.signals import AnymailTrackingEvent, tracking

    fire_post_send(0)
    delivery = MailDelivery.objects.get()
    assert delivery.message_id == "0"

    tracking.send(
        sender=object,
        event=AnymailTrackingEvent(
            event_type=MailDelivery.STATE_DELIVERED,
            timestamp=at(5),
            event_id="norm-0",
            message_id=0,
            recipient=RECIPIENT,
        ),
        esp_name=SES,
    )

    event = MailDeliveryEvent.objects.get(event_id="norm-0")
    assert event.message_id == "0"
    assert not MailDeliveryEvent.objects.orphans().filter(pk=event.pk).exists()
    assert delivery.state == MailDelivery.STATE_DELIVERED


def test_tracking_empty_message_id_gets_placeholder_not_empty_string():
    from anymail.signals import AnymailTrackingEvent, tracking

    tracking.send(
        sender=object,
        event=AnymailTrackingEvent(
            event_type=MailDelivery.STATE_DELIVERED,
            timestamp=at(5),
            event_id="norm-empty",
            message_id=None,
            recipient=RECIPIENT,
        ),
        esp_name=SES,
    )

    event = MailDeliveryEvent.objects.get(event_id="norm-empty")
    assert event.message_id.startswith(signals.NO_MESSAGE_ID_PREFIX + "-")
    assert event.message_id != ""
