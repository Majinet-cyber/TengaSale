from django import forms

from .models import DeviceIntake


class DeviceIntakeForm(forms.ModelForm):
    class Meta:
        model = DeviceIntake
        fields = ["customer", "existing_device", "imei", "serial_number", "brand", "model", "intake_location", "declared_ownership"]

    def clean_imei(self):
        imei = "".join(filter(str.isdigit, self.cleaned_data["imei"]))
        if len(imei) not in (14, 15, 16):
            raise forms.ValidationError("Enter a valid device identifier.")
        if DeviceIntake.objects.exclude(pk=self.instance.pk).filter(imei=imei).exclude(status__in=["cancelled", "closed"]).exists():
            raise forms.ValidationError("This device already has an active recommerce case.")
        return imei


class TransitionForm(forms.Form):
    next_status = forms.ChoiceField(choices=DeviceIntake.STATUS_CHOICES)
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    override = forms.BooleanField(required=False)
