from django.urls import path

from . import views

app_name = "recommerce"

urlpatterns = [
    path("", views.home, name="home"),
    path("intake/", views.home, name="intake_workspace"),
    path("assessments/", views.home, name="assessment_workspace"),
    path("refurbishment/", views.home, name="refurbishment_workspace"),
    path("qa/", views.home, name="qa_workspace"),
    path("inventory/", views.home, name="inventory_workspace"),
    path("operations/", views.home, name="operations_workspace"),
    path("intakes/new/", views.intake_create, name="intake_create"),
    path("cases/<int:pk>/", views.case_detail, name="case"),
    path("earnings/", views.earnings, name="earnings"),
    path("hq/", views.hq_overview, name="hq_overview"),
]
