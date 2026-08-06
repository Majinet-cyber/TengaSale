from django import forms
from django.contrib.auth import get_user_model

from .utils import assign_role, profile_role, ROLE_GROUPS
from .models import StaffRole, UserProfile


ROLE_CHOICES = [
    ("merchant",       "Merchant"),
    ("merchant_admin", "Merchant Administrator"),
    ("underwriter",    "Underwriter"),
    ("tech_support",   "Tech Support"),
    ("hq",             "HQ"),
    ("recommerce_intake", "Recommerce Intake Officer"),
    ("recommerce_assessor", "Device Assessor / Grader"),
    ("recommerce_technician", "Refurbishment Technician"),
    ("recommerce_qa", "Recommerce QA Officer"),
    ("recommerce_inventory", "Recommerce Inventory / Sales Officer"),
    ("recommerce_supervisor", "Recommerce Supervisor"),
    ("recommerce_hq", "HQ Recommerce Admin"),
]

RECOMMERCE_ROLE_CODES = {value for value, _label in ROLE_CHOICES if value.startswith("recommerce_")}


class HQUserForm(forms.ModelForm):
    full_name = forms.CharField(max_length=150, required=False, label="Full name")
    role = forms.ChoiceField(choices=ROLE_CHOICES)
    password = forms.CharField(widget=forms.PasswordInput, required=False, label="Password")

    class Meta:
        model = get_user_model()
        fields = ["username", "email", "full_name", "password", "role", "is_active"]

    def __init__(self, *args, **kwargs):
        self.creating = kwargs.pop("creating", False)
        super().__init__(*args, **kwargs)
        if self.creating:
            self.fields["password"].required = True
        if self.instance.pk:
            self.fields["full_name"].initial = self.instance.get_full_name()
            role = profile_role(self.instance)
            staff_role_code = getattr(getattr(self.instance, "profile", None), "staff_role", None)
            if getattr(staff_role_code, "code", "") in RECOMMERCE_ROLE_CODES:
                role = staff_role_code.code
            all_roles = set(ROLE_GROUPS.keys())
            if role in all_roles:
                self.fields["role"].initial = role
            else:
                for slug, group_name in ROLE_GROUPS.items():
                    if self.instance.groups.filter(name=group_name).exists():
                        self.fields["role"].initial = slug
                        break

    def save(self, commit=True):
        user = super().save(commit=False)
        full_name = self.cleaned_data.get("full_name", "").strip()
        if full_name:
            parts = full_name.split(" ", 1)
            user.first_name = parts[0]
            user.last_name = parts[1] if len(parts) > 1 else ""
        else:
            user.first_name = ""
            user.last_name = ""

        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)

        if commit:
            user.save()
            selected_role = self.cleaned_data["role"]
            if selected_role in RECOMMERCE_ROLE_CODES:
                staff_role = StaffRole.objects.filter(code=selected_role, active=True).first()
                if not staff_role:
                    raise forms.ValidationError("Recommerce roles must be seeded before assignment.")
                profile, _ = UserProfile.objects.get_or_create(user=user)
                profile.role = None
                profile.staff_role = staff_role
                profile.department = staff_role.department
                profile.user_type = UserProfile.USER_TYPE_STAFF
                profile.status = UserProfile.STATUS_ACTIVE if user.is_active else UserProfile.STATUS_INACTIVE
                profile.save(update_fields=["role", "staff_role", "department", "user_type", "status"])
            else:
                assign_role(user, selected_role)
        return user
