from django.contrib import admin
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from anymail_status_tracker.models import MailDelivery


def _format_datetime_ms(dt):
    if dt is None:
        return "-"
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    # %f is microseconds; trim to milliseconds
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class StateListFilter(admin.SimpleListFilter):
    """Filter on the derived state (an annotation, not a column)."""

    title = "state"
    parameter_name = "state"

    def lookups(self, request, model_admin):
        return MailDelivery.DELIVERY_STATES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(state=self.value())
        return queryset


@admin.register(MailDelivery)
class MailDeliveryAdmin(admin.ModelAdmin):
    list_display = ("recipient", "message_id", "sent_at_ms", "state_timestamp_ms", "state_display")
    list_filter = (StateListFilter, "sent_at", "esp_name")
    search_fields = ("recipient", "message_id")
    date_hierarchy = "sent_at"
    fields = (
        "esp_name",
        "message_id",
        "recipient",
        "sent_at_ms",
        "state_display",
        "state_timestamp_ms",
        "latest_event_details",
        "event_log",
    )
    readonly_fields = (
        "sent_at_ms",
        "state_display",
        "state_timestamp_ms",
        "latest_event_details",
        "event_log",
    )
    ordering = ("-sent_at",)

    def get_queryset(self, request):
        return super().get_queryset(request).with_state()

    @admin.display(description="Sent at", ordering="sent_at")
    def sent_at_ms(self, obj):
        return _format_datetime_ms(obj.sent_at)

    @admin.display(description="State", ordering="state")
    def state_display(self, obj):
        return obj.get_state_display()

    @admin.display(description="State since", ordering="state_timestamp")
    def state_timestamp_ms(self, obj):
        return _format_datetime_ms(obj.state_timestamp)

    @admin.display(description="Latest event")
    def latest_event_details(self, obj):
        event = obj.latest_event
        if event is None:
            return "No events logged yet."
        details = (
            ("Subject", event.subject),
            ("Event id", event.event_id),
            ("Description", event.description),
            ("Reject reason", event.get_reject_reason_display() if event.reject_reason else None),
            ("MTA response", event.mta_response),
            ("User agent", event.user_agent),
            ("Click URL", event.click_url),
            ("Metadata", event.metadata or None),
            ("Tags", ", ".join(t for t in event.tags if t) or None),
        )
        rows = format_html_join(
            "", "<tr><th style='text-align:left'>{}</th><td>{}</td></tr>", ((k, v) for k, v in details if v)
        )
        url = reverse("admin:anymail_status_tracker_maildeliveryevent_change", args=[event.pk])
        return format_html('<table>{}</table><a href="{}">Open event</a>', rows, url)

    @admin.display(description="Event log")
    def event_log(self, obj):
        if obj.pk is None:
            return "-"
        events = list(obj.events)
        if not events:
            return "No events logged yet."
        latest_pk = obj.latest_event.pk if obj.latest_event else None
        rows = format_html_join(
            "",
            '<tr><td>{}</td><td>{}{}</td><td>{}</td><td>{}</td><td><a href="{}">{}</a></td></tr>',
            (
                (
                    _format_datetime_ms(event.timestamp),
                    event.get_event_type_display(),
                    " (current)" if event.pk == latest_pk else "",
                    event.subject or "—",
                    _format_datetime_ms(event.received_at),
                    reverse("admin:anymail_status_tracker_maildeliveryevent_change", args=[event.pk]),
                    event.event_id,
                )
                for event in events
            ),
        )
        return format_html(
            "<table><thead><tr><th>ESP timestamp</th><th>Event</th><th>Subject</th>"
            "<th>Received</th><th>Event id</th></tr></thead>"
            "<tbody>{}</tbody></table>",
            rows,
        )
