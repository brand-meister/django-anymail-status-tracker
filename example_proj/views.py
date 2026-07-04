from django.shortcuts import redirect, render

from anymail_status_tracker.debug.helpers import send_test_email, simulate_sns_event
from anymail_status_tracker.models import MailDelivery
from example_proj.forms import TestForm


def test_view(request):
    form = TestForm(request.POST or None)
    if form.is_valid():
        webhook_status_type = form.cleaned_data["webhook_status_type"]
        message_id = send_test_email()
        simulate_sns_event(webhook_status_type, message_id=message_id)
        return redirect("test")

    deliveries = MailDelivery.objects.all().order_by("-updated_at")
    return render(request, "test.html", {"form": form, "deliveries": deliveries})
