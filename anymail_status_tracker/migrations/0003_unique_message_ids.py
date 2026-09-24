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

from django.db import connections, migrations, models
from django.db.models import Count, Exists, F, OuterRef, Value
from django.db.models.functions import Cast, Coalesce, Concat, Now


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


def make_message_ids_unique(MailDelivery, db_alias) -> dict:
    deliveries = MailDelivery.objects.using(db_alias)
    placeholders = [
        MailDelivery(pk=pk, message_id=f"{NO_MESSAGE_ID}-{uuid.uuid4()}")
        for pk in deliveries.filter(message_id=NO_MESSAGE_ID).values_list("pk", flat=True)
    ]
    deliveries.bulk_update(placeholders, ["message_id"], batch_size=BATCH_SIZE)

    duplicates = []
    duplicate_keys = deliveries.values(*NATURAL_KEY).annotate(n=Count("pk")).filter(n__gt=1).values_list(*NATURAL_KEY)
    for esp_name, message_id, recipient in list(duplicate_keys):
        pks = deliveries.filter(esp_name=esp_name, message_id=message_id, recipient=recipient).order_by(
            "-updated_at", "-pk"
        )
        duplicates += [
            MailDelivery(pk=pk, message_id=f"{message_id}#dup-{uuid.uuid4()}")
            for pk in pks.values_list("pk", flat=True)[1:]
        ]
    deliveries.bulk_update(duplicates, ["message_id"], batch_size=BATCH_SIZE)

    return {"placeholders_rewritten": len(placeholders), "duplicates_rewritten": len(duplicates)}


def snapshot_deliveries_to_events(MailDelivery, MailDeliveryEvent, db_alias) -> int:
    """
    ``timestamp`` becomes the old webhook timestamp, or ``sent_at`` if the
    delivery never received a webhook (so that a webhook delayed across the
    migration still ranks above the snapshot). ``received_at`` is the migration
    time. Idempotent: rows that already have a legacy event are skipped.

    Runs as a single INSERT ... SELECT so the rows never pass through Python.
    The SELECT is built with the ORM to stay portable across backends.
    """
    columns = {
        "esp_name": F("esp_name"),
        "message_id": F("message_id"),
        "recipient": F("recipient"),
        "event_id": Concat(Value(LEGACY_EVENT_ID_PREFIX), Cast("pk", models.CharField())),
        "event_type": F("state"),
        "timestamp": Coalesce("timestamp", "sent_at"),
        "received_at": Now(),
        "tags": Value([], output_field=models.JSONField()),
        **{field: F(field) for field in SNAPSHOT_FIELDS},
    }
    # Annotation names must not clash with MailDelivery's own field names.
    aliases = {f"snapshot_{column}": expression for column, expression in columns.items()}
    already_snapshotted = MailDeliveryEvent.objects.using(db_alias).filter(
        esp_name=OuterRef("esp_name"), event_id=OuterRef("snapshot_event_id")
    )
    select = (
        MailDelivery.objects.using(db_alias)
        .annotate(**aliases)
        .filter(~Exists(already_snapshotted))
        .values_list(*aliases)
    )
    # get_compiler(db_alias), not sql_with_params(): the latter always compiles for "default".
    select_sql, params = select.query.get_compiler(db_alias).as_sql()

    connection = connections[db_alias]
    quote = connection.ops.quote_name
    target = ", ".join(quote(MailDeliveryEvent._meta.get_field(column).column) for column in columns)
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {quote(MailDeliveryEvent._meta.db_table)} ({target}) {select_sql}", params)
        return cursor.rowcount


def forwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    MailDelivery = apps.get_model("anymail_status_tracker", "MailDelivery")
    MailDeliveryEvent = apps.get_model("anymail_status_tracker", "MailDeliveryEvent")
    make_message_ids_unique(MailDelivery, db_alias)
    snapshot_deliveries_to_events(MailDelivery, MailDeliveryEvent, db_alias)


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


def restore_state_from_events(MailDelivery, MailDeliveryEvent, db_alias) -> int:
    """Write the winning event of each delivery back into the status columns."""
    deliveries_qs = MailDelivery.objects.using(db_alias)
    events_qs = MailDeliveryEvent.objects.using(db_alias)
    restored = 0
    pks = list(deliveries_qs.order_by("pk").values_list("pk", flat=True))
    for start in range(0, len(pks), BATCH_SIZE):
        deliveries = list(deliveries_qs.filter(pk__in=pks[start : start + BATCH_SIZE]))

        events_by_key = defaultdict(list)
        esp_names = {d.esp_name for d in deliveries}
        message_ids = {d.message_id for d in deliveries}
        for event in events_qs.filter(esp_name__in=esp_names, message_id__in=message_ids):
            events_by_key[(event.esp_name, event.message_id, event.recipient)].append(event)

        for delivery in deliveries:
            events = events_by_key.get((delivery.esp_name, delivery.message_id, delivery.recipient))
            if not events:
                continue
            winner = max(events, key=_rank)
            is_snapshot = winner.event_id == f"{LEGACY_EVENT_ID_PREFIX}{delivery.pk}"
            # bulk_update() is not used on purpose: its CASE WHEN per field and row is
            # quadratic per batch and takes hours on a few 100k rows.
            deliveries_qs.filter(pk=delivery.pk).update(
                state=winner.event_type,
                # The snapshot used sent_at as a stand-in for "never had a webhook".
                timestamp=None if is_snapshot and winner.timestamp == delivery.sent_at else winner.timestamp,
                **{field: getattr(winner, field) for field in SNAPSHOT_FIELDS},
            )
            restored += 1
    return restored


def restore_message_ids(MailDelivery, MailDeliveryEvent, db_alias) -> int:
    deliveries = MailDelivery.objects.using(db_alias)
    events = MailDeliveryEvent.objects.using(db_alias)
    restored = 0
    candidates = deliveries.filter(message_id__startswith=f"{NO_MESSAGE_ID}-") | deliveries.filter(
        message_id__contains="#dup-"
    )
    for pk, esp_name, message_id in list(candidates.values_list("pk", "esp_name", "message_id")):
        if PLACEHOLDER_RE.match(message_id):
            original = NO_MESSAGE_ID
        elif match := DUPLICATE_RE.match(message_id):
            original = match["message_id"]
        else:
            continue
        deliveries.filter(pk=pk).update(message_id=original)
        events.filter(esp_name=esp_name, message_id=message_id).update(message_id=original)
        restored += 1
    return restored


def backwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    MailDelivery = apps.get_model("anymail_status_tracker", "MailDelivery")
    MailDeliveryEvent = apps.get_model("anymail_status_tracker", "MailDeliveryEvent")
    restore_state_from_events(MailDelivery, MailDeliveryEvent, db_alias)
    restore_message_ids(MailDelivery, MailDeliveryEvent, db_alias)
    MailDeliveryEvent.objects.using(db_alias).filter(event_id__startswith=LEGACY_EVENT_ID_PREFIX).delete()


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
        # Without it, every per-key lookup in RunPython is a full table scan.
        # 0004 adds the permanent unique constraint on the same columns.
        migrations.AddIndex(
            model_name="maildelivery",
            index=models.Index(fields=["esp_name", "message_id", "recipient"], name="maildelivery_tmp_key_idx"),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveIndex(model_name="maildelivery", name="maildelivery_tmp_key_idx"),
    ]
