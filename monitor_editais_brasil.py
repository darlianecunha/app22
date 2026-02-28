def enviar_email(corpo_email, assunto=None):
    email_user = os.getenv("EMAIL_USER")
    email_pass = os.getenv("EMAIL_PASS")
    email_to = os.getenv("EMAIL_TO")

    if not email_user or not email_pass or not email_to:
        raise RuntimeError("Secrets ausentes. Verifique EMAIL_USER, EMAIL_PASS e EMAIL_TO no GitHub.")

    if not assunto:
        assunto = f"📢 Editais e Chamadas Públicas - últimos {RECENCIA_DIAS} dias"

    msg = MIMEMultipart()
    msg["From"] = email_user
    msg["To"] = email_to
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo_email, "plain", "utf-8"))

    s = smtplib.SMTP("smtp.gmail.com", 587)
    s.starttls()
    s.login(email_user, email_pass)
    s.send_message(msg)
    s.quit()
if __name__ == "__main__":
    main()
