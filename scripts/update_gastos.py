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
import email.header
import json
import urllib.request
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
    # Comida (restaurantes / delivery) — se evalúa primero
    'bocatti':'comida','burger':'comida','pizza':'comida','kfc':'comida','mcdonald':'comida',
    'uber eats':'comida','rappi':'comida','pedidosya':'comida','restaurant':'comida','cocina':'comida',
    'sushi':'comida','taco bell':'comida','papa john':'comida','krispy':'comida','erretz':'comida',
    'subway':'comida','juan valdez':'comida','sweet':'comida','cafe':'comida','café':'comida',
    # Supermercado
    'tipti':'supermercado','supermaxi':'supermercado','coral':'supermercado','megamaxi':'supermercado',
    'mi comisariato':'supermercado','aki':'supermercado','tia':'supermercado',
    # Transporte
    'gasolinera':'transporte','petroecuador':'transporte','primax':'transporte','terpel':'transporte',
    'uber':'transporte','cabify':'transporte','didi':'transporte',
    # Suscripciones / servicios digitales
    'netflix':'suscripcion','spotify':'suscripcion','apple':'suscripcion','amazon':'suscripcion',
    'paypal':'suscripcion','google':'suscripcion','hostinger':'suscripcion','ifttt':'suscripcion',
    'microsoft':'suscripcion','adobe':'suscripcion','openai':'suscripcion','audible':'suscripcion',
    'patreon':'suscripcion','godaddy':'suscripcion','starlink':'suscripcion','pdfmonkey':'suscripcion',
    'stellarwp':'suscripcion','playstation':'suscripcion','oculus':'suscripcion','nuvei':'suscripcion',
    # Salud
    'farmacia':'salud','fybeca':'salud','sana sana':'salud','cruz azul':'salud','pharmacy':'salud',
    'clinica':'salud','clínica':'salud','hospital':'salud','laboratorio':'salud','medico':'salud',
    # Viajes
    'airbnb':'viajes','hotel':'viajes','booking':'viajes','despegar':'viajes','latam':'viajes','avianca':'viajes',
    # Hogar / muebles / decoración
    'colineal':'hogar','todohogar':'hogar','ambiente living':'hogar','casa firenza':'hogar',
    'naniconcept':'hogar','centro sur':'hogar','electricas':'hogar',
    # Entretenimiento
    'apadel':'entretenimiento','paddle':'entretenimiento','karting':'entretenimiento','cinemark':'entretenimiento',
    'supercines':'entretenimiento',
    # Mascotas / compras varias
    'pet market':'compras','mascota':'compras','patas':'compras','miniso':'compras',
    'hm':'compras','h&m':'compras','ecuavapes':'compras','libreria':'compras','librería':'compras',
}

# Reglas editables desde config/rules.json (rename de comercios + categorías extra)
RULES = {'rename': {}, 'categories': {}}
try:
    _rules_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'rules.json')
    with open(_rules_path, encoding='utf-8') as _f:
        _loaded = json.load(_f)
        RULES['rename'] = _loaded.get('rename', {})
        RULES['categories'] = _loaded.get('categories', {})
        # Las categorías del config tienen prioridad sobre las por defecto
        MERCHANT_CATS = {**MERCHANT_CATS, **RULES['categories']}
except Exception as _e:
    print(f'  (aviso: no se pudo leer config/rules.json: {_e})')

def guess_cat(merchant: str) -> str:
    low = merchant.lower()
    for kw, cat in MERCHANT_CATS.items():
        if kw in low:
            return cat
    return 'compras'

def apply_rename(est: str, raw: str) -> str:
    """Si el detalle crudo contiene una clave de 'rename', usa el nombre bonito."""
    up = raw.upper()
    for key, nice in RULES['rename'].items():
        if key.upper() in up:
            return nice
    return est

_fx_cache = {}
def eur_to_usd(date_s: str) -> float:
    """Tasa EUR→USD del día (histórica). Cachea y hace fallback si no hay red."""
    if date_s in _fx_cache:
        return _fx_cache[date_s]
    rate = 1.08  # fallback razonable
    try:
        url = f'https://api.frankfurter.app/{date_s}?from=EUR&to=USD'
        with urllib.request.urlopen(url, timeout=8) as r:
            rate = json.loads(r.read())['rates']['USD']
    except Exception as e:
        print(f'  (aviso: no se pudo obtener tasa EUR→USD, uso {rate}: {e})')
    _fx_cache[date_s] = rate
    return rate

def decode_mime_header(raw: str) -> str:
    """Decodifica encabezados MIME (=?UTF-8?...?=) a texto plano."""
    if not raw:
        return ''
    out = []
    for part, enc in email.header.decode_header(raw):
        if isinstance(part, bytes):
            out.append(part.decode(enc or 'utf-8', errors='replace'))
        else:
            out.append(part)
    return ''.join(out)

def strip_html(h: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', h)
    text = html_mod.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()

def clean_merchant(raw: str) -> str:
    """Corta el texto de seguridad/legal que algunos bancos agregan al nombre."""
    m = ' '.join(raw.strip().split())
    # Cortar en frases de disclaimer conocidas (case-insensitive)
    for phrase in ['si no realizaste', 'si usted no', 'atentamente', 'produbanco',
                   'le informamos', 'recuerda que', 'por su seguridad',
                   'esta transacción', 'esta transaccion', 'tiene un cargo',
                   'por concepto de']:
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
    # Produbanco usa dos formatos de fecha:
    #   "6/Julio/2026  9:01"   (día/mes-nombre/año)
    #   "07/01/2026 12:41"     (MM/DD/YYYY numérico)
    m_date = re.search(r'Fecha y Hora:\s*(\S+)\s+(\d{1,2}:\d{2})', text)
    # Monto puede venir en USD o EUR
    m_amt  = re.search(r'Valor:\s*(USD|EUR|\$)?\s*([\d,]+\.?\d*)', text)
    m_est  = re.search(r'Establecimiento:\s*(.+?)(?:Atentamente|Produbanco|$)', text)
    if not (m_date and m_amt and m_est):
        return None
    tok = m_date.group(1)
    time_s = m_date.group(2)
    if len(time_s.split(':')[0]) == 1:
        time_s = '0' + time_s
    parts = tok.split('/')
    if len(parts) == 3 and parts[1].isdigit():
        # MM/DD/YYYY numérico
        mon, day, year = int(parts[0]), int(parts[1]), int(parts[2])
    elif len(parts) == 3:
        # día/mes-nombre/año
        day = int(parts[0]); mon = MONTHS_ES_LONG.get(parts[1], email_date.month); year = int(parts[2])
    else:
        return None
    date_s = f'{year}-{mon:02d}-{day:02d}'
    currency = m_amt.group(1)
    amt = float(m_amt.group(2).replace(',', ''))
    note = None
    if currency == 'EUR':
        eur = amt
        amt = round(eur * eur_to_usd(date_s), 2)
        note = f'€{eur:.2f} ≈ ${amt:.2f}'
    merchant = clean_merchant(m_est.group(1))
    cat = guess_cat(merchant)
    return {'acct':'produ-tc','date':date_s,'time':time_s,
            'est':apply_rename(merchant.title(), merchant.upper()),'raw':merchant.upper(),'amt':amt,'cat':cat,'note':note}

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
            'est':apply_rename(merchant.title(), merchant.upper()),'raw':merchant.upper(),'amt':amt,'cat':cat}

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
                'subject': decode_mime_header(msg.get('Subject', '')),
            })
        break  # found emails in this folder, don't check next
    return results

def make_line(tid: str, tx: dict) -> str:
    """Arma la fila JS. Escapa comillas simples y agrega nota opcional (8º campo)."""
    def esc(s): return str(s).replace("'", "’")
    row = f"['{tid}','{tx['date']}','{tx['time']}','{esc(tx['est'])}','{esc(tx['raw'])}',{tx['amt']:.2f},'{tx['cat']}'"
    note = tx.get('note')
    if note:
        row += f",'{esc(note)}'"
    return row + '],'

def insert_into_array(content: str, array_name: str, lines: list[str]) -> str:
    if not lines:
        return content
    new_lines = '\n' + '\n'.join(lines)
    # Find the closing ]; of the named array and insert before it
    pattern = rf'(const {re.escape(array_name)} = \[)([\s\S]*?)(\n\];)'
    def replacer(m):
        body = m.group(2).rstrip()
        # La última fila existente puede no tener coma final: agregarla
        if body and not body.endswith(','):
            body += ','
        return m.group(1) + body + new_lines + m.group(3)
    return re.sub(pattern, replacer, content)

def main():
    with open(INDEX_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # Processed message IDs (stored in HTML comment to avoid re-processing)
    proc_match = re.search(r'<!-- GMAIL_PROCESSED: (.*?) -->', content)
    processed = set()
    if proc_match:
        processed = {x.strip() for x in proc_match.group(1).split(',') if x.strip()}

    # Mes real actual (hora Ecuador). Se puede forzar con la variable TARGET_MONTH=YYYY-MM
    target = os.environ.get('TARGET_MONTH')
    if target and re.match(r'\d{4}-\d{2}', target):
        year, month = int(target[:4]), int(target[5:7])
    else:
        now = datetime.now(ECT)
        year, month = now.year, now.month
    since_imap = f'01-{MONTHS_EN_IMAP[month-1]}-{year}'
    month_prefix = f'{year}-{month:02d}'
    print(f"Mes objetivo: {month_prefix}  |  Buscando desde: {since_imap}")

    # Firmas (fecha, monto) de transacciones ya presentes, para evitar duplicados
    existing = set()
    for row in re.findall(r"\['[^']*','(\d{4}-\d{2}-\d{2})','[\d:]{5}',[^\]]*?,(\d+\.\d{2}),'[^']*'\]", content):
        existing.add((row[0], row[1]))

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
            if (tx['date'], f"{tx['amt']:.2f}") in existing:
                processed.add(e['msg_id']); continue
            tid = tx_id(tx['acct'], tx['date'], tx['time'], tx['amt'])
            line = make_line(tid, tx)
            new_produ.append(line)
            existing.add((tx['date'], f"{tx['amt']:.2f}"))
            processed.add(e['msg_id'])
            print(f"  + Produbanco: {tx['date']} {tx['est']} ${tx['amt']:.2f}")

    # --- Pacífico TC ---
    for e in fetch_emails(imap, since_imap, 'notificaciones@infopacificard.com.ec'):
        if e['msg_id'] in processed:
            continue
        tx = parse_bp_tc(e['html_body'], e['email_date'])
        if tx and tx['date'].startswith(month_prefix):
            if (tx['date'], f"{tx['amt']:.2f}") in existing:
                processed.add(e['msg_id']); continue
            tid = tx_id(tx['acct'], tx['date'], tx['time'], tx['amt'])
            line = make_line(tid, tx)
            new_bptc.append(line)
            existing.add((tx['date'], f"{tx['amt']:.2f}"))
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
            if (tx['date'], f"{tx['amt']:.2f}") in existing:
                processed.add(e['msg_id']); continue
            tid = tx_id(tx['acct'], tx['date'], tx['time'], tx['amt'])
            line = make_line(tid, tx)
            new_bpdeb.append(line)
            existing.add((tx['date'], f"{tx['amt']:.2f}"))
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
