"""Forms for public Tenga website enquiries."""

from django import forms


class WebsiteEnquiryForm(forms.Form):
    CATEGORY_CUSTOMER = "customer_support"
    CATEGORY_APPLICATION = "new_application"
    CATEGORY_PAYMENT = "payment_help"
    CATEGORY_MERCHANT = "merchant_partnership"
    CATEGORY_INVESTOR = "investor_media"
    CATEGORY_CAREERS = "careers"
    CATEGORY_GENERAL = "general"

    CATEGORY_CHOICES = (
        (CATEGORY_CUSTOMER, "Customer support"),
        (CATEGORY_APPLICATION, "New phone / application"),
        (CATEGORY_PAYMENT, "Payment help"),
        (CATEGORY_MERCHANT, "Merchant partnership"),
        (CATEGORY_INVESTOR, "Investor or media"),
        (CATEGORY_CAREERS, "Careers"),
        (CATEGORY_GENERAL, "General enquiry"),
    )
    PHONE_REQUIRED_CATEGORIES = {
        CATEGORY_APPLICATION,
        CATEGORY_PAYMENT,
        CATEGORY_MERCHANT,
    }

    full_name = forms.CharField(
        label="Full name",
        max_length=120,
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Your full name"}),
    )
    email = forms.EmailField(
        label="Email address",
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "you@example.com"}),
    )
    phone = forms.CharField(
        label="Phone number",
        max_length=40,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "tel", "placeholder": "+265..."}),
    )
    category = forms.ChoiceField(label="How can we help?", choices=CATEGORY_CHOICES)
    subject = forms.CharField(
        label="Subject",
        max_length=160,
        widget=forms.TextInput(attrs={"placeholder": "A short summary"}),
    )
    message = forms.CharField(
        label="Message",
        min_length=20,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "Tell us what you need help with..."}),
    )
    consent = forms.BooleanField(
        label="I agree that Tenga may use these details to respond to my enquiry."
    )
    website = forms.CharField(
        label="Website",
        required=False,
        widget=forms.TextInput(
            attrs={"tabindex": "-1", "autocomplete": "off", "aria-hidden": "true"}
        ),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("category") in self.PHONE_REQUIRED_CATEGORIES and not cleaned.get("phone", "").strip():
            self.add_error("phone", "A phone number is required for this type of enquiry.")
        return cleaned

    @property
    def category_label(self):
        return dict(self.CATEGORY_CHOICES).get(self.cleaned_data.get("category"), "General enquiry")
