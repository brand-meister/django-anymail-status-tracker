import uuid


DEFAULT_EMAIL = "recipient@example.com"

EVENT_TYPES = ("Send", "Delivery", "Open", "Bounce", "Complaint")


def _generate_message_id():
    return str(uuid.uuid4())


def _build_mail_object(email, message_id):
    """Common mail object shared across all SES notification types."""
    return {
        "timestamp": "2016-01-27T14:59:38.237Z",
        "source": "sender@example.com",
        "sourceArn": "arn:aws:ses:us-east-1:123456789012:identity/example.com",
        "sourceIp": "127.0.3.0",
        "sendingAccountId": "123456789012",
        "messageId": message_id,
        "destination": [email],
        "headersTruncated": False,
        "headers": [
            {"name": "From", "value": "sender@example.com"},
            {"name": "To", "value": email},
            {"name": "Subject", "value": "Test Email"},
            {"name": "Content-Type", "value": 'text/plain; charset="UTF-8"'},
            {"name": "Date", "value": "Wed, 27 Jan 2016 14:05:45 +0000"},
            {"name": "Message-ID", "value": message_id},
        ],
        "commonHeaders": {
            "from": ["sender@example.com"],
            "date": "Wed, 27 Jan 2016 14:05:45 +0000",
            "to": [email],
            "messageId": message_id,
            "subject": "Test Email",
        },
    }


def get_bounce_event(email=DEFAULT_EMAIL, message_id=None):
    message_id = message_id or _generate_message_id()
    return {
        "notificationType": "Bounce",
        "bounce": {
            "bounceType": "Permanent",
            "bounceSubType": "General",
            "bouncedRecipients": [
                {
                    "emailAddress": email,
                    "status": "5.1.1",
                    "action": "failed",
                    "diagnosticCode": "smtp; 550 5.1.1 User unknown",
                }
            ],
            "timestamp": "2016-01-27T14:59:44.101Z",
            "feedbackId": str(uuid.uuid4()),
            "reportingMTA": "dns; email.example.com",
            "remoteMtaIp": "127.0.2.0",
        },
        "mail": _build_mail_object(email, message_id),
    }


def get_complaint_event(email=DEFAULT_EMAIL, message_id=None):
    message_id = message_id or _generate_message_id()
    return {
        "notificationType": "Complaint",
        "complaint": {
            "userAgent": "AnyCompany Feedback Loop (V0.01)",
            "complainedRecipients": [{"emailAddress": email}],
            "complaintFeedbackType": "abuse",
        },
        "mail": _build_mail_object(email, message_id),
    }


def get_delivery_event(email=DEFAULT_EMAIL, message_id=None):
    message_id = message_id or _generate_message_id()
    return {
        "notificationType": "Delivery",
        "delivery": {
            "timestamp": "2016-01-27T14:59:38.237Z",
            "recipients": [email],
            "processingTimeMillis": 546,
            "reportingMTA": "a8-70.smtp-out.amazonses.com",
            "smtpResponse": "250 ok: Message 64111812 accepted",
            "remoteMtaIp": "127.0.2.0",
        },
        "mail": _build_mail_object(email, message_id),
    }


def get_send_event(email=DEFAULT_EMAIL, message_id=None):
    # SES "event publishing" format (eventType), emitted as soon as SES accepts the send.
    message_id = message_id or _generate_message_id()
    return {
        "eventType": "Send",
        "send": {},
        "mail": _build_mail_object(email, message_id),
    }


def get_open_event(email=DEFAULT_EMAIL, message_id=None):
    message_id = message_id or _generate_message_id()
    return {
        "eventType": "Open",
        "open": {
            "ipAddress": "192.0.2.1",
            "timestamp": "2016-01-27T15:01:02.000Z",
            "userAgent": "Mozilla/5.0 (Example)",
        },
        "mail": _build_mail_object(email, message_id),
    }


EVENT_BUILDERS = {
    "Send": get_send_event,
    "Delivery": get_delivery_event,
    "Open": get_open_event,
    "Bounce": get_bounce_event,
    "Complaint": get_complaint_event,
}
