# main.py
import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from celery import Celery
from celery.schedules import crontab
import requests


# --- 1) Flask app & DB ------------------------------------
app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI=os.environ.get('DATABASE_URL', 'sqlite:///registrations.db'),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)
db = SQLAlchemy(app)

class Registration(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    phone          = db.Column(db.String(20), nullable=False)
    webinar_dt     = db.Column(db.DateTime, nullable=False)
    message_template = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(256), nullable=False)
    sent_3d        = db.Column(db.Boolean, default=False)
    sent_2d        = db.Column(db.Boolean, default=False)
    sent_1d        = db.Column(db.Boolean, default=False)

with app.app_context():
    db.create_all()

# --- 2) Celery factory & config ---------------------------
def make_celery(app):
    celery = Celery(
        app.import_name,
        broker=os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
        backend=os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
    )
    celery.conf.update(
        timezone='UTC',
        beat_schedule={
            'check-reminders-daily': {
                'task': 'main.check_and_send_reminders',
                'schedule': crontab(hour=0, minute=0),
            },
        }
    )

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)
    celery.Task = ContextTask
    return celery

celery = make_celery(app)

# Webhook URL for Make.com
MAKE_WEBHOOK_URL = os.environ.get(
    'MAKE_WEBHOOK_URL',
    'https://hook.eu2.make.com/ivmd738m2xhkd2e7kto12h2x9wd6rthz'
)

# --- 3) Celery tasks ---------------------------------------
@celery.task(name='main.send_whatsapp_reminder')
def send_whatsapp_reminder(phone, wd, wt, name, days_left):
    try:
        """
        Sends a WhatsApp reminder via the Make.com webhook.
        """
        wd = datetime.strptime(wd, '%Y-%m-%d')
        wt = datetime.strptime(wt, '%H:%M')

        payload = {
            'phone': phone,
            'webinar_date': wd.strftime('%d %B %Y'),
            'webinar_time': wt.strftime('%I:%M %p'),
            'name': name,
            'days_left':days_left
        }
        print("Payload ", payload)
        resp = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        print(f"[{datetime.utcnow()}] Reminder sent to {phone}")
    except Exception as e:
        print(f"Error sending reminder to {phone}: {e}")


@celery.task(name='main.check_and_send_reminders')
def check_and_send_reminders():
    now = datetime.utcnow()
    today = now.date()
    
    for reg in Registration.query.filter(Registration.webinar_dt >= now):
        wd = datetime.strptime(reg.webinar_date, '%Y-%m-%d').date()
        wt = datetime.strptime(reg.webinar_date, '%H:%M').time()

        days_out = (reg.webinar_dt.date() - today).days
        if days_out == 3 and not reg.sent_3d:
            # txt = reg.message_template.format(days=3,
            #                                   date=reg.webinar_dt.strftime('%Y-%m-%d'),
            #                                   time=reg.webinar_dt.strftime('%H:%M'))
            send_whatsapp_reminder.delay(reg.phone, wd, wt, reg.name, 3)
            reg.sent_3d = True
        elif days_out == 2 and not reg.sent_2d:
            # txt = reg.message_template.format(days=2,
            #                                   date=reg.webinar_dt.strftime('%Y-%m-%d'),
            #                                   time=reg.webinar_dt.strftime('%H:%M'))
            send_whatsapp_reminder.delay(reg.phone, wd, wt, reg.name, 2)
            reg.sent_2d = True
        elif days_out == 1 and not reg.sent_1d:
            # txt = reg.message_template.format(days=1,
            #                                   date=reg.webinar_dt.strftime('%Y-%m-%d'),
            #                                   time=reg.webinar_dt.strftime('%H:%M'))
            send_whatsapp_reminder.delay(reg.phone, wd, wt, reg.name, 1)
            reg.sent_1d = True
    db.session.commit()


# --- 4) Flask endpoint -------------------------------------
@app.route('/register_webinar', methods=['POST'])
def register_webinar():
    data = request.get_json()
    try:
        wd = datetime.strptime(data['webinar_date'], '%Y-%m-%d').date()
        wt = datetime.strptime(data['webinar_time'], '%H:%M').time()
        # link = data.get('link', 'https://www.whalestreet.in')
        name = data.get('name', 'user')
        webinar_dt = datetime.combine(wd, wt)
        phone = data['phone']
        template = data.get('message_template',
            "Reminder: webinar in {days} day(s) on {date} at {time}.")
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    reg = Registration(
        phone=phone,
        webinar_dt=webinar_dt,
        message_template=template,
        name=name,
    )
    db.session.add(reg)
    db.session.commit()

    # ─── TEST REMINDER ─────────────────────────────────────
    # schedule a one‑off test remi
    # nder 60 seconds from now
    # test_text = "🔔 [Test] You just registered! This reminder is 1 minute later."
    # send_whatsapp_reminder.apply_async(
    #     args=[phone, test_text, wd.strftime('%Y-%m-%d'), wt.strftime('%H:%M'), name],
    #     countdown=5
    #     # alternatively, use eta=datetime.utcnow() + timedelta(minutes=1)
    # )
    # ────────────────────────────────────────────────────────
    
    return jsonify({'status': 'registered', 'id': reg.id}), 201

# --- 5) Run Flask ------------------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))