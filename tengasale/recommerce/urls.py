from django.urls import path

from . import views

app_name = "recommerce"

urlpatterns = [
    path("", views.home, name="home"),
    path("intakes/new/", views.intake_create, name="intake_create"),
    path("cases/<int:pk>/", views.case_detail, name="case"),
    path("earnings/", views.earnings, name="earnings"),
    path("hq/", views.hq_overview, name="hq_overview"),
]
