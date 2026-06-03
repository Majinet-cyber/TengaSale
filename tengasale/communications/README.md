# TengaSale SMS Dispatch

Environment variables:

```env
SMS_ENABLED=True
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
DEFAULT_SMS_LANGUAGE=ny
```

If `SMS_ENABLED` is false or Twilio credentials are missing, dispatch attempts are saved as `pending_config` and the application flow continues.

Render Cron Job:

```bash
python manage.py send_due_sms_reminders
```

Schedule it daily at `08:00` Africa/Blantyre time. The Django project uses `TIME_ZONE = "Africa/Blantyre"` and `USE_TZ = True`.
