from django.contrib import admin
from django.utils import timezone

from anymail_status_tracker.models import MailDelivery


def _format_datetime_ms(dt):
    if dt is None:
        return "-"
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    # %f is microseconds; trim to milliseconds
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


@admin.register(MailDelivery)
class MailDeliveryAdmin(admin.ModelAdmin):
    list_display = ("recipient", "message_id", "sent_at_ms", "updated_at_ms", "state")
    list_filter = ("state", "sent_at", "updated_at", "esp_name")
    search_fields = ("recipient", "message_id")
    date_hierarchy = "sent_at"
    fields = (
        "esp_name",
        "state",
        "message_id",
        "timestamp",
        "recipient",
        "metadata",
        "reject_reason",
        "description",
        "mta_response",
        "user_agent",
        "click_url",
        "esp_event",
        "sent_at_ms",
        "updated_at_ms",
    )
    readonly_fields = ("sent_at_ms", "updated_at_ms")
    ordering = ("-updated_at",)

    @admin.display(description="Sent at", ordering="sent_at")
    def sent_at_ms(self, obj):
        return _format_datetime_ms(obj.sent_at)

    @admin.display(description="Updated at", ordering="updated_at")
    def updated_at_ms(self, obj):
        return _format_datetime_ms(obj.updated_at)
