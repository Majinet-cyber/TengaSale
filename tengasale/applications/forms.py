import base64
import binascii
import uuid
import json
from datetime import date
from decimal import Decimal

from django import forms
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.db import OperationalError, ProgrammingError

from geography.models import District, Region, TraditionalAuthority
from geography.data import DISTRICTS_BY_REGION

from .models import FinancingApplication
from .income_bands import INCOME_BANDS as CANONICAL_INCOME_BANDS, client_income_band_rules, validate_income_band_amount


INCOME_BANDS = [
    ("", "Select income band"),
    ("less_than_100k", "Less than MWK 100,000"),
    ("100k_150k", "MWK 100,000 – MWK 150,000"),
    ("150k_200k", "MWK 150,000 – MWK 200,000"),
    ("200k_250k", "MWK 200,000 – MWK 250,000"),
    ("250k_300k", "MWK 250,000 – MWK 300,000"),
    ("300k_350k", "MWK 300,000 – MWK 350,000"),
    ("350k_400k", "MWK 350,000 – MWK 400,000"),
    ("400k_600k", "MWK 400,000 – MWK 600,000"),
    ("more_than_600k", "More than MWK 600,000"),
]

INCOME_BANDS = CANONICAL_INCOME_BANDS

REGIONS = [
    ("", "Select region"),
    ("Central", "Central"),
    ("Southern", "Southern"),
    ("Northern", "Northern"),
]

PROOF_TYPES = [
    ("", "Select proof type"),
    ("MoMo", "MoMo"),
    ("Bank", "Bank"),
    ("Employer", "Employer"),
    ("Business Contact", "Business Contact"),
    ("Other", "Other"),
]

OCCUPATION_CHOICES = [
    ("", "How does the customer earn money?"),
    ("Employed", "They are employed"),
    ("Business Owner", "They own a business"),
    ("Farming", "Farming / agriculture"),
    ("Piece Work", "Piece work / informal income"),
    ("Civil Servant", "Civil servant"),
    ("Teacher", "Teacher"),
    ("Health Worker", "Health worker"),
    ("Security Services", "Security services"),
    ("Driver / Transport", "Driver / transport"),
    ("NGO / Development Sector", "NGO / development sector"),
    ("Retired", "Retired"),
    ("Unemployed", "Unemployed"),
    ("Other", "Other"),
]

OCCUPATION_VALUES = {value for value, _label in OCCUPATION_CHOICES if value}

RELATIONSHIP_CHOICES = [
    ("", "Select relationship"),
    ("Family", "Family"),
    ("Friend", "Friend"),
    ("Neighbour", "Neighbour"),
    ("Other", "Other"),
]


def clean_exact_digits(value, length, field_label):
    value = (value or "").strip()
    if not value.isdigit() or len(value) != length:
        raise forms.ValidationError(f"{field_label} must be exactly {length} digits.")
    return value


def local_phone_widget(placeholder="990870616"):
    return forms.TextInput(
        attrs={
            "maxlength": "9",
            "minlength": "9",
            "pattern": "[0-9]{9}",
            "inputmode": "numeric",
            "autocomplete": "tel",
            "data-phone-input": "true",
            "placeholder": placeholder,
        }
    )


GENDER_CHOICES = [
    ("", "Select gender"),
    ("male", "Male"),
    ("female", "Female"),
]

MARITAL_CHOICES = [
    ("", "Select marital status"),
    ("single", "Single"),
    ("married", "Married"),
    ("divorced", "Divorced"),
    ("widowed", "Widowed"),
    ("separated", "Separated"),
]

PHONE_USER_CHOICES = [
    ("", "Who will mainly use this phone?"),
    ("customer_self", "The customer themselves"),
    ("spouse", "Spouse"),
    ("child", "Child"),
    ("parent", "Parent"),
    ("family_member", "Other family member"),
    ("friend", "Friend"),
    ("business_employee", "Business employee"),
    ("other", "Other"),
]

FIRST_SMARTPHONE_CHOICES = [
    ("", "Is this your first smartphone?"),
    ("yes", "Yes"),
    ("no", "No"),
]


class CustomerDetailsForm(forms.ModelForm):
    customer_name = forms.CharField(required=True, min_length=2, strip=True)
    national_id = forms.CharField(
        required=True,
        error_messages={"required": "National ID must be exactly 8 letters or numbers."},
        widget=forms.TextInput(
            attrs={
                "maxlength": "8",
                "minlength": "8",
                "pattern": "[A-Za-z0-9]{8}",
                "autocomplete": "off",
                "autocapitalize": "characters",
                "spellcheck": "false",
                "data-national-id-input": "true",
                "placeholder": "RQXFVZC9",
            }
        ),
    )
    customer_phone = forms.CharField(
        required=True,
        error_messages={"required": "Phone number must be exactly 9 digits."},
        widget=forms.TextInput(
            attrs={
                "maxlength": "9",
                "minlength": "9",
                "pattern": "[0-9]{9}",
                "inputmode": "numeric",
                "autocomplete": "tel",
                "data-phone-input": "true",
                "placeholder": "990870616",
            }
        ),
    )
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "inputmode": "numeric"}),
        label="Date of birth",
    )
    gender = forms.ChoiceField(choices=GENDER_CHOICES, required=False, label="Gender")
    marital_status = forms.ChoiceField(choices=MARITAL_CHOICES, required=False, label="Marital status")
    num_dependents = forms.IntegerField(
        required=False, min_value=0, max_value=20,
        label="Number of dependents",
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "placeholder": "0"}),
    )
    phone_user = forms.ChoiceField(choices=PHONE_USER_CHOICES, required=False, label="Who will use the phone?")
    first_smartphone = forms.TypedChoiceField(
        choices=FIRST_SMARTPHONE_CHOICES,
        coerce=lambda value: value == "yes",
        empty_value=None,
        required=False,
        label="Is this your first smartphone?",
    )
    phone_user_other = forms.CharField(
        required=False, max_length=100,
        label="Specify who",
        widget=forms.TextInput(attrs={"placeholder": "Specify who will use it"}),
    )
    occupation = forms.ChoiceField(choices=OCCUPATION_CHOICES, required=True)
    occupation_other = forms.CharField(
        required=False,
        min_length=2,
        strip=True,
        label="Specify occupation",
        widget=forms.TextInput(attrs={"data-occupation-other": "true", "placeholder": "Specify occupation"}),
    )
    income_band = forms.ChoiceField(choices=INCOME_BANDS, required=True)
    exact_monthly_income = forms.DecimalField(required=True, min_value=Decimal("0.01"))

    class Meta:
        model = FinancingApplication
        fields = [
            "customer_name",
            "national_id",
            "customer_phone",
            "date_of_birth",
            "gender",
            "marital_status",
            "num_dependents",
            "phone_user",
            "first_smartphone",
            "phone_user_other",
            "occupation",
            "income_band",
            "exact_monthly_income",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["income_band"].widget.attrs["data-income-band"] = "true"
        self.fields["exact_monthly_income"].widget.attrs.update({"data-exact-income": "true", "data-income-band-rules": json.dumps(client_income_band_rules()), "inputmode": "numeric", "step": "1"})
        existing_occupation = self.instance.occupation if self.instance.pk else ""
        if existing_occupation and existing_occupation not in OCCUPATION_VALUES:
            self.fields["occupation"].initial = "Other"
            self.fields["occupation_other"].initial = existing_occupation

    def clean_national_id(self):
        value = (self.cleaned_data.get("national_id") or "").strip().upper()
        if len(value) != 8 or not value.isalnum():
            raise forms.ValidationError("National ID must be exactly 8 letters or numbers.")
        return value

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get("date_of_birth")
        if dob:
            today = date.today()
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            if age < 20:
                raise forms.ValidationError(
                    "Applicant must be at least 20 years old to qualify for financing."
                )
            if age > 69:
                raise forms.ValidationError(
                    "Applicant must be 69 years old or under to qualify for financing."
                )
        return dob

    def clean_customer_phone(self):
        value = (self.cleaned_data.get("customer_phone") or "").strip()
        if not value.isdigit() or len(value) != 9:
            raise forms.ValidationError("Phone number must be exactly 9 digits.")
        return value

    def clean(self):
        cleaned_data = super().clean()
        band = cleaned_data.get("income_band")
        amount = cleaned_data.get("exact_monthly_income")
        if band and amount is not None:
            try:
                cleaned_data["exact_monthly_income"] = validate_income_band_amount(band, amount)
            except ValidationError as exc:
                self.add_error("exact_monthly_income", exc)
        occupation = cleaned_data.get("occupation")
        occupation_other = (cleaned_data.get("occupation_other") or "").strip()
        if occupation == "Other":
            if not occupation_other:
                self.add_error("occupation_other", "Specify the occupation when Other is selected.")
            else:
                cleaned_data["occupation"] = occupation_other

        phone_user = cleaned_data.get("phone_user", "")
        if phone_user and phone_user != "customer_self":
            cleaned_data["third_party_phone_user_risk_flagged"] = True
        return cleaned_data


class KYCForm(forms.ModelForm):
    allowed_image_types = {"image/jpeg", "image/png", "image/webp"}

    class Meta:
        model = FinancingApplication
        fields = ["customer_face_image", "id_front_image", "id_back_image"]
        widgets = {
            "customer_face_image": forms.FileInput(
                attrs={
                    "accept": "image/*",
                    "capture": "user",
                    "class": "kyc-file-input",
                    "data-capture-input": "customer-face",
                }
            ),
            "id_front_image": forms.FileInput(
                attrs={
                    "accept": "image/*",
                    "capture": "environment",
                    "class": "kyc-file-input",
                    "data-capture-input": "id-front",
                }
            ),
            "id_back_image": forms.FileInput(
                attrs={
                    "accept": "image/*",
                    "capture": "environment",
                    "class": "kyc-file-input",
                    "data-capture-input": "id-back",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False

    def clean(self):
        cleaned_data = super().clean()
        labels = {
            "customer_face_image": "Customer face image",
            "id_front_image": "ID front image",
            "id_back_image": "ID back image",
        }

        from applications.flow_helpers import kyc_image_field_ready

        for field_name, label in labels.items():
            uploaded_file = self.files.get(field_name)
            saved_file = getattr(self.instance, field_name, None)

            if not uploaded_file and not kyc_image_field_ready(saved_file):
                self.add_error(field_name, f"{label} is required.")
                continue

            if uploaded_file and getattr(uploaded_file, "content_type", None) not in self.allowed_image_types:
                self.add_error(field_name, "Upload a JPEG, PNG, or WebP image.")

        return cleaned_data


KycForm = KYCForm


class LocationNextOfKinForm(forms.ModelForm):
    region = forms.ChoiceField(choices=REGIONS, required=True)
    district = forms.ChoiceField(choices=[("", "Select district")], required=True)
    traditional_authority = forms.ChoiceField(choices=[("", "Select traditional authority")], required=True)
    precise_location = forms.CharField(required=True, strip=True)
    next_of_kin_1_name = forms.CharField(required=True, label="Next of kin 1 name *")
    next_of_kin_1_phone = forms.CharField(
        required=True,
        label="Next of kin 1 phone *",
        widget=local_phone_widget(),
    )
    next_of_kin_1_relationship = forms.ChoiceField(
        choices=RELATIONSHIP_CHOICES,
        required=True,
        label="Next of kin 1 relationship *",
    )

    class Meta:
        model = FinancingApplication
        fields = [
            "region",
            "district",
            "traditional_authority",
            "precise_location",
            "gps_coordinates",
            "map_screenshot",
            "next_of_kin_1_name",
            "next_of_kin_1_phone",
            "next_of_kin_1_relationship",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        region = self.data.get("region") or self.initial.get("region") or self.instance.region
        district = self.data.get("district") or self.initial.get("district") or self.instance.district
        traditional_authority = (
            self.data.get("traditional_authority")
            or self.initial.get("traditional_authority")
            or self.instance.traditional_authority
        )

        self.fields["region"].choices = self.region_choices()
        self.fields["district"].choices = self.district_choices(region, district)
        self.fields["traditional_authority"].choices = self.ta_choices(district, traditional_authority)

    def region_choices(self):
        try:
            names = list(Region.objects.order_by("name").values_list("name", flat=True))
        except (OperationalError, ProgrammingError):
            names = []
        if not names:
            return REGIONS
        return [("", "Select region")] + [(name, name) for name in names]

    def district_choices(self, region, current_district=None):
        names = []
        if region:
            try:
                names = list(
                    District.objects.filter(region__name=region).order_by("name").values_list("name", flat=True)
                )
            except (OperationalError, ProgrammingError):
                names = []
        if not names:
            names = DISTRICTS_BY_REGION.get(region, [])
        if current_district and current_district not in names:
            names.append(current_district)
        return [("", "Select district")] + [(name, name) for name in names]

    def ta_choices(self, district, current_ta=None):
        names = []
        if district:
            try:
                names = list(
                    TraditionalAuthority.objects.filter(district__name=district)
                    .order_by("name")
                    .values_list("name", flat=True)
                )
            except (OperationalError, ProgrammingError):
                names = []
        if current_ta and current_ta not in names:
            names.append(current_ta)
        return [("", "Select traditional authority")] + [(name, name) for name in names]

    def clean(self):
        cleaned_data = super().clean()
        region = cleaned_data.get("region")
        district = cleaned_data.get("district")
        traditional_authority = cleaned_data.get("traditional_authority")

        try:
            district_record = District.objects.filter(region__name=region, name=district).first()
            if region and district and Region.objects.filter(name=region).exists() and not district_record:
                self.add_error("district", "Select a district in the selected region.")
            if district_record and TraditionalAuthority.objects.filter(district=district_record).exists():
                if not TraditionalAuthority.objects.filter(
                    district=district_record,
                    name=traditional_authority,
                ).exists():
                    self.add_error("traditional_authority", "Select a traditional authority in the selected district.")
        except (OperationalError, ProgrammingError):
            pass

        return cleaned_data

    def clean_next_of_kin_1_phone(self):
        value = clean_exact_digits(self.cleaned_data.get("next_of_kin_1_phone"), 9, "Next of kin 1 phone")
        if value == (self.instance.customer_phone or "").strip():
            raise forms.ValidationError("Next of kin phone cannot be the same as customer phone.")
        return value


class WorkProofForm(forms.ModelForm):
    work_description = forms.CharField(
        required=True,
        label="Work / service description *",
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    next_of_kin_2_name = forms.CharField(required=True, label="Next of kin 2 name *")
    next_of_kin_2_phone = forms.CharField(
        required=True,
        label="Next of kin 2 phone *",
        widget=local_phone_widget(),
    )
    next_of_kin_2_relationship = forms.ChoiceField(
        choices=RELATIONSHIP_CHOICES,
        required=True,
        label="Next of kin 2 relationship *",
    )
    proof_of_income_type = forms.ChoiceField(
        choices=PROOF_TYPES,
        required=True,
        label="Proof of income type *",
    )
    proof_contact_name = forms.CharField(required=True, label="Proof contact name *")
    proof_contact_phone = forms.CharField(
        required=True,
        label="Proof contact phone *",
        widget=local_phone_widget(),
    )

    class Meta:
        model = FinancingApplication
        fields = [
            "work_description",
            "next_of_kin_2_name",
            "next_of_kin_2_phone",
            "next_of_kin_2_relationship",
            "proof_of_income_type",
            "proof_contact_name",
            "proof_contact_phone",
            "proof_notes",
            "proof_income_file",
        ]

    def clean_next_of_kin_2_phone(self):
        value = clean_exact_digits(self.cleaned_data.get("next_of_kin_2_phone"), 9, "Next of kin 2 phone")
        if value == (self.instance.customer_phone or "").strip():
            raise forms.ValidationError("Next of kin phone cannot be the same as customer phone.")
        if value == (self.instance.next_of_kin_1_phone or "").strip():
            raise forms.ValidationError("Next of kin 2 phone cannot be the same as next of kin 1 phone.")
        return value

    def clean_proof_contact_phone(self):
        value = clean_exact_digits(self.cleaned_data.get("proof_contact_phone"), 9, "Proof contact phone")
        if value == (self.instance.customer_phone or "").strip():
            raise forms.ValidationError("Proof contact phone cannot be the same as customer phone.")
        return value


def signature_data_to_file(signature_data):
    prefix = "data:image/png;base64,"
    if not signature_data.startswith(prefix):
        raise forms.ValidationError("Save a valid PNG signature before submitting.")
    try:
        decoded = base64.b64decode(signature_data[len(prefix):], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise forms.ValidationError("Save a valid PNG signature before submitting.") from exc
    return ContentFile(decoded, name=f"signature-{uuid.uuid4().hex}.png")


class SignatureCaptureForm(forms.Form):
    signature_data = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.signature_file = None

    def clean_signature_data(self):
        signature_data = (self.cleaned_data.get("signature_data") or "").strip()
        if not signature_data:
            raise forms.ValidationError("Please sign before saving.")
        self.signature_file = signature_data_to_file(signature_data)
        return signature_data


class SignatureForm(forms.ModelForm):
    signature_data = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = FinancingApplication
        fields = ["agreed_to_terms"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.signature_file = None

    def clean(self):
        cleaned_data = super().clean()
        signature_data = (cleaned_data.get("signature_data") or "").strip()
        agreed = cleaned_data.get("agreed_to_terms")

        if signature_data:
            try:
                self.signature_file = signature_data_to_file(signature_data)
            except forms.ValidationError as exc:
                self.add_error("signature_data", exc)

        if not self.signature_file and not self.instance.signature_image:
            self.add_error("signature_data", "Save the customer signature before submitting.")
        if not agreed:
            self.add_error("agreed_to_terms", "The customer must agree to the terms before submitting.")

        return cleaned_data


LocationForm = LocationNextOfKinForm
WorkForm = WorkProofForm
