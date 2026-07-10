#!/usr/bin/env python3
"""
update_gastos.py - Lee emails de los bancos en Gmail y actualiza index.html
Bancos soportados:
  - Produbanco Visa (bancaenlinea@produbanco.com)
  - Pacífico Mastercard (notificaciones@infopacificard.com.ec)
  - Pacífico Débito (intermail@bancopacifico.ec)
"""

import imaplib
import email
import email.utils
import re
import os
import sys
import hashlib
import html as html_mod
from datetime import datetime, timezone, timedelta

GMAIL_USER = os.environ['GMAIL_USER']
GMAIL_PASS = os.environ['GMAIL_APP_PASSWORD']

INDEX_PATH = os.path.join(os.path.dirname(__file__), '..', 'index.html')

ECT = timezone(timedelta(hours=-5))  # Ecuador time

MONTHS_EN_IMAP = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
MONTHS_ES_SHORT = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic']
MONTHS_ES_LONG = {
    'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
    'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12,
    'Enero':1,'Febrero':2,'Marzo':3,'Abril':4,'Mayo':5,'Junio':6,
    'Julio':7,'Agosto':8,'Septiembre':9,'Octubre':10,'Noviembre':11,'Diciembre':12,
}

MERCHANT_CATS = {
    'tipti':'supermercado','supermaxi':'supermercado','coral':'supermercado','megamaxi':'supermercado',
    'bocatti':'comida','burger':'comida','pizza':'comida','kfc':'comida','mcdonalds':'comida',
    'uber eats':'comida','rappi':'comida','restaurant':'comida','cocina':'comida','sushi':'comida',
    'gasolinera':'transporte','petroecuador':'transporte','uber':'transporte','cabify':'transporte',
    'netflix':'suscripcion','spotify':'suscripcion','apple':'suscripcion','amazon':'suscripcion',
    'paypal':'suscripcion','google':'suscripcion','hostinger':'suscripcion','ifttt':'suscripcion',
    'microsoft':'suscripcion','adobe':'suscripcion','openai':'suscripcion',
    'farmacia':'salud','clinica':'salud','hospital':'salud','laboratorio':'salud',
    'airbnb':'viajes','hotel':'viajes','booking':'viajes',
    'colineal':'hogar','chordeleg':'compras',
    'apadel':'entretenimiento','paddle':'entretenimiento',
}

def guess_cat(merchant: str) -> str:
    low = merchant.lower()
    for kw, cat in MERCHANT_CATS.items():
        if kw in low:
            return cat
    return 'compras'

def strip_html(h: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', h)
    text = html_mod.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()

def clean_merchant(raw: str) -> str:
    """Corta el texto de seguridad/legal que algunos bancos agregan al nombre."""
    m = ' '.join(raw.strip().split())
    # Cortar en frases de disclaimer conocidas (case-insensitive)
    for phrase in ['si no realizaste', 'si usted no', 'atentamente', 'produbanco',
                   'le informamos', 'recuerda que', 'por su seguridad']:
        idx = m.lower().find(phrase)
        if idx > 0:
            m = m[:idx].strip()
    # Colapsar espacios múltiples que los bancos usan como padding
    m = ' '.join(m.split())
    return m.strip(' .,-')

def tx_id(acct: str, date: str, time: str, amt: float) -> str:
    key = f'{acct}|{date}|{time}|{amt:.2f}'
    return acct[:2] + hashlib.md5(key.encode()).hexdigest()[:6]

def parse_produ(html_body: str, email_date: datetime) -> dict | None:
    text = strip_html(html_body)
    m_date = re.search(r'Fecha y Hora:\s*(\d+)/(\w+)/(\d{4})\s+(\d{2}:\d{2})', text)
    m_amt  = re.search(r'Valor:\s*USD\s*([\d,]+\.?\d*)', text)
    m_est  = re.search(r'Establecimiento:\s*(.+?)(?:Atentamente|Produbanco|$)', text)
    if not (m_date and m_amt and m_est):
        return None
    day = int(m_date.group(1))
    mon = MONTHS_ES_LONG.get(m_date.group(2), email_date.month)
    year = int(m_date.group(3))
    time_s = m_date.group(4)
    date_s = f'{year}-{mon:02d}-{day:02d}'
    amt = float(m_amt.group(1).replace(',', ''))
    merchant = clean_merchant(m_est.group(1))
    cat = guess_cat(merchant)
    return {'acct':'produ-tc','date':date_s,'time':time_s,
            'est':merchant.title(),'raw':merchant.upper(),'amt':amt,'cat':cat}

def parse_bp_tc(html_body: str, email_date: datetime) -> dict | None:
    text = strip_html(html_body)
    m_date = re.search(r'Fecha de la transacci[oó]n\s+(\d{4}-\d{2}-\d{2})\s+a las\s+(\d{2}:\d{2})', text)
    m_amt  = re.search(r'Monto\s+\$\s*([\d,]+\.?\d*)', text)
    m_est  = re.search(r'Establecimiento:\s*(.+?)(?:Fecha de la|$)', text)
    if not (m_date and m_amt and m_est):
        return None
    date_s = m_date.group(1)
    time_s = m_date.group(2)
    amt = float(m_amt.group(1).replace(',', ''))
    merchant = clean_merchant(m_est.group(1))
    cat = guess_cat(merchant)
    return {'acct':'bp-tc','date':date_s,'time':time_s,
            'est':merchant.title(),'raw':merchant.upper(),'amt':amt,'cat':cat}

def parse_bp_retiro(html_body: str, email_date: datetime) -> dict | None:
    text = strip_html(html_body)
    m_amt = re.search(r'Monto:\s*\$([\d,]+\.?\d*)', text)
    if not m_amt:
        return None
    amt = float(m_amt.group(1).replace(',', ''))
    dt = email_date.astimezone(ECT)
    return {'acct':'bp-deb','date':dt.strftime('%Y-%m-%d'),'time':dt.strftime('%H:%M'),
            'est':'Retiro Efectivo Móvil','raw':'RETIRO SIN TARJETA','amt':amt,'cat':'efectivo'}

def parse_bp_transfer(html_body: str, email_date: datetime) -> dict | None:
    text = strip_html(html_body)
    m_amt    = re.search(r'Valor:\s*\$([\d,]+\.?\d*)', text)
    m_recip  = re.search(r'Perteneciente a:\s*(.+?)(?:Banco de destino|Valor|$)', text)
    m_date   = re.search(r'Fecha y hora:\s*(\d{2})-(\d{2})-(\d{4})\s+a las\s+(\d{2}:\d{2})', text)
    if not m_amt:
        return None
    amt = float(m_amt.group(1).replace(',', ''))
    recipient = ' '.join((m_recip.group(1).strip() if m_recip else 'Transferencia').split())
    if m_date:
        d,m,y = int(m_date.group(1)),int(m_date.group(2)),int(m_date.group(3))
        date_s = f'{y}-{m:02d}-{d:02d}'
        time_s = m_date.group(4)
    else:
        dt = email_date.astimezone(ECT)
        date_s = dt.strftime('%Y-%m-%d')
        time_s = dt.strftime('%H:%M')
    cat = guess_cat(recipient)
    if cat == 'compras':
        cat = 'transferencia'
    return {'acct':'bp-deb','date':date_s,'time':time_s,
            'est':recipient.title(),'raw':f'TRANSFERENCIA {recipient.upper()}','amt':amt,'cat':cat}

def fetch_emails(imap, since_imap: str, sender: str) -> list[dict]:
    results = []
    for folder in ['INBOX', '"[Gmail]/All Mail"']:
        try:
            imap.select(folder, readonly=True)
        except Exception:
            continue
        _, data = imap.search(None, f'(FROM "{sender}" SINCE {since_imap})')
        if not data or not data[0]:
            continue
        for num in data[0].split():
            _, raw_data = imap.fetch(num, '(RFC822)')
            if not raw_data or not raw_data[0]:
                continue
            msg = email.message_from_bytes(raw_data[0][1])
            msg_id = msg.get('Message-ID', '').strip('<> ')
            if not msg_id:
                continue
            try:
                email_date = email.utils.parsedate_to_datetime(msg.get('Date', ''))
                if email_date.tzinfo is None:
                    email_date = email_date.replace(tzinfo=timezone.utc)
            except Exception:
                email_date = datetime.now(timezone.utc)
            html_body = ''
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == 'text/html':
                        html_body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                        break
            elif msg.get_content_type() == 'text/html':
                html_body = msg.get_payload(decode=True).decode('utf-8', errors='replace')
            results.append({
                'msg_id': msg_id,
                'email_date': email_date,
                'html_body': html_body,
                'subject': msg.get('Subject', ''),
            })
        break  # found emails in this folder, don't check next
    return results

def insert_into_array(content: str, array_name: str, lines: list[str]) -> str:
    if not lines:
        return content
    new_lines = '\n' + '\n'.join(lines)
    # Find the closing ]; of the named array and insert before it
    pattern = rf'(const {re.escape(array_name)} = \[)([\s\S]*?)(\n\];)'
    def replacer(m):
        return m.group(1) + m.group(2) + new_lines + m.group(3)
    return re.sub(pattern, replacer, content)

def main():
    with open(INDEX_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # Processed message IDs (stored in HTML comment to avoid re-processing)
    proc_match = re.search(r'<!-- GMAIL_PROCESSED: (.*?) -->', content)
    processed = set()
    if proc_match:
        processed = {x.strip() for x in proc_match.group(1).split(',') if x.strip()}

    # Active month from localStorage key
    m = re.search(r"'jay_exp_excluded_(\d{4})_(\d{2})'", content)
    if not m:
        print("ERROR: No se pudo determinar el mes activo en index.html")
        sys.exit(1)
    year, month = int(m.group(1)), int(m.group(2))
    since_imap = f'01-{MONTHS_EN_IMAP[month-1]}-{year}'
    month_prefix = f'{year}-{month:02d}'
    print(f"Mes activo: {month_prefix}  |  Buscando desde: {since_imap}")

    # Connect IMAP
    imap = imaplib.IMAP4_SSL('imap.gmail.com')
    imap.login(GMAIL_USER, GMAIL_PASS)

    new_produ, new_bptc, new_bpdeb = [], [], []

    # --- Produbanco ---
    for e in fetch_emails(imap, since_imap, 'bancaenlinea@produbanco.com'):
        if e['msg_id'] in processed:
            continue
        tx = parse_produ(e['html_body'], e['email_date'])
        if tx and tx['date'].startswith(month_prefix):
            tid = tx_id(tx['acct'], tx['date'], tx['time'], tx['amt'])
            line = f"['{tid}','{tx['date']}','{tx['time']}','{tx['est']}','{tx['raw']}',{tx['amt']:.2f},'{tx['cat']}'],"
            new_produ.append(line)
            processed.add(e['msg_id'])
            print(f"  + Produbanco: {tx['date']} {tx['est']} ${tx['amt']:.2f}")

    # --- Pacífico TC ---
    for e in fetch_emails(imap, since_imap, 'notificaciones@infopacificard.com.ec'):
        if e['msg_id'] in processed:
            continue
        tx = parse_bp_tc(e['html_body'], e['email_date'])
        if tx and tx['date'].startswith(month_prefix):
            tid = tx_id(tx['acct'], tx['date'], tx['time'], tx['amt'])
            line = f"['{tid}','{tx['date']}','{tx['time']}','{tx['est']}','{tx['raw']}',{tx['amt']:.2f},'{tx['cat']}'],"
            new_bptc.append(line)
            processed.add(e['msg_id'])
            print(f"  + BP TC: {tx['date']} {tx['est']} ${tx['amt']:.2f}")

    # --- Pacífico Débito ---
    for e in fetch_emails(imap, since_imap, 'intermail@bancopacifico.ec'):
        if e['msg_id'] in processed:
            continue
        subj = e['subject'].lower()
        if 'retiro sin tarjeta' in subj:
            tx = parse_bp_retiro(e['html_body'], e['email_date'])
        elif 'envío de dinero' in subj or 'envio de dinero' in subj:
            tx = parse_bp_transfer(e['html_body'], e['email_date'])
        else:
            continue
        if tx and tx['date'].startswith(month_prefix):
            tid = tx_id(tx['acct'], tx['date'], tx['time'], tx['amt'])
            line = f"['{tid}','{tx['date']}','{tx['time']}','{tx['est']}','{tx['raw']}',{tx['amt']:.2f},'{tx['cat']}'],"
            new_bpdeb.append(line)
            processed.add(e['msg_id'])
            print(f"  + BP Déb: {tx['date']} {tx['est']} ${tx['amt']:.2f}")

    imap.logout()

    total = len(new_produ) + len(new_bptc) + len(new_bpdeb)
    if total == 0:
        print("Sin transacciones nuevas.")
        sys.exit(0)

    # Insert into TX arrays
    content = insert_into_array(content, 'TX_PRODU', new_produ)
    content = insert_into_array(content, 'TX_BP', new_bptc)
    content = insert_into_array(content, 'TX_BPD', new_bpdeb)

    # Update/insert processed IDs comment
    proc_str = ','.join(sorted(processed))
    if '<!-- GMAIL_PROCESSED:' in content:
        content = re.sub(r'<!-- GMAIL_PROCESSED: .*? -->', f'<!-- GMAIL_PROCESSED: {proc_str} -->', content)
    else:
        content = content.replace('</script>', f'<!-- GMAIL_PROCESSED: {proc_str} -->\n</script>', 1)

    # Update banner
    now = datetime.now(ECT)
    ts = f"{now.day} {MONTHS_ES_SHORT[now.month-1]} {now.year}, {now.strftime('%H:%M')}"
    content = re.sub(r'↻ Última actualización: .+', f'↻ Última actualización: {ts}', content)

    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"\n✓ {total} transacciones nuevas añadidas.")
    print(f"✓ Banner actualizado: {ts}")

if __name__ == '__main__':
    main()
