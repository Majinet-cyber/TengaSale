from django import forms

from .models import CustomerCallQuestionnaire


SCORE_CHOICES = [
    (1, ""),
    (2, ""),
    (3, ""),
]


class CustomerCallQuestionnaireForm(forms.ModelForm):
    class Meta:
        model = CustomerCallQuestionnaire
        fields = [
            "recording_consent_acknowledged",
            "identity_score",
            "work_context_score",
            "income_confidence_score",
            "price_understanding_score",
            "payment_understanding_score",
            "determined_income",
            "closing_notes",
        ]
        widgets = {
            "recording_consent_acknowledged": forms.CheckboxInput(attrs={"class": "call-checkbox"}),
            "identity_score": forms.RadioSelect(choices=SCORE_CHOICES),
            "work_context_score": forms.RadioSelect(choices=SCORE_CHOICES),
            "income_confidence_score": forms.RadioSelect(choices=SCORE_CHOICES),
            "price_understanding_score": forms.RadioSelect(choices=SCORE_CHOICES),
            "payment_understanding_score": forms.RadioSelect(choices=SCORE_CHOICES),
            "determined_income": forms.NumberInput(attrs={
                "class": "call-input",
                "inputmode": "decimal",
                "min": "0",
                "step": "0.01",
                "placeholder": "MWK amount",
            }),
            "closing_notes": forms.Textarea(attrs={
                "class": "call-textarea",
                "rows": 5,
                "placeholder": "Capture customer concerns, repayment doubts, or final context.",
            }),
        }

    def clean_recording_consent_acknowledged(self):
        value = self.cleaned_data.get("recording_consent_acknowledged")
        if not value:
            raise forms.ValidationError("Recording consent must be acknowledged before submitting.")
        return value

    def clean(self):
        cleaned = super().clean()
        for field in CustomerCallQuestionnaire.SCORE_FIELDS:
            score = cleaned.get(field)
            if score not in (1, 2, 3):
                self.add_error(field, "Choose a score.")
        return cleaned
