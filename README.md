# django-anymail-status-tracker
A simple wrapper around django-anymail to receive and persist e-mail status event data.

Records mail delivery in the `MailDelivery` model and updates the state if a matching callback notification is received.


## Requirements

- [Django](https://www.djangoproject.com) version 4.2+


## Quick start

1. Add `anymail_status_tracker` to your INSTALLED_APPS setting like this:

    ```python
    INSTALLED_APPS = [
        # ...
        'anymail_status_tracker',
    ]
    ```

2. Run `python manage.py migrate` to create the models.

3. [Setup](https://anymail.dev/en/stable/installation/) `django-anymail`

4. [Configure Webhooks](https://anymail.dev/en/stable/installation/#configuring-tracking-and-inbound-webhooks)

5. [Configure Django E-mail Backend](https://anymail.dev/en/v3.0/installation/#configuring-django-s-email-backend)


## Development setup

1. Install development dependencies:

    ```bash
    uv sync
    ```

2. Install the [ruff extension](https://docs.astral.sh/ruff/integrations/) for code linting & formatting in your IDE.

3. (Optional) Override settings in `example_proj/settings_local.py` & `tests/settings_local.py` as required.
