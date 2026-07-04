from django.core.management.base import BaseCommand

from anymail_status_tracker.debug.data import DEFAULT_EMAIL, EVENT_TYPES
from anymail_status_tracker.debug.helpers import simulate_sns_event


class Command(BaseCommand):
    help = "Simulate an SNS webhook event. Without --message_id, a self-contained MailDelivery is created."

    def add_arguments(self, parser):
        parser.add_argument(
            "--event",
            type=str,
            required=True,
            choices=EVENT_TYPES,
            help="The type of SNS event to simulate",
        )
        parser.add_argument(
            "--message_id",
            type=str,
            default=None,
            help="The message_id of an existing MailDelivery to target",
        )
        parser.add_argument(
            "--email",
            type=str,
            default=DEFAULT_EMAIL,
            help="Recipient email address (default: %(default)s)",
        )

    def handle(self, *args, **options):
        event_type = options["event"]
        message_id = options["message_id"]
        email = options["email"]
        simulate_sns_event(event_type, email, message_id=message_id)
        target = f"message {message_id}" if message_id else f"{email} (new MailDelivery)"
        self.stdout.write(self.style.SUCCESS(f"Successfully simulated {event_type} event for {target}"))
