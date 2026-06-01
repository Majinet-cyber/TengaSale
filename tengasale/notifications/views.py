from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def notification_list(request):
    notifications = Notification.for_user(request.user, limit=50)
    unread_count = Notification.unread_count_for(request.user)
    return TemplateResponse(request, "notifications/list.html", {
        "notifications": notifications,
        "unread_count": unread_count,
        "page_title": "Notifications",
    })


@login_required
@require_POST
def mark_read(request, pk):
    notif = get_object_or_404(
        Notification,
        Q(recipient=request.user) | Q(recipient__isnull=True),
        pk=pk,
    )
    notif.mark_read()
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    return redirect(next_url)


@login_required
@require_POST
def mark_all_read(request):
    Notification.objects.filter(
        Q(recipient=request.user) | Q(recipient__isnull=True),
        is_read=False,
    ).update(is_read=True)
    return redirect(request.POST.get("next") or "/notifications/")


@login_required
def unread_count_api(request):
    count = Notification.unread_count_for(request.user)
    return JsonResponse({"unread_count": count})


@login_required
def dropdown_partial(request):
    notifications = Notification.for_user(request.user, limit=8)
    return TemplateResponse(request, "notifications/dropdown.html", {
        "notifications": notifications,
        "unread_count": Notification.unread_count_for(request.user),
    })
