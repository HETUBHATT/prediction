import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def send_email(recipient: str, subject: str, body: str) -> None:
    host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    port = int(os.getenv('SMTP_PORT', '587'))
    username = os.getenv('SMTP_USERNAME', 'Nmcbca2010@gmail.com')
    password = os.getenv('SMTP_PASSWORD')
    sender = os.getenv('SMTP_FROM', username)

    if not password:
        raise RuntimeError('SMTP_PASSWORD is not set. Configure a Gmail app password before sending email.')

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = sender
    message['To'] = recipient
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(message)


def send_otp_email(recipient: str, otp: str, subject: str = 'Prediction verification code') -> None:
    send_email(
        recipient,
        subject,
        f'Your Prediction verification code is {otp}. It expires in 10 minutes.\n\n'
        'If you did not request this code, you can ignore this email.'
    )