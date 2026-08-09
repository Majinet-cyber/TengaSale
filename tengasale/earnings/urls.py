from django.urls import path
from . import views

urlpatterns = [
    path("", views.earnings_home, name="earnings_home"),
    path("unlock/", views.earnings_unlock, name="earnings_unlock"),
    path("lock/", views.earnings_lock_now, name="earnings_lock_now"),
    path("security/enable/", views.earnings_lock_enable, name="earnings_lock_enable"),
    path("security/disable/", views.earnings_lock_disable, name="earnings_lock_disable"),
    path("security/reset/", views.earnings_pin_reset, name="earnings_pin_reset"),
    path("security/recovery/start/", views.earnings_recovery_start, name="earnings_recovery_start"),
    path("security/recovery/verify/", views.earnings_recovery_verify, name="earnings_recovery_verify"),
    path("leaderboard/", views.merchant_leaderboard, name="merchant_leaderboard"),
    path("spin/", views.spin_rewards, name="spin_rewards"),
]
