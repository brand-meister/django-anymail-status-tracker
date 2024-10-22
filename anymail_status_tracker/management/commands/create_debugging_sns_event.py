import json

from django.core.management.base import BaseCommand
from django.test import RequestFactory

from anymail.webhooks.amazon_ses import AmazonSESTrackingWebhookView

from anymail_status_tracker.models import MailDelivery


class Command(BaseCommand):
    help = 'Create a dummy SNS event for testing and debugging purposes'

    def add_arguments(self, parser):
        parser.add_argument(
            'event_type',
            type=str,
            choices=['Bounce', 'Complaint', 'Delivery'],
            help='The type of SNS event to create'
        )
        parser.add_argument('state_data', type=str, help='The state data to be sent in the SNS event')
        parser.add_argument('mail_data', type=str, help='The mail data to be sent in the SNS event')


    def handle(self, *args, **options):
        event_type = options['event_type']
        state_data = json.loads(options['state_data'])
        mail_data = json.loads(options['mail_data'])
        recipient = [h['value'] for h in mail_data['headers'] if h['name'] == 'To'][0]
        message_id = mail_data['messageId']
        MailDelivery.objects.get_or_create(
            message_id=message_id,
            defaults={
                'recipient': recipient,
            }
        )

        webhook = AmazonSESTrackingWebhookView()
        sns_message = {'eventType': event_type, event_type.lower(): state_data, 'mail': mail_data}
        payload = {
            'Type': 'Notification',
            'Message': json.dumps(sns_message),
        }
        header = {'HTTP_X_AMZ_SNS_MESSAGE_TYPE': 'Notification'}
        request = RequestFactory().post('', data=json.dumps(payload), content_type='application/json', **header)
        webhook.dispatch(request)

