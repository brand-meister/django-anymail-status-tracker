# django-anymail-status-tracker
A simple wrapper around django-anymail to receive and persist e-mail status event data.

Every recipient of a sent e-mail gets a `MailDelivery` row identifying that delivery. Every tracking
notification the ESP sends (delivered, bounced, opened, ...) is stored in an append-only `MailDeliveryEvent`
log. The current state of a delivery is **derived from the log** when you read it, so there is nothing to
keep in sync and nothing that can be lost when webhooks arrive early, twice or out of order.


## Requirements

- [Django](https://www.djangoproject.com) version 4.2+
- [django-anymail](https://anymail.dev) 13.1+


## Quick start

1. Add `anymail_status_tracker` to your INSTALLED_APPS setting like this:

    ```python
    INSTALLED_APPS = [
        # ...
        'anymail_status_tracker',
    ]
    ```

2. Run `python manage.py migrate` to create the models.

3. [Setup](https://anymail.dev/en/stable/installation/) `django-anymail`

4. [Configure Webhooks](https://anymail.dev/en/stable/installation/#configuring-tracking-and-inbound-webhooks)

5. [Configure Django E-mail Backend](https://anymail.dev/en/v3.0/installation/#configuring-django-s-email-backend)


## Usage

Send mail the normal Django way. The created `MailDelivery` rows are attached to the message after `send()`:

```python
from django.core.mail import EmailMessage

message = EmailMessage(subject="Hi", body="...", to=["someone@example.com"])
message.send()

for delivery in message.mail_deliveries:
    print(delivery.recipient, delivery.state, delivery.message_id)
```

`MailDelivery.objects.create_message(message)` does the same and returns the rows directly.
`create_message(message, fake_delivery=True)` skips sending and records the recipients as delivered
(useful in development).

### Reading the state

`MailDelivery` itself only stores `esp_name`, `message_id`, `recipient` and `sent_at`. Its state is derived
from the event log:

```python
delivery = MailDelivery.objects.get(...)
delivery.state            # "opened", "bounced", ... or "unknown" if no event has been logged yet
delivery.get_state_display()
delivery.state_timestamp  # when the ESP reported the current state
delivery.success          # True / False / None (queued, sent, deferred, unknown)
delivery.latest_event     # the MailDeliveryEvent that defines the state: mta_response, reject_reason, ...
for event in delivery.events:  # full history, oldest first
    print(event.timestamp, event.event_type, event.description)
```

On a plain instance the first access to any of these runs one query, cached on the instance. For lists, use
the queryset so the state is computed in the same query as the rows (one correlated subquery, no N+1):

```python
MailDelivery.objects.with_state()                          # annotates state, state_timestamp, latest_event_id
MailDelivery.objects.with_state().order_by("-state_timestamp")
MailDelivery.objects.filter_state(MailDelivery.STATE_BOUNCED, MailDelivery.STATE_COMPLAINED)
MailDelivery.objects.exclude_state(MailDelivery.STATE_QUEUED)
```

Annotated values are a snapshot: `delivery.refresh_from_db()` clears them.


## How it works

```
message.send() ──► post_send ──► MailDelivery row (identity)
                                 MailDeliveryEvent row (baseline, e.g. "queued")
ESP webhook    ──► tracking  ──► MailDeliveryEvent row

read           ──► with_state() / delivery.state ──► "winning" event per (esp_name, message_id, recipient)
```

- `MailDelivery` is one **identity** per recipient, unique on `(esp_name, message_id, recipient)`.
- `MailDeliveryEvent` is the **append-only log** of everything the ESP reported, unique on the ESP's
  notification id (`event_id`), so redelivered webhooks are recorded once.
- The two are matched by the natural key, **not** a foreign key. A webhook may arrive before the
  `MailDelivery` row is committed (for example when mail is sent inside a long database transaction). The
  event is stored regardless and counts as soon as the row is visible. The webhook handler never reads for
  update, never waits and never retries, so it cannot tie up request workers or fail on timing.

### State rule

The event that defines a delivery's state is chosen with rules that give the same answer regardless of the
order in which notifications arrived:

1. **Terminal negative** events (`bounced`, `complained`, `rejected`, `failed`) beat all others, even if a
   soft event (`delivered`, `opened`, `clicked`, ...) is newer. Among terminal negatives the newest wins.
2. Otherwise the event with the **latest ESP timestamp** wins; an event that arrives late is logged but does
   not regress the state.
3. The baseline written by `post_send` (any status the ESP returned on send) is normally lower priority than
   an ESP-reported event, even though our clock may stamp it later than the ESP's notification. Exception: a
   terminal-negative baseline (`failed`, and the other terminal negatives) still beats a soft ESP event such
   as `delivered`; non-terminal baselines (`queued`, `sent`, …) never do.
4. Events without an ESP timestamp use the time they were received.

### Orphan events

Events with no matching `MailDelivery` (mail sent by another system through the same ESP configuration, or a
send whose transaction has not committed yet) are kept and logged at `INFO` level. The admin lists them under
*Mail Delivery Events* with the *Orphan (no delivery)* filter; `MailDeliveryEvent.objects.orphans()` returns
them.


## Sending mail inside transactions

The tracker copes with mail sent inside `transaction.atomic()` blocks: events that arrive meanwhile are kept and
counted as soon as the transaction commits. If you send many mails in one transaction, consider sending after
the commit instead so that the delivery rows are visible immediately:

```python
from django.db import transaction

with transaction.atomic():
    ...
    transaction.on_commit(lambda: message.send())
```


## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `ANYMAIL_STATUS_TRACKER_LOG_ACTION_USER_ID` | `None` | If set, an admin `LogEntry` is written by this user id whenever a tracking event changes a delivery's state. |
| `ANYMAIL_STATUS_TRACKER_LOG_TRACKING_EVENT` | `False` | Log every incoming tracking event (normalised fields plus raw ESP payload) at `INFO` level. |
| `ANYMAIL_STATUS_TRACKER_DEBUG_BACKEND` | `None` | With `DEBUG=True`, `create_message()` sends through this backend instead of the configured one. |


## Upgrading

### From 1.x to 2.0

- Run `python manage.py migrate`. No `MailDelivery` row is deleted:
  - `0003` makes `(esp_name, message_id, recipient)` unique: legacy `NO_MESSAGE_ID` placeholders are rewritten to
    `NO_MESSAGE_ID-<uuid>` and, should any true duplicates exist, the older rows to `<message_id>#dup-<uuid>`.
    It then copies every row's status columns into one `legacy:<pk>` event, so the state you see afterwards is the
    state you had.
  - `0004` adds the constraint and **drops the status columns** (`state`, `timestamp`, `metadata`, `reject_reason`,
    `description`, `mta_response`, `user_agent`, `click_url`, `esp_event`, `updated_at`). Take a backup first;
    reversing `0004` recreates them empty.
- Code changes:
  - `delivery.state`, `state_timestamp`, `success` and `get_state_display()` still work on single instances but now
    query the event log. Use `MailDelivery.objects.with_state()` for lists and ordering, and `filter_state()`
    instead of `.filter(state=...)`.
  - `delivery.mta_response`, `reject_reason`, `esp_event`, ... moved to `delivery.latest_event`.
  - `updated_at` is gone; use `state_timestamp` or `latest_event.received_at`.
  - `state` is `"unknown"` (not `"queued"`) for a delivery without any event.
  - `success` is `None` for pending states (`queued`, `sent`, `deferred`, `unknown`). In 1.x only
    `sent` was `None`; `queued`, `deferred` and `unknown` were `False`. Only `delivered` is `True`,
    as before (`opened`, `clicked`, ... remain `False`).
- `ANYMAIL_STATUS_TRACKER_TRACKING_RETRY_DELAYS` is gone; delete it from your settings if you still have it.
- `create_message(fake_delivery=True)` now records `esp_name="Fake"` and a unique `fake-<uuid>` message id
  per call instead of `NO_MESSAGE_ID`.
- `handle_post_send` never raises into your `send()` call any more; failures are logged instead.


## Debugging webhooks locally

```bash
# Create a delivery and send it a Delivery notification
python manage.py simulate_sns_event --event Delivery

# Deliver the same SNS notification three times (recorded once)
python manage.py simulate_sns_event --event Bounce --repeat 3

# Store an event without a delivery (webhook faster than post_send)
python manage.py simulate_sns_event --event Send --orphan
```

The example project (`python manage.py runserver`, then open `/`) creates an Amazon SES
`MailDelivery` and simulates the chosen SNS notification for it (no real send). Each listed
delivery has an **Add event** control to fire further notifications against the same
`message_id`.


## Development setup

1. Install development dependencies:

    ```bash
    uv sync
    ```

2. Install the [ruff extension](https://docs.astral.sh/ruff/integrations/) for code linting & formatting in your IDE.

3. Run the test-suite:

    ```bash
    uv run pytest
    ```

4. (Optional) Override settings in `example_proj/settings_local.py` as required.
