from django.urls import path
from . import views
from .whatsapp_webhook import whatsapp_webhook, whatsapp_simulate

urlpatterns = [
    path("",                                  views.support_dashboard,  name="support_dashboard"),
    path("tickets/",                          views.ticket_list,        name="ticket_list"),
    path("tickets/new/",                      views.ticket_create,      name="ticket_create"),
    path("tickets/<int:ticket_id>/",          views.ticket_detail,      name="ticket_detail"),
    path("tickets/<int:ticket_id>/assign/",   views.ticket_assign,      name="ticket_assign"),
    path("tickets/<int:ticket_id>/assign-me/", views.ticket_assign_me,  name="ticket_assign_me"),
    path("tickets/<int:ticket_id>/status/",   views.ticket_status,      name="ticket_status"),
    path("tickets/<int:ticket_id>/escalate/", views.ticket_escalate,    name="ticket_escalate"),
    path("tickets/<int:ticket_id>/resolve/",  views.ticket_resolve,     name="ticket_resolve"),
    path("tickets/<int:ticket_id>/reopen/",   views.ticket_reopen,      name="ticket_reopen"),
    path("bugs/",                             views.bug_monitor,        name="bug_monitor"),
    path("bugs/<int:bug_id>/",                views.bug_detail,         name="bug_detail"),
    path("bugs/<int:bug_id>/resolve/",        views.bug_resolve,        name="bug_resolve"),
    path("bugs/<int:bug_id>/ignore/",         views.bug_ignore,          name="bug_ignore"),
    path("bugs/<int:bug_id>/to-ticket/",      views.bug_to_ticket,      name="bug_to_ticket"),

    # WhatsApp / chatbot webhook
    path("whatsapp/webhook/", whatsapp_webhook,  name="whatsapp_webhook"),
    path("whatsapp/simulate/", whatsapp_simulate, name="whatsapp_simulate"),

    # Conversation list for HQ
    path("conversations/", views.conversation_list, name="conversation_list"),
]
