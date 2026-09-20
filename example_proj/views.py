from django.shortcuts import redirect, render

from anymail_status_tracker.debug.data import DEFAULT_EMAIL
from anymail_status_tracker.debug.helpers import simulate_sns_event
from anymail_status_tracker.models import MailDelivery, MailDeliveryEvent
from example_proj.forms import TestForm


def test_view(request):
    form = TestForm(request.POST or None)
    if form.is_valid():
        message_id = form.cleaned_data["message_id"] or None
        simulate_sns_event(
            form.cleaned_data["webhook_status_type"],
            email=form.cleaned_data["email"] or DEFAULT_EMAIL,
            message_id=message_id,
        )
        return redirect("test")

    deliveries = MailDelivery.objects.with_state().order_by("-state_timestamp", "-sent_at")
    events = MailDeliveryEvent.objects.all().order_by("-received_at")[:50]
    orphan_count = MailDeliveryEvent.objects.orphans().count()
    return render(
        request,
        "test.html",
        {
            "form": form,
            "deliveries": deliveries,
            "events": events,
            "orphan_count": orphan_count,
            "event_choices": TestForm.FORM_EVENT_TYPES,
        },
    )
