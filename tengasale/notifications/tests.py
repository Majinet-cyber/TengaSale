from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Notification


class NotificationStabilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="notif-user",
            password="test-pass-123",
        )

    def test_notifications_page_loads_with_missing_object_reference(self):
        Notification.send(
            recipient=self.user,
            notification_type=Notification.TYPE_APP_UPDATE,
            title="Payment received",
            body="No contract reference",
            object_type="PaymentContract",
            object_id="999999",
            level=Notification.LEVEL_SUCCESS,
        )
        self.client.login(username="notif-user", password="test-pass-123")

        response = self.client.get(reverse("notifications_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment received")
        self.assertContains(response, "No contract reference")
