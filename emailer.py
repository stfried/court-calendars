import os

from email.message import EmailMessage
from email.utils import make_msgid
import mimetypes
import smtplib


def embed_gif(msg, content, gif):
    with open(gif["Filename"], 'rb') as file:
        gif_bytes = file.read()
    description = gif["Description"]
    url = gif["URL"]
    
    cid = make_msgid(domain="frinkiac.com")[1:-1]
    html_body = f"""
    <html>
    <body>
        <p>{content}</p>
        <img src="cid:{cid}">
        <p>{description}</p>
    </body>
    </html>
    """

    msg.set_content("Your email client does not support HTML.")
    msg.add_alternative(html_body, subtype="html")

    mime_type, _ = mimetypes.guess_type(url)
    if mime_type is None:
        mime_type = "image/gif"
    maintype, subtype = mime_type.split("/")

    msg.get_payload()[1].add_related(
        gif_bytes,
        maintype=maintype,
        subtype=subtype,
        cid=f"<{cid}>"
    )
    return msg


def send_email(subject, sender, recipient, content, attachments, gif):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    
    msg = embed_gif(msg, content, gif)
    for attachment in attachments:
        with open(attachment, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="pdf",
                filename=os.path.basename(attachment)
            )    

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(os.environ["EMAIL_USERNAME"], os.environ["EMAIL_PASSWORD"])
        server.send_message(msg)
    return

if __name__ == "__main__":
    print(os.environ["EMAIL_USERNAME"])
    print(os.environ["EMAIL_PASSWORD"])