from django.contrib import admin

from anymail_status_tracker.models import MailDelivery


@admin.register(MailDelivery)
class MailDeliveryAdmin(admin.ModelAdmin):
    list_display = ("recipient", "sent_at", "updated_at", "state")
    list_filter = ("state",)
    readonly_fields = ("sent_at", "updated_at")
    ordering = ("-updated_at",)
