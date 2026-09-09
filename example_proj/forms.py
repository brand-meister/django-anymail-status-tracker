from django import forms

from anymail_status_tracker.debug.data import DEFAULT_EMAIL, EVENT_TYPES


class TestForm(forms.Form):
    FORM_EVENT_TYPES = tuple((x, x) for x in EVENT_TYPES)

    webhook_status_type = forms.ChoiceField(choices=FORM_EVENT_TYPES)
    # When set, fire against an existing delivery instead of inventing a new one.
    message_id = forms.CharField(required=False, widget=forms.HiddenInput)
    email = forms.EmailField(required=False, initial=DEFAULT_EMAIL, widget=forms.HiddenInput)
