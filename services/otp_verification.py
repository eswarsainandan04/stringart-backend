import smtplib
import socket
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
from config import Settings


def create_smtp_connection(smtp_server: str, smtp_port: int, timeout: int = 15) -> smtplib.SMTP:
    """
    Creates an SMTP connection strictly forced to IPv4 to prevent '[Errno 101] Network is unreachable'
    on cloud container platforms (like Render free tier) that do not support outbound IPv6 routing.
    """
    ipv4_ips = []
    try:
        infos = socket.getaddrinfo(smtp_server, smtp_port, socket.AF_INET, socket.SOCK_STREAM)
        ipv4_ips = [info[4][0] for info in infos if info[4] and info[4][0]]
    except Exception as e:
        print(f"Warning resolving IPv4 for {smtp_server}: {e}")

    targets = ipv4_ips if ipv4_ips else [smtp_server]
    last_err = None

    for target in targets:
        try:
            server = smtplib.SMTP(target, smtp_port, timeout=timeout)
            server._host = smtp_server
            server.ehlo()
            context = ssl.create_default_context()
            server.starttls(context=context)
            server.ehlo()
            return server
        except Exception as e:
            last_err = e
            continue

    if last_err:
        raise last_err
    raise ConnectionError(f"Could not connect to {smtp_server}:{smtp_port}")


def generate_otp() -> str:
    """Generates a secure 6-digit numeric OTP code."""
    return f"{secrets.randbelow(900000) + 100000}"


def send_otp_email(to_email: str, otp: str, purpose: str = "verification") -> bool:
    """
    Sends a 6-digit verification OTP email to the recipient using Gmail SMTP (STARTTLS).
    Returns True if sent successfully, or raises an Exception with detailed reason.
    """
    current_settings = Settings()
    smtp_server = current_settings.SMTP_HOST or "smtp.gmail.com"
    smtp_port = current_settings.SMTP_PORT or 587
    sender_email = current_settings.SMTP_EMAIL
    sender_password = (current_settings.SMTP_PASSWORD or "").replace(" ", "")

    if not sender_email or not sender_password:
        raise ValueError("SMTP_EMAIL and SMTP_PASSWORD must be configured in .env")

    is_signup = (purpose.lower().strip() == "verification")
    subject = (
        "Your Verification Code - HS Creations String Art"
        if is_signup
        else "Password Reset Code - HS Creations String Art"
    )
    action_text = (
        "complete your account registration"
        if is_signup
        else "reset your account password"
    )
    title_text = "Verify Your Account" if is_signup else "Reset Your Password"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
          background-color: #FAF7F2;
          margin: 0;
          padding: 24px;
          color: #1C1917;
        }}
        .email-container {{
          max-width: 480px;
          margin: 0 auto;
          background: #FFFFFF;
          border-radius: 18px;
          padding: 32px 28px;
          border: 1px solid #ECE4D8;
          box-shadow: 0 4px 16px rgba(0, 0, 0, 0.05);
        }}
        .brand {{
          display: inline-block;
          font-size: 13px;
          font-weight: 800;
          color: #8A660C;
          letter-spacing: 0.8px;
          text-transform: uppercase;
          margin-bottom: 18px;
        }}
        h2 {{
          margin: 0 0 10px 0;
          font-size: 22px;
          font-weight: 800;
          color: #1C1917;
          letter-spacing: -0.3px;
        }}
        p {{
          font-size: 14px;
          line-height: 1.6;
          color: #57534E;
          margin: 10px 0;
        }}
        .otp-card {{
          text-align: center;
          margin: 24px 0;
          padding: 20px;
          background: #FFFBEB;
          border: 1.5px dashed #F59E0B;
          border-radius: 14px;
        }}
        .otp-number {{
          font-size: 36px;
          font-weight: 800;
          letter-spacing: 10px;
          color: #B45309;
          font-family: 'Courier New', Courier, monospace;
          margin: 4px 0;
        }}
        .expiry-note {{
          font-size: 12px;
          font-weight: 600;
          color: #D97706;
          margin-top: 6px;
        }}
        .warning {{
          font-size: 12px;
          color: #78716C;
          line-height: 1.5;
        }}
        .footer {{
          margin-top: 26px;
          padding-top: 18px;
          border-top: 1px solid #F5F0E8;
          font-size: 11px;
          color: #A8A29E;
          text-align: center;
          line-height: 1.4;
        }}
      </style>
    </head>
    <body>
      <div class="email-container">
        <div class="brand">✨ HS Creations • String Art Studio</div>
        <h2>{title_text}</h2>
        <p>Use the 6-digit code below to {action_text}. This code is valid for <b>5 minutes</b>.</p>
        
        <div class="otp-card">
          <div class="otp-number">{otp}</div>
          <div class="expiry-note">Expires in 5 minutes • Single Use Only</div>
        </div>

        <p class="warning">
          If you did not request this verification code, please ignore this email. Do not share this code with anyone.
        </p>

        <div class="footer">
          HS Creations String Art Studio • Algorithmic Geometric Art & CNC Automation<br>
          Vijayawada, Andhra Pradesh, India
        </div>
      </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"HS Creations <{sender_email}>"
    msg["To"] = to_email

    text_part = MIMEText(
        f"Your HS Creations verification code is: {otp}. This code is valid for 5 minutes. Do not share it.",
        "plain"
    )
    html_part = MIMEText(html_content, "html")

    msg.attach(text_part)
    msg.attach(html_part)

    server = create_smtp_connection(smtp_server, smtp_port, timeout=15)
    try:
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, to_email, msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass

    return True
