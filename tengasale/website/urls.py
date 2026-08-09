from django.urls import path
from . import views

urlpatterns = [
    path("", views.landing, name="website_landing"),
    path("about/", views.about, name="website_about"),
    path("how-it-works/", views.how_it_works, name="website_how_it_works"),
    path("phones/", views.phones, name="website_phones"),
    path("trade-upgrade/", views.trade_upgrade, name="website_trade_upgrade"),
    path("markets/", views.markets, name="website_markets"),
    path("company/", views.company, name="website_company"),
    path("merchants/", views.merchants, name="website_merchants"),
    path("customers/", views.customers, name="website_customers"),
    path("faq/", views.faq, name="website_faq"),
    path("contact/", views.contact, name="website_contact"),
    path("terms/", views.terms, name="website_terms"),
    path("privacy/", views.privacy, name="website_privacy"),
    path("payment-terms/", views.payment_terms, name="website_payment_terms"),
    path("merchant-terms/", views.merchant_terms, name="website_merchant_terms"),
    path("merchant-signup/", views.merchant_signup, name="website_merchant_signup"),
    path("merchant-signup/success/", views.merchant_signup_success, name="website_merchant_signup_success"),
    path("careers/", views.careers, name="website_careers"),
    path("careers/success/", views.careers_success, name="website_careers_success"),
]
