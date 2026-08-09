from pathlib import Path
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from .models import ProtectCase
from .services import CONSENT_TEXT


class ProtectReportForm(forms.Form):
    case_type = forms.ChoiceField(choices=ProtectCase.TYPE_CHOICES, widget=forms.RadioSelect)
    incident_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    incident_time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    incident_location = forms.CharField(max_length=250)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    police_station = forms.CharField(max_length=150, required=False)
    police_reference = forms.CharField(max_length=100, required=False)
    police_report = forms.FileField(required=False)
    alternative_phone = forms.CharField(max_length=30)
    alternative_contact_name = forms.CharField(max_length=150, required=False)
    consent = forms.BooleanField(label=CONSENT_TEXT)
    customer_pin = forms.CharField(max_length=12, widget=forms.PasswordInput, help_text="Enter the last four digits of the contract phone number to confirm your identity.")

    def __init__(self, *args, contract=None, **kwargs):
        super().__init__(*args, **kwargs); self.contract = contract

    def clean_customer_pin(self):
        value = self.cleaned_data["customer_pin"]
        digits = "".join(c for c in self.contract.customer_phone if c.isdigit())
        if len(digits) < 4 or value != digits[-4:]:
            raise ValidationError("Identity confirmation failed.")
        return value

    def clean_police_report(self):
        upload = self.cleaned_data.get("police_report")
        if not upload: return upload
        if upload.size > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            raise ValidationError("File is too large.")
        if Path(upload.name).suffix.lower() not in {".pdf", ".jpg", ".jpeg", ".png"}:
            raise ValidationError("Upload a PDF, JPG or PNG file.")
        if upload.content_type not in {"application/pdf", "image/jpeg", "image/png"}:
            raise ValidationError("Unsupported file type.")
        return upload

    def clean(self):
        data = super().clean()
        if data.get("case_type") == "stolen" and not data.get("police_report"):
            self.add_error("police_report", "A police report is required for stolen-device reports.")
        return data


class ImeiCheckForm(forms.Form):
    imei = forms.CharField(min_length=14, max_length=20)

    def clean_imei(self):
        value = "".join(c for c in self.cleaned_data["imei"] if c.isdigit())
        if len(value) not in {14, 15, 16}: raise ValidationError("Enter a valid IMEI.")
        return value
