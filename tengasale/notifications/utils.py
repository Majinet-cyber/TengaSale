from django.contrib.auth import get_user_model

from .models import Notification


def _role_recipients(*roles):
    User = get_user_model()
    return User.objects.filter(profile__role__in=roles, is_active=True)


def _send_once(sent_to, *, recipient, notification_type, title, body, link, level, application):
    if not recipient or recipient.pk in sent_to:
        return
    Notification.send(
        recipient=recipient,
        notification_type=notification_type,
        title=title,
        body=body,
        link=link,
        object_type="FinancingApplication",
        object_id=application.pk,
        level=level,
    )
    sent_to.add(recipient.pk)


def notify_application_claimed(application, underwriter):
    try:
        underwriter_name = underwriter.get_full_name() or underwriter.username
        Notification.send(
            recipient=underwriter,
            notification_type=Notification.TYPE_ASSIGNED,
            title="Application claimed successfully",
            body=f"{application.application_number} is now in your active reviews.",
            link=f"/sales/applications/{application.pk}/",
            object_type="FinancingApplication",
            object_id=application.pk,
            level=Notification.LEVEL_SUCCESS,
        )
        if application.created_by_id:
            Notification.send(
                recipient=application.created_by,
                notification_type=Notification.TYPE_ASSIGNED,
                title="Application claimed by underwriter",
                body=(
                    f"{application.customer_name or 'Customer'} - {application.application_number} "
                    f"is now being reviewed by {underwriter_name}."
                ),
                link=application.get_continue_url(),
                object_type="FinancingApplication",
                object_id=application.pk,
                level=Notification.LEVEL_INFO,
            )
    except Exception:
        pass


def notify_application_approved(application, approved_by=None):
    try:
        sent_to = set()
        body = (
            f"{application.customer_name or 'Customer'} - {application.application_number} "
            "was approved. IMEI is now required."
        )
        _send_once(
            sent_to,
            recipient=application.created_by,
            notification_type=Notification.TYPE_APP_APPROVED,
            title="Application approved",
            body=body,
            link=application.get_continue_url(),
            level=Notification.LEVEL_SUCCESS,
            application=application,
        )
        if approved_by:
            _send_once(
                sent_to,
                recipient=approved_by,
                notification_type=Notification.TYPE_APP_APPROVED,
                title="Application approved",
                body=f"{application.application_number} was approved successfully.",
                link=f"/sales/applications/{application.pk}/",
                level=Notification.LEVEL_SUCCESS,
                application=application,
            )
        for user in _role_recipients("hq"):
            _send_once(
                sent_to,
                recipient=user,
                notification_type=Notification.TYPE_APP_APPROVED,
                title="Application approved",
                body=body,
                link="/tengasale/hq/applications/",
                level=Notification.LEVEL_SUCCESS,
                application=application,
            )
    except Exception:
        pass


def notify_application_rejected(application, rejected_by=None):
    try:
        sent_to = set()
        body = f"{application.customer_name or 'Customer'} - {application.application_number} was rejected."
        _send_once(
            sent_to,
            recipient=application.created_by,
            notification_type=Notification.TYPE_APP_REJECTED,
            title="Application rejected",
            body=body,
            link=application.get_continue_url(),
            level=Notification.LEVEL_DANGER,
            application=application,
        )
        if rejected_by:
            _send_once(
                sent_to,
                recipient=rejected_by,
                notification_type=Notification.TYPE_APP_REJECTED,
                title="Application rejected",
                body=f"{application.application_number} was rejected.",
                link=f"/sales/applications/{application.pk}/",
                level=Notification.LEVEL_DANGER,
                application=application,
            )
        for user in _role_recipients("hq"):
            _send_once(
                sent_to,
                recipient=user,
                notification_type=Notification.TYPE_APP_REJECTED,
                title="Application rejected",
                body=body,
                link="/tengasale/hq/applications/",
                level=Notification.LEVEL_DANGER,
                application=application,
            )
    except Exception:
        pass
