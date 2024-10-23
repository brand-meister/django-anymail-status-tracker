import importlib.util
import json

from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef
from django.utils.dateparse import parse_datetime

from anymail.signals import RejectReason

from anymail_status_tracker.models import MailDelivery


EVENT_TYPES = {
    0: ("Send", "sent"),
    1: ("Delivery", "delivered"),
    2: ("Bounce", "bounced"),
    3: ("Complaint", "complained"),
}


class Command(BaseCommand):
    help = "Migrate mail tracking data from django-ses-tracker package to django-anymail-status-tracker"

    def create_mail_delivery_from_ses_mail(self, mail_info):
        """Create a MailDelivery object from a SESMailDelivery object"""

        timestamp_raw = mail_info.state_data.get("timestamp")
        timestamp = parse_datetime(timestamp_raw) if timestamp_raw else None
        state = EVENT_TYPES[mail_info.state][1]
        event_type = EVENT_TYPES[mail_info.state][0]
        metadata = {}
        tags = []
        for header in mail_info.mail_data.get("headers", []):
            name = header["name"].lower()
            if name == "x-tag":
                tags.append(header["value"])
            elif name == "x-metadata":
                try:
                    metadata = json.loads(header["value"])
                except (ValueError, TypeError, KeyError):
                    pass
        reject_reason = None
        mta_response = None
        description = None
        user_agent = None
        # Some fields are dependent on the state of the mail
        if state == "bounced":
            reject_reason = RejectReason.BOUNCED
            bounce_type = mail_info.state_data.get("bounceType", "")
            bounce_sub_type = mail_info.state_data.get("bounceSubType", "")
            if bounce_type or bounce_sub_type:
                description = f"{bounce_type}.{bounce_sub_type}"
            for bounced_recipient in mail_info.state_data.get("bouncedRecipients", []):
                if bounced_recipient.get("emailAddress") == mail_info.recipient:
                    mta_response = bounced_recipient.get("diagnosticCode")
                    continue
        elif state == "complained":
            reject_reason = RejectReason.SPAM
            description = mail_info.state_data.get("complaintFeedbackType")
            user_agent = mail_info.state_data.get("userAgent")
        elif state == "delivered":
            mta_response = mail_info.state_data.get("smtpResponse")

        esp_event = {
            "eventType": event_type,
            "timestamp": timestamp_raw,
            "tags": tags,
            "mail": mail_info.mail_data,
            event_type.lower(): mail_info.state_data,
        }

        return MailDelivery(
            esp_name="Amazon SES",
            message_id=mail_info.message_id,
            recipient=mail_info.recipient,
            state=state,
            timestamp=timestamp,
            metadata=metadata,
            reject_reason=reject_reason,
            description=description,
            mta_response=mta_response,
            user_agent=user_agent,
            esp_event=esp_event,
            sent_at=mail_info.sent_at,
            updated_at=mail_info.updated_at,
        )

    def handle(self, *args, **options):
        """
        Migrate mail tracking data from django-ses-tracker package to django-anymail-status-tracker

        This command will check if the package ses_sns_tracker is installed and if so, it will migrate the data
        from SESMailDelivery to MailDelivery.
        """
        if importlib.util.find_spec("ses_sns_tracker"):
            from ses_sns_tracker.models import SESMailDelivery  # noqa T201

            mail_base_infos = SESMailDelivery.objects.filter(
                ~Exists(MailDelivery.objects.filter(message_id=OuterRef("message_id"), recipient=OuterRef("recipient")))
            ).order_by("id")
            mail_deliveries = []
            for mail_info in mail_base_infos:
                mail_delivery = self.create_mail_delivery_from_ses_mail(mail_info)
                mail_deliveries.append(mail_delivery)

            MailDelivery.objects.bulk_create(mail_deliveries)
        else:
            print("Package ses_sns_tracker is not installed")
            return
