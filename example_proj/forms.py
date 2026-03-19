from django import forms

from anymail_status_tracker.debug.data import EVENT_TYPES


class TestForm(forms.Form):
    FORM_EVENT_TYPES = tuple((x, x) for x in EVENT_TYPES)

    webhook_status_type = forms.ChoiceField(choices=FORM_EVENT_TYPES)
