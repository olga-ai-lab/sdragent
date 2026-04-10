"""
Email Client — Envio real via SMTP (Gmail, Outlook, SendGrid).
Suporta HTML templates com identidade 88i.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from modules.logger import get_logger

log = get_logger("sdr.email")

# Configuração via .env
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "88i Seguradora Digital")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "")


class EmailClient:

    def __init__(self):
        self.configured = bool(SMTP_USER and SMTP_PASS)
        if not self.configured:
            log.warning("SMTP não configurado — emails serão simulados")

    def send(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> dict:
        """
        Envia email via SMTP.
        Se body_html não fornecido, usa template 88i padrão.
        """
        if not self.configured:
            log.info(f"[SIMULADO] Email para {to_email}: {subject}")
            return {"status": "simulated", "to": to_email, "subject": subject}

        if not body_html:
            body_html = self._wrap_html_template(body_text, subject)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
        msg["To"] = to_email
        if reply_to:
            msg["Reply-To"] = reply_to

        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)

            log.info(f"Email enviado: {to_email} | {subject}")
            return {"status": "sent", "to": to_email, "subject": subject}

        except smtplib.SMTPAuthenticationError:
            log.error(f"Erro autenticação SMTP para {to_email}")
            return {"status": "auth_error", "to": to_email}
        except smtplib.SMTPRecipientsRefused:
            log.error(f"Destinatário recusado: {to_email}")
            return {"status": "bounced", "to": to_email}
        except Exception as e:
            log.error(f"Erro SMTP: {e}")
            return {"status": "error", "to": to_email, "error": str(e)}

    def _wrap_html_template(self, body_text: str, subject: str) -> str:
        """Wrapa texto em template HTML com identidade 88i."""
        # Converter quebras de linha em <br>
        body_html = body_text.replace("\n", "<br>")

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="border-bottom: 3px solid #0E353D; padding-bottom: 16px; margin-bottom: 24px;">
        <span style="font-weight: bold; font-size: 20px; color: #0E353D;">88i</span>
        <span style="font-size: 14px; color: #666; margin-left: 8px;">Seguradora Digital</span>
    </div>

    <div style="font-size: 15px; line-height: 1.6; color: #333;">
        {body_html}
    </div>

    <div style="margin-top: 32px; padding-top: 16px; border-top: 1px solid #eee; font-size: 12px; color: #999;">
        <p>88i Seguradora Digital — Proteção para a Gig Economy</p>
        <p style="margin: 4px 0;">Powered by OlgaAI</p>
    </div>
</body>
</html>"""
