from anymail_status_tracker.models import MailDelivery, MailDeliveryEvent
from anymail_status_tracker.models.mail_delivery_event import subject_from_esp_event


def test_subject_from_ses_common_headers():
    assert (
        subject_from_esp_event(
            {
                "mail": {
                    "commonHeaders": {"subject": "Hello"},
                    "headers": [{"name": "Subject", "value": "Ignored"}],
                }
            }
        )
        == "Hello"
    )


def test_subject_from_ses_headers_list_when_common_headers_missing():
    payload = {
        "mail": {
            "headers": [
                {"name": "From", "value": "a@b.c"},
                {"name": "Subject", "value": "Via headers"},
            ]
        }
    }
    assert subject_from_esp_event(payload) == "Via headers"


def test_subject_missing_or_empty_payload():
    assert subject_from_esp_event({}) is None
    assert subject_from_esp_event({"mail": {"headers": [{"name": "Subject", "value": ""}]}}) is None
    assert subject_from_esp_event({"headers": [{"name": "From", "value": "a@b.c"}]}) is None
    assert subject_from_esp_event(None) is None


def test_is_terminal_negative():
    assert MailDeliveryEvent(event_type=MailDelivery.STATE_BOUNCED).is_terminal_negative is True
    assert MailDeliveryEvent(event_type=MailDelivery.STATE_DELIVERED).is_terminal_negative is False
