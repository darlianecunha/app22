# -*- coding: utf-8 -*-
import csv
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dtparser


# ==========================
# CONFIGURAÇÃO
# ==========================

RECENCIA_DIAS = int(os.getenv("RECENCIA_DIAS", "14"))

POSITIVE_TERMS = [
    r"\bedital(es)?\b",
    r"\bchamada(s)?\s+p(ú|u)blica(s)?\b",
    r"\bsele(ç|c)[aã]o\s+p(ú|u)blica\b",
    r"\bconvocat(ó|o)ria\b",
    r"\bbolsa(s)?\b",
    r"\bfomento\b",
    r"\bpesquisa\b",
    r"\binova(ç|c)[aã]o\b",
]

NEGATIVE_TERMS = [
    r"\blicita(ç|c)[aã]o\b",
]

DEFAULT_RSS = {
    "CAPES": "https://www.gov.br/capes/pt-br/assuntos/noticias/rss",
    "CNPq": "https://www.gov.br/cnpq/pt-br/assuntos/noticias/ultimas-noticias/RSS",
    "FINEP": "https://www.finep.gov.br/noticias?format=feed&type=rss",
    "MCTI": "https://www.gov.br/mcti/pt-br/acompanhe-o-mcti/noticias/RSS",
    "FAPEMA": "https://www.fapema.br/portal/feed/",
}

DEFAULT_HTML = {
    "CAPES - Editais": "https://www.gov.br/capes/pt-br/assuntos/editais",
    "CNPq - Chamadas": "https://www.gov.br/cnpq/pt-br/chamadas",
    "FINEP - Chamadas": "https://www.finep.gov.br/chamadas-publicas",
    "MCTI - Editais": "https://www.gov.br/mcti/pt-br/acompanhe-o-mcti/editais-e-chamadas",
    "FAPEMA - Editais": "https://www.fapema.br/portal/editais/",
}


# ==========================
# FUNÇÕES AUXILIARES
# ==========================

def tem_match(padroes, texto):
    for p in padroes:
        if re.search(p, texto, flags=re.IGNORECASE):
            return True
    return False


def dentro_recencia(dt):
    if not dt:
        return False
    limite = datetime.now(timezone.utc) - timedelta(days=RECENCIA_DIAS)
    return dt >= limite


def parse_data(entry):
    try:
        if hasattr(entry, "published"):
            dt = dtparser.parse(entry.published)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except Exception:
        pass
    return None


def http_get(url):
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 EditaisBot"},
            timeout=20,
        )
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print(f"[WARN] Falha GET {url}: {e}")
    return None


# ==========================
# COLETA
# ==========================

def coletar_rss(rss_map):
    itens = []
    for nome, url in rss_map.items():
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                titulo = getattr(e, "title", "")
                link = getattr(e, "link", "")
                resumo = getattr(e, "summary", "")
                dt_pub = parse_data(e)

                if not titulo or not link:
                    continue
                if not dentro_recencia(dt_pub):
                    continue

                texto = f"{titulo} {resumo}".lower()

                if tem_match(NEGATIVE_TERMS, texto):
                    continue
                if not tem_match(POSITIVE_TERMS, texto):
                    continue

                itens.append({
                    "fonte": nome,
                    "titulo": titulo.strip(),
                    "link": link.strip(),
                    "publicado_em": dt_pub.isoformat() if dt_pub else "",
                    "metodo": "RSS",
                })

        except Exception as e:
            print(f"[WARN] RSS falhou {nome}: {e}")

    return itens


def coletar_html(html_map):
    itens = []

    for nome, url in html_map.items():
        html = http_get(url)
        if not html:
            continue

        try:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select("a[href]"):
                texto = a.get_text(" ", strip=True)
                href = a.get("href")

                if not texto or not href:
                    continue

                if href.startswith("/"):
                    href = urljoin(url, href)

                alvo = f"{texto} {href}".lower()

                if tem_match(NEGATIVE_TERMS, alvo):
                    continue
                if not tem_match(POSITIVE_TERMS, alvo):
                    continue

                itens.append({
                    "fonte": nome,
                    "titulo": texto[:200],
                    "link": href,
                    "publicado_em": "",
                    "metodo": "HTML",
                })

        except Exception as e:
            print(f"[WARN] HTML falhou {nome}: {e}")

    return itens


# ==========================
# EMAIL
# ==========================

def formatar_email(itens):
    if not itens:
        return f"Nenhum edital encontrado nos últimos {RECENCIA_DIAS} dias."

    linhas = [f"📢 Editais e Chamadas Públicas - últimos {RECENCIA_DIAS} dias\n"]

    for i, it in enumerate(itens, 1):
        linhas.append(
            f"{i}. [{it['fonte']}] {it['titulo']}\n"
            f"   Link: {it['link']}\n"
        )

    return "\n".join(linhas)


def enviar_email(corpo):
    email_user = os.getenv("EMAIL_USER")
    email_pass = os.getenv("EMAIL_PASS")
    email_to = os.getenv("EMAIL_TO")

    if not email_user or not email_pass or not email_to:
        raise RuntimeError("Secrets EMAIL_USER, EMAIL_PASS ou EMAIL_TO não configurados.")

    msg = MIMEMultipart()
    msg["From"] = email_user
    msg["To"] = email_to
    msg["Subject"] = f"📢 Monitor Editais Brasil - últimos {RECENCIA_DIAS} dias"
    msg.attach(MIMEText(corpo, "plain", "utf-8"))

    s = smtplib.SMTP("smtp.gmail.com", 587)
    s.starttls()
    s.login(email_user, email_pass)
    s.send_message(msg)
    s.quit()


# ==========================
# MAIN
# ==========================

def main():
    print(f"🔎 Buscando editais (últimos {RECENCIA_DIAS} dias)...")

    rss_map = DEFAULT_RSS
    html_map = DEFAULT_HTML

    itens = coletar_rss(rss_map) + coletar_html(html_map)

    corpo = formatar_email(itens)

    print("\n===== PRÉVIA DO EMAIL =====\n")
    print(corpo)
    print("\n===========================\n")

    enviar_email(corpo)

    print("✅ Email enviado com sucesso!")


if __name__ == "__main__":
    main()
