"""
Data migration moving the delivery status from MailDelivery into the
MailDeliveryEvent log.

Forwards
    1. Widen message_id to 300 so "<message_id>#dup-<uuid4>" fits when the
       original is already 255 characters (the previous column limit).
    2. Make (esp_name, message_id, recipient) unique without deleting any row
       (the constraint itself is added in 0004):
       * "NO_MESSAGE_ID" placeholders become "NO_MESSAGE_ID-<uuid4>".
       * Remaining duplicates keep the most recently updated row's message_id
         and rewrite the older rows to "<message_id>#dup-<uuid4>".
    3. Snapshot every row's status columns into one "legacy:<pk>" event so the
       state is preserved when 0004 drops those columns.

Backwards (runs after 0004 has re-created the, then empty, status columns)
    1. Write the event that currently defines each delivery's state back into
       the status columns. Events received after the forward migration are
       therefore not lost on rollback.
    2. Undo the message_id rewrites.
    3. Delete the "legacy:<pk>" snapshot events.
"""

import re
import uuid
from collections import defaultdict

from django.db import migrations, models
from django.db.models import Count


NO_MESSAGE_ID = "NO_MESSAGE_ID"
NATURAL_KEY = ("esp_name", "message_id", "recipient")
LEGACY_EVENT_ID_PREFIX = "legacy:"
BATCH_SIZE = 1000

UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
PLACEHOLDER_RE = re.compile(rf"^{NO_MESSAGE_ID}-{UUID_RE}$")
DUPLICATE_RE = re.compile(rf"^(?P<message_id>.+)#dup-{UUID_RE}$")

# Kept in sync with MailDelivery.TERMINAL_NEGATIVE_STATES and
# MailDeliveryEvent.POST_SEND_EVENT_ID_PREFIX. Copied on purpose: a migration
# must not depend on the current model code.
TERMINAL_NEGATIVE_STATES = frozenset(("rejected", "failed", "bounced", "complained"))
POST_SEND_EVENT_ID_PREFIX = "post_send:"

# Status columns copied 1:1 between MailDelivery (until 0004) and MailDeliveryEvent.
SNAPSHOT_FIELDS = (
    "metadata",
    "reject_reason",
    "description",
    "mta_response",
    "user_agent",
    "click_url",
    "esp_event",
)


# --- forwards -----------------------------------------------------------------


def make_message_ids_unique(MailDelivery) -> dict:
    placeholders_rewritten = 0
    for pk in MailDelivery.objects.filter(message_id=NO_MESSAGE_ID).values_list("pk", flat=True).iterator():
        MailDelivery.objects.filter(pk=pk).update(message_id=f"{NO_MESSAGE_ID}-{uuid.uuid4()}")
        placeholders_rewritten += 1

    duplicates_rewritten = 0
    duplicate_keys = (
        MailDelivery.objects.values(*NATURAL_KEY).annotate(n=Count("pk")).filter(n__gt=1).values_list(*NATURAL_KEY)
    )
    for esp_name, message_id, recipient in list(duplicate_keys):
        rows = MailDelivery.objects.filter(esp_name=esp_name, message_id=message_id, recipient=recipient).order_by(
            "-updated_at", "-pk"
        )
        for row in rows[1:]:
            MailDelivery.objects.filter(pk=row.pk).update(message_id=f"{message_id}#dup-{uuid.uuid4()}")
            duplicates_rewritten += 1

    return {"placeholders_rewritten": placeholders_rewritten, "duplicates_rewritten": duplicates_rewritten}


def snapshot_deliveries_to_events(MailDelivery, MailDeliveryEvent) -> int:
    """
    ``timestamp`` becomes the old webhook timestamp, or ``sent_at`` if the
    delivery never received a webhook (so that a webhook delayed across the
    migration still ranks above the snapshot). ``received_at`` is the migration
    time. Idempotent: rows that already have a legacy event are skipped.
    """
    existing = set(
        MailDeliveryEvent.objects.filter(event_id__startswith=LEGACY_EVENT_ID_PREFIX).values_list("event_id", flat=True)
    )

    created = 0
    batch = []
    for delivery in MailDelivery.objects.order_by("pk").iterator(chunk_size=BATCH_SIZE):
        event_id = f"{LEGACY_EVENT_ID_PREFIX}{delivery.pk}"
        if event_id in existing:
            continue
        batch.append(
            MailDeliveryEvent(
                esp_name=delivery.esp_name,
                message_id=delivery.message_id,
                recipient=delivery.recipient,
                event_id=event_id,
                event_type=delivery.state,
                timestamp=delivery.timestamp or delivery.sent_at,
                **{field: getattr(delivery, field) for field in SNAPSHOT_FIELDS},
            )
        )
        if len(batch) >= BATCH_SIZE:
            MailDeliveryEvent.objects.bulk_create(batch)
            created += len(batch)
            batch = []
    if batch:
        MailDeliveryEvent.objects.bulk_create(batch)
        created += len(batch)
    return created


def forwards(apps, schema_editor):
    MailDelivery = apps.get_model("anymail_status_tracker", "MailDelivery")
    MailDeliveryEvent = apps.get_model("anymail_status_tracker", "MailDeliveryEvent")
    make_message_ids_unique(MailDelivery)
    snapshot_deliveries_to_events(MailDelivery, MailDeliveryEvent)


# --- backwards ----------------------------------------------------------------


def _rank(event):
    """Same ordering as MailDeliveryEventQuerySet.ranked(); the max() is the winner."""
    return (
        event.event_type in TERMINAL_NEGATIVE_STATES,
        not str(event.event_id).startswith(POST_SEND_EVENT_ID_PREFIX),
        event.timestamp or event.received_at,
        event.received_at,
        event.pk,
    )


def restore_state_from_events(MailDelivery, MailDeliveryEvent) -> int:
    """Write the winning event of each delivery back into the status columns."""
    restored = 0
    pks = list(MailDelivery.objects.order_by("pk").values_list("pk", flat=True))
    for start in range(0, len(pks), BATCH_SIZE):
        deliveries = list(MailDelivery.objects.filter(pk__in=pks[start : start + BATCH_SIZE]))

        events_by_key = defaultdict(list)
        message_ids = {d.message_id for d in deliveries}
        for event in MailDeliveryEvent.objects.filter(message_id__in=message_ids):
            events_by_key[(event.esp_name, event.message_id, event.recipient)].append(event)

        to_update = []
        for delivery in deliveries:
            events = events_by_key.get((delivery.esp_name, delivery.message_id, delivery.recipient))
            if not events:
                continue
            winner = max(events, key=_rank)
            delivery.state = winner.event_type
            is_snapshot = winner.event_id == f"{LEGACY_EVENT_ID_PREFIX}{delivery.pk}"
            # The snapshot used sent_at as a stand-in for "never had a webhook".
            delivery.timestamp = None if is_snapshot and winner.timestamp == delivery.sent_at else winner.timestamp
            for field in SNAPSHOT_FIELDS:
                setattr(delivery, field, getattr(winner, field))
            to_update.append(delivery)

        MailDelivery.objects.bulk_update(to_update, ["state", "timestamp", *SNAPSHOT_FIELDS])
        restored += len(to_update)
    return restored


def restore_message_ids(MailDelivery, MailDeliveryEvent) -> int:
    restored = 0
    candidates = MailDelivery.objects.filter(message_id__startswith=f"{NO_MESSAGE_ID}-") | MailDelivery.objects.filter(
        message_id__contains="#dup-"
    )
    for pk, message_id in candidates.values_list("pk", "message_id").iterator():
        if PLACEHOLDER_RE.match(message_id):
            original = NO_MESSAGE_ID
        elif match := DUPLICATE_RE.match(message_id):
            original = match["message_id"]
        else:
            continue
        MailDelivery.objects.filter(pk=pk).update(message_id=original)
        MailDeliveryEvent.objects.filter(message_id=message_id).update(message_id=original)
        restored += 1
    return restored


def backwards(apps, schema_editor):
    MailDelivery = apps.get_model("anymail_status_tracker", "MailDelivery")
    MailDeliveryEvent = apps.get_model("anymail_status_tracker", "MailDeliveryEvent")
    restore_state_from_events(MailDelivery, MailDeliveryEvent)
    restore_message_ids(MailDelivery, MailDeliveryEvent)
    MailDeliveryEvent.objects.filter(event_id__startswith=LEGACY_EVENT_ID_PREFIX).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("anymail_status_tracker", "0002_maildeliveryevent"),
    ]

    operations = [
        # Must run before the rewrite: reverse then restores ids to <=255 before
        # shrinking the column back.
        migrations.AlterField(
            model_name="maildelivery",
            name="message_id",
            field=models.CharField(max_length=300),
        ),
        migrations.AlterField(
            model_name="maildeliveryevent",
            name="message_id",
            field=models.CharField(max_length=300),
        ),
        migrations.RunPython(forwards, backwards),
    ]
