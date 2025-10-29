# -*- coding: utf-8 -*-
import csv, os, re, smtplib, sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urljoin

import feedparser, requests, yaml
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

RECENCIA_DIAS = int(os.getenv('RECENCIA_DIAS', '3'))

POSITIVE_TERMS = [
    r"\bedital(es)?\b",
    r"\bchamada(s)?\s+p(ú|u)blica(s)?\b",
    r"\bsele(ç|c)[aã]o\s+p(ú|u)blica\b",
    r"\bretifica(ç|c)[aã]o\s+de\s+edital\b",
    r"\bconvocat(ó|o)ria\b",
    r"\bportaria\b",
    r"\bbolsa(s)?\b",
    r"\bconcurso(s)?\b",
    r"\bfomento\b",
    r"\bsubmiss(ã|a)o\b",
    r"\bprorroga(ç|c)[aã]o\b",
    r"\bci[êe]ncia\b", r"\binova(ç|c)[aã]o\b", r"\bpesquisa\b",
]

NEGATIVE_TERMS = [r"\bedital\s+de\s+licita(ç|c)[aã]o\b"]

DEFAULT_RSS = {
    'CAPES (notícias RSS)': 'https://www.gov.br/capes/pt-br/assuntos/noticias/rss',
    'CNPq (notícias RSS)': 'https://www.gov.br/cnpq/pt-br/assuntos/noticias/ultimas-noticias/RSS',
    'FINEP (notícias RSS)': 'https://www.finep.gov.br/noticias?format=feed&type=rss',
    'MCTI (notícias RSS)': 'https://www.gov.br/mcti/pt-br/acompanhe-o-mcti/noticias/RSS',
    'FAPEMA (notícias RSS)': 'https://www.fapema.br/portal/feed/',
}
DEFAULT_HTML = {
    'CAPES - Editais/Chamadas': 'https://www.gov.br/capes/pt-br/assuntos/editais',
    'CNPq - Chamadas Públicas': 'https://www.gov.br/cnpq/pt-br/chamadas',
    'FINEP - Chamadas Públicas': 'https://www.finep.gov.br/chamadas-publicas',
    'MCTI - Editais e Chamadas': 'https://www.gov.br/mcti/pt-br/acompanhe-o-mcti/editais-e-chamadas',
    'FAPEMA - Editais': 'https://www.fapema.br/portal/editais/',
}

def carregar_fontes_yaml(caminho='sources_editais.yaml'):
    if not os.path.exists(caminho):
        return DEFAULT_RSS, DEFAULT_HTML
    try:
        import yaml
        with open(caminho, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        rss = data.get('rss_sources', {}) or DEFAULT_RSS
        html = data.get('html_sources', {}) or DEFAULT_HTML
        return rss, html
    except Exception as e:
        print(f'[WARN] Falha ao ler {caminho}: {e}. Usando defaults.')
        return DEFAULT_RSS, DEFAULT_HTML

def limpar_html(txt):
    if not txt: return ''
    try:
        return BeautifulSoup(txt, 'html5lib').get_text(' ', strip=True)
    except Exception:
        import re
        return re.sub('<[^>]+>', ' ', txt)

def dentro_recencia(dt, dias=RECENCIA_DIAS):
    if not dt: return False
    return (datetime.now(timezone.utc) - dt) <= timedelta(days=dias)

def parse_datetime(entry):
    for c in [getattr(entry,'published',None), getattr(entry,'updated',None), entry.get('published'), entry.get('updated')]:
        if not c: continue
        try:
            d = dtparser.parse(c)
            if not d.tzinfo: d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc)
        except Exception: pass
    return None

def tem_match(padroes, texto):
    import re
    for p in padroes:
        if re.search(p, texto, flags=re.IGNORECASE):
            return True
    return False

def http_get(url, timeout=25):
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 EditaisBot'}, timeout=timeout)
        if r.status_code == 200 and r.text:
            return r
    except Exception as ex:
        print(f'[WARN] GET falhou {url}: {ex}')
    return None

def coletar_rss(rss_map):
    itens = []
    for nome, url in rss_map.items():
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                title = limpar_html(getattr(e,'title','') or e.get('title',''))
                summary = limpar_html(getattr(e,'summary','') or e.get('summary',''))
                link = getattr(e,'link','') or e.get('link','')
                dt_pub = parse_datetime(e)
                if not title or not link: continue
                if not dentro_recencia(dt_pub): continue
                texto = f"{title} {summary}".lower()
                if tem_match(NEGATIVE_TERMS, texto): continue
                if not tem_match(POSITIVE_TERMS, texto): continue
                itens.append({'fonte':nome,'titulo':title.strip(),'resumo':summary.strip(),'link':link.strip(),'publicado_em':dt_pub.isoformat() if dt_pub else '','metodo':'RSS'})
        except Exception as ex:
            print(f'[WARN] RSS falhou {nome}: {ex}')
    return itens

def coletar_html(html_map):
    itens = []
    for nome, url in html_map.items():
        resp = http_get(url)
        if not resp: continue
        try:
            soup = BeautifulSoup(resp.text, 'html5lib')
            for a in soup.select('a[href]'):
                href = a.get('href') or ''
                texto = a.get_text(' ', strip=True) or ''
                if not href or href.startswith('#'): continue
                if href.startswith('/'):
                    href = urljoin(url, href)
                alvo = f"{texto} {href}".lower()
                if not tem_match(POSITIVE_TERMS, alvo): continue
                itens.append({'fonte':nome,'titulo':texto[:180] or '(sem título)','resumo':'','link':href,'publicado_em':'','metodo':'HTML'})
        except Exception as ex:
            print(f'[WARN] HTML falhou {nome}: {ex}')
    return itens

def formatar_email(itens):
    if not itens:
        return f'Nenhum edital/chamada encontrado nos últimos {RECENCIA_DIAS} dias.'
    def key_sort(x): return (x['publicado_em'] or '', x['fonte'], x['titulo'])
    linhas = [f'📢 Editais e Chamadas Públicas (últimos {RECENCIA_DIAS} dias)\n']
    for i, it in enumerate(sorted(itens, key=key_sort, reverse=True), 1):
        dt_fmt = ''
        if it['publicado_em']:
            try: dt_fmt = dtparser.parse(it['publicado_em']).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            except Exception: dt_fmt = it['publicado_em']
        linhas.append(f"{i}. [{it['fonte']}] {it['titulo']}\n   Data: {dt_fmt}\n   Link: {it['link']}\n")
    return '\n'.join(linhas)

def enviar_email(corpo_email, assunto=None):
    caminho = 'credenciais.txt'
    assert os.path.exists(caminho), f'Arquivo de credenciais não encontrado: {caminho}'
    with open(caminho,'r',encoding='utf-8') as f:
        linhas = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
    email_user, email_pass, email_to = linhas[0], linhas[1], linhas[2]
    if not assunto:
        assunto = f'📢 Editais e Chamadas Públicas — últimos {RECENCIA_DIAS} dias'
    msg = MIMEMultipart(); msg['From']=email_user; msg['To']=email_to; msg['Subject']=assunto
    msg.attach(MIMEText(corpo_email,'plain','utf-8'))
    s = smtplib.SMTP('smtp.gmail.com',587); s.starttls(); s.login(email_user,email_pass); s.send_message(msg); s.quit()

def salvar_csv(itens, caminho='editais_brasil_log.csv'):
    campos = ['timestamp_execucao_utc','fonte','titulo','link','publicado_em','metodo']
    ts = datetime.now(timezone.utc).isoformat()
    novo = not os.path.exists(caminho)
    with open(caminho,'a',encoding='utf-8',newline='') as f:
        w = csv.DictWriter(f, fieldnames=campos)
        if novo: w.writeheader()
        for it in itens:
            w.writerow({'timestamp_execucao_utc':ts,'fonte':it['fonte'],'titulo':it['titulo'],'link':it['link'],'publicado_em':it['publicado_em'],'metodo':it['metodo']})

def main():
    rss_map, html_map = carregar_fontes_yaml()
    print(f'🔍 Buscando editais/chamadas (últimos {RECENCIA_DIAS} dias)…')
    itens = coletar_rss(rss_map) + coletar_html(html_map)
    corpo = formatar_email(itens)
    print('\n===== PRÉVIA DO EMAIL =====\n'); print(corpo); print('\n===========================\n')
    try:
        enviar_email(corpo); print('✅ Email enviado com sucesso!')
    except Exception as e:
        print('❌ Erro ao enviar e-mail:', e, file=sys.stderr)
    try:
        salvar_csv(itens)
    except Exception as e:
        print('[WARN] Falha ao salvar CSV:', e, file=sys.stderr)

if __name__ == '__main__':
    main()
