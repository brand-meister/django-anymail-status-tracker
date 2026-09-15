import json

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from anymail_status_tracker.admin.mail_delivery import _format_datetime_ms
from anymail_status_tracker.models import MailDelivery, MailDeliveryEvent


class HasDeliveryFilter(admin.SimpleListFilter):
    title = "matching delivery"
    parameter_name = "has_delivery"

    def lookups(self, request, model_admin):
        return (
            ("yes", "Has delivery"),
            ("no", "Orphan (no delivery)"),
        )

    def queryset(self, request, queryset):
        if self.value() == "no":
            return queryset.orphans()
        if self.value() == "yes":
            orphan_pks = queryset.orphans().values_list("pk", flat=True)
            return queryset.exclude(pk__in=orphan_pks)
        return queryset


@admin.register(MailDeliveryEvent)
class MailDeliveryEventAdmin(admin.ModelAdmin):
    """Read-only view of the append-only event log."""

    list_display = ("recipient", "message_id", "event_type", "timestamp_ms", "received_at_ms", "esp_name")
    list_filter = ("event_type", "esp_name", HasDeliveryFilter, "received_at")
    search_fields = ("recipient", "message_id", "event_id")
    date_hierarchy = "received_at"
    ordering = ("-received_at",)
    readonly_fields = tuple(
        "esp_event_pretty" if field.name == "esp_event" else field.name for field in MailDeliveryEvent._meta.fields
    ) + ("delivery_link",)
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="ESP timestamp", ordering="timestamp")
    def timestamp_ms(self, obj):
        return _format_datetime_ms(obj.timestamp)

    @admin.display(description="Received at", ordering="received_at")
    def received_at_ms(self, obj):
        return _format_datetime_ms(obj.received_at)

    @admin.display(description="ESP event")
    def esp_event_pretty(self, obj):
        return format_html(
            '<pre style="white-space:pre-wrap; max-width:80em; margin:0">{}</pre>',
            json.dumps(obj.esp_event or {}, indent=2, sort_keys=True, default=str),
        )

    @admin.display(description="Mail delivery")
    def delivery_link(self, obj):
        delivery = MailDelivery.objects.for_event(obj).first()
        if delivery is None:
            return "Orphan: no MailDelivery for this event (yet)."
        url = reverse("admin:anymail_status_tracker_maildelivery_change", args=[delivery.pk])
        return format_html('<a href="{}">{}</a>', url, delivery)
