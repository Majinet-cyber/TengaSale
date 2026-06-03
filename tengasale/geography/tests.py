from importlib import import_module

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from django.core.files.base import ContentFile

from applications.models import FinancingApplication
from applications.tests import PNG_BYTES
from accounts.utils import assign_role

from .models import District, Region, TraditionalAuthority


class GeographySeedTests(TestCase):
    def test_seed_command_creates_regions_districts_and_tas(self):
        call_command("seed_tengasale")

        self.assertEqual(Region.objects.count(), 3)
        self.assertEqual(District.objects.filter(region__name="Central").count(), 9)
        self.assertEqual(District.objects.filter(region__name="Southern").count(), 13)
        self.assertEqual(District.objects.filter(region__name="Northern").count(), 7)
        self.assertTrue(TraditionalAuthority.objects.filter(district__name="Lilongwe").count() >= 2)

    def test_admin_imports_do_not_crash(self):
        admin_module = import_module("geography.admin")

        self.assertIs(admin_module.Region, Region)
        self.assertIn(Region, admin.site._registry)


class LocationGeographyTests(TestCase):
    def setUp(self):
        call_command("seed_tengasale")
        self.user = get_user_model().objects.create_user(username="merchant", password="test-pass-123")
        assign_role(self.user, "merchant")
        self.client.login(username="merchant", password="test-pass-123")
        self.app = FinancingApplication.objects.create(
            created_by=self.user,
            customer_phone="990870616",
        )
        self.app.customer_face_image.save("face.png", ContentFile(PNG_BYTES), save=False)
        self.app.id_front_image.save("front.png", ContentFile(PNG_BYTES), save=False)
        self.app.id_back_image.save("back.png", ContentFile(PNG_BYTES), save=False)
        self.app.save()

    def location_data(self, **overrides):
        data = {
            "region": "Central",
            "district": "Lilongwe",
            "traditional_authority": "Chitukula",
            "precise_location": "Area 25",
            "next_of_kin_1_name": "Mary Banda",
            "next_of_kin_1_phone": "991111111",
            "next_of_kin_1_relationship": "Family",
        }
        data.update(overrides)
        return data

    def test_location_page_contains_location_fields_and_ta_json(self):
        response = self.client.get(reverse("location_details", args=[self.app.id]))

        self.assertContains(response, 'name="region"')
        self.assertContains(response, 'name="district"')
        self.assertContains(response, 'name="traditional_authority"')
        self.assertContains(response, "geography-data")
        self.assertContains(response, "Chitukula")
        self.assertContains(response, "Chakhaza")

    def test_cannot_submit_location_without_ta(self):
        response = self.client.post(
            reverse("location_details", args=[self.app.id]),
            self.location_data(traditional_authority=""),
        )

        self.app.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(self.app.status, "location_details")
        self.assertContains(response, "This field is required.")

    def test_valid_location_submission_saves_selected_ta(self):
        response = self.client.post(reverse("location_details", args=[self.app.id]), self.location_data())

        self.app.refresh_from_db()
        self.assertRedirects(response, reverse("work_details", args=[self.app.id]))
        self.assertEqual(self.app.region, "Central")
        self.assertEqual(self.app.district, "Lilongwe")
        self.assertEqual(self.app.traditional_authority, "Chitukula")

    def test_district_selection_filters_tas_correctly(self):
        response = self.client.get(reverse("location_details", args=[self.app.id]))
        content = response.content.decode()

        self.assertIn('"Lilongwe": ["Chadza", "Chitukula"', content)
        self.assertIn('"Dowa": ["Chakhaza"', content)
