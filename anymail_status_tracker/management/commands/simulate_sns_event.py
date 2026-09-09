from django.core.management.base import BaseCommand

from anymail_status_tracker.debug.data import DEFAULT_EMAIL, EVENT_TYPES
from anymail_status_tracker.debug.helpers import simulate_sns_event


class Command(BaseCommand):
    help = (
        "Simulate an SNS webhook event. Without --message_id, a self-contained MailDelivery is created "
        "(or, with --orphan, only the event is stored)."
    )

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
        parser.add_argument(
            "--repeat",
            type=int,
            default=1,
            help="Deliver the same SNS notification N times to demonstrate idempotent handling (default: 1)",
        )
        parser.add_argument(
            "--orphan",
            action="store_true",
            help="Do not create a MailDelivery; store the event as an orphan (webhook before post_send)",
        )

    def handle(self, *args, **options):
        event_type = options["event"]
        message_id = options["message_id"]
        email = options["email"]
        repeat = max(1, options["repeat"])

        sns_message_id = None
        for _ in range(repeat):
            message_id, sns_message_id = simulate_sns_event(
                event_type,
                email,
                message_id=message_id,
                sns_message_id=sns_message_id,
                create_delivery=False if options["orphan"] else None,
            )

        times = f" x{repeat} (same SNS MessageId)" if repeat > 1 else ""
        self.stdout.write(
            self.style.SUCCESS(f"Simulated {event_type} event{times} for message {message_id} to {email}")
        )
