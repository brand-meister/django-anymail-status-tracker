"""
Make MailDelivery a pure identity row.

Adds the natural-key constraint and removes all status columns. Their content
was copied into "legacy:<pk>" MailDeliveryEvent rows by 0003; from here on the
state is derived from the event log (MailDeliveryQuerySet.with_state).

Reversible: unapplying re-creates the columns (empty), after which the reverse
of 0003 fills them from the event log again.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("anymail_status_tracker", "0003_unique_message_ids"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="maildelivery",
            constraint=models.UniqueConstraint(
                fields=("esp_name", "message_id", "recipient"),
                name="maildelivery_unique_esp_message_recipient",
            ),
        ),
        # "state" is NOT NULL without a default, so the reverse of RemoveField could
        # not re-add it on a populated table. Giving it a default first makes the
        # removal reversible; the reverse of 0003 then fills in the real values.
        migrations.AlterField(
            model_name="maildelivery",
            name="state",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("sent", "Sent"),
                    ("rejected", "Rejected"),
                    ("failed", "Failed"),
                    ("bounced", "Bounced"),
                    ("deferred", "Deferred"),
                    ("delivered", "Delivered"),
                    ("autoresponded", "Autoresponded"),
                    ("opened", "Opened"),
                    ("clicked", "Clicked"),
                    ("complained", "Complained"),
                    ("unsubscribed", "Unsubscribed"),
                    ("subscribed", "Subscribed"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                max_length=32,
            ),
        ),
        migrations.RemoveField(model_name="maildelivery", name="state"),
        migrations.RemoveField(model_name="maildelivery", name="timestamp"),
        migrations.RemoveField(model_name="maildelivery", name="metadata"),
        migrations.RemoveField(model_name="maildelivery", name="reject_reason"),
        migrations.RemoveField(model_name="maildelivery", name="description"),
        migrations.RemoveField(model_name="maildelivery", name="mta_response"),
        migrations.RemoveField(model_name="maildelivery", name="user_agent"),
        migrations.RemoveField(model_name="maildelivery", name="click_url"),
        migrations.RemoveField(model_name="maildelivery", name="esp_event"),
        migrations.RemoveField(model_name="maildelivery", name="updated_at"),
    ]
