#!/usr/bin/env python3
"""Forskningsbevakning: astma, allergi, KOL m.m.

Läser sökningar från searches.txt, frågar PubMed och Europe PMC (preprints),
sparar nya träffar och skriver:
  docs/index.html  (sida med filter)
  docs/feed.xml    (RSS)
samt mejlar nya träffar om SMTP_* och NOTIFY_EMAIL är satta.
"""
import html, json, os, smtplib, ssl, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

LOOKBACK_DAYS = int(os.getenv('LOOKBACK_DAYS') or 14)
MAX_RESULTS = 100
SITE_URL = os.getenv('SITE_URL', '')          # t.ex. https://namn.github.io/repo/
NOTIFY_EMAIL = os.getenv('NOTIFY_EMAIL')      # mottagare
NEW_BADGE_DAYS = 7

OUT = Path('docs'); OUT.mkdir(exist_ok=True)
SEEN, ITEMS, SEARCHES = Path('seen.json'), Path('items.json'), Path('searches.txt')


# ---------- Sökningar ----------
def load_searches():
    out = []
    for line in SEARCHES.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        label, sep, q = line.partition('::')
        if not sep:
            label, q = line, line
        groups = [[t.strip().strip('"') for t in g.split('|') if t.strip()] for g in q.split('+')]
        groups = [g for g in groups if g]
        if groups:
            out.append({'label': label.strip(), 'groups': groups})
    return out


def pubmed_query(groups):
    def t(x): return f'{x}[Title/Abstract]' if '*' in x else f'"{x}"[Title/Abstract]'
    return ' AND '.join('(' + ' OR '.join(t(x) for x in g) + ')' for g in groups)


def epmc_query(groups):
    def t(x):
        v = x if '*' in x and ' ' not in x else f'"{x}"'
        return f'(TITLE:{v} OR ABSTRACT:{v})'
    return ' AND '.join('(' + ' OR '.join(t(x) for x in g) + ')' for g in groups) + ' AND SRC:PPR'


def get(url, tries=3):
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'research-watch/2.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if n == tries - 1:
                raise
            time.sleep(2 * (n + 1))


def norm_date(s):
    return (s or '')[:10].replace('/', '-')


def pubmed(groups):
    base = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
    p = urllib.parse.urlencode({'db': 'pubmed', 'term': pubmed_query(groups), 'retmode': 'json',
                                'retmax': MAX_RESULTS, 'datetype': 'edat',
                                'reldate': LOOKBACK_DAYS, 'sort': 'date'})
    ids = get(base + 'esearch.fcgi?' + p)['esearchresult']['idlist']
    if not ids:
        return []
    time.sleep(0.4)
    s = get(base + 'esummary.fcgi?' + urllib.parse.urlencode(
        {'db': 'pubmed', 'id': ','.join(ids), 'retmode': 'json'}))['result']
    out = []
    for i in ids:
        d = s.get(i)
        if not d:
            continue
        au = d.get('authors', [])
        out.append({'id': f'pmid:{i}', 'title': d.get('title', '').rstrip('.'),
                    'authors': ', '.join(a['name'] for a in au[:4]) + (' et al.' if len(au) > 4 else ''),
                    'journal': d.get('fulljournalname', ''), 'date': norm_date(d.get('sortpubdate')),
                    'url': f'https://pubmed.ncbi.nlm.nih.gov/{i}/', 'source': 'PubMed'})
    time.sleep(0.4)
    return out


def preprints(groups):
    p = urllib.parse.urlencode({'query': epmc_query(groups), 'format': 'json', 'pageSize': 50,
                                'sort': 'P_PDATE_D desc'})
    res = get('https://www.ebi.ac.uk/europepmc/webservices/rest/search?' + p)['resultList']['result']
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    out = []
    for d in res:
        date = norm_date(d.get('firstPublicationDate'))
        if not date or date < cutoff:
            continue
        out.append({'id': f"{d['source']}:{d['id']}", 'title': d.get('title', '').rstrip('.'),
                    'authors': d.get('authorString', ''),
                    'journal': d.get('bookOrReportDetails', {}).get('publisher') or 'Preprint',
                    'date': date, 'url': f"https://europepmc.org/article/{d['source']}/{d['id']}",
                    'source': 'Preprint'})
    return out


# ---------- Utdata ----------
def esc(s): return html.escape(s or '')


PAGE = '''<!doctype html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Forskningsbevakning</title>
<link rel="alternate" type="application/rss+xml" title="Forskningsbevakning" href="feed.xml">
<style>
:root{--bg:#f6f7f5;--card:#fff;--ink:#16211f;--mute:#5d6b68;--line:#e2e6e3;--accent:#0f766e;--accent-soft:#d9efec;--new:#c2410c;--new-soft:#ffedd5}
@media (prefers-color-scheme:dark){:root{--bg:#0f1514;--card:#172120;--ink:#e6efed;--mute:#9bb0ac;--line:#263230;--accent:#4fd1c5;--accent-soft:#17403c;--new:#fdba74;--new-soft:#4a2a12}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:0 16px 64px}
header{padding:40px 0 20px}
h1{margin:0 0 6px;font-size:clamp(1.6rem,4vw,2.2rem);letter-spacing:-.02em;font-weight:700}
.sub{color:var(--mute);margin:0}
.sub a{color:var(--accent);text-decoration:none;font-weight:600}.sub a:hover{text-decoration:underline}
.tools{background:var(--bg);padding:12px 0;border-bottom:1px solid var(--line)}
@media (min-width:700px){.tools{position:sticky;top:0;z-index:2}}
input[type=search]{width:100%;padding:11px 14px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);font:inherit}
input[type=search]:focus{outline:2px solid var(--accent);outline-offset:1px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.chip{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 12px;border-radius:999px;font:inherit;font-size:.86rem;cursor:pointer}
.chip b{color:var(--mute);font-weight:600;margin-left:4px}
.chip[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}
.chip[aria-pressed=true] b{color:#ffffffcc}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-top:14px}
.card h2{margin:0 0 6px;font-size:1.06rem;line-height:1.35;font-weight:650}
.card h2 a{color:var(--ink);text-decoration:none}.card h2 a:hover{color:var(--accent);text-decoration:underline}
.meta{color:var(--mute);font-size:.88rem;margin:0}
.meta em{font-style:italic}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.tag{background:var(--accent-soft);color:var(--accent);border-radius:6px;padding:2px 8px;font-size:.76rem;font-weight:600}
.badge{display:inline-block;background:var(--new-soft);color:var(--new);border-radius:6px;padding:1px 7px;font-size:.72rem;font-weight:700;margin-right:8px;vertical-align:2px;letter-spacing:.03em}
.empty{color:var(--mute);text-align:center;padding:48px 0}
.count{color:var(--mute);font-size:.85rem;margin:14px 0 0}
</style></head><body><div class="wrap">
<header><h1>Ny forskning</h1>
<p class="sub">Astma, allergi, KOL och närliggande områden · uppdaterad __UPDATED__ · <a href="feed.xml">RSS</a></p></header>
<div class="tools"><input id="q" type="search" placeholder="Sök i titlar, författare och tidskrifter…" aria-label="Sök">
<div class="chips" id="chips"></div></div>
<p class="count" id="count"></p><main id="list"></main>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const today=new Date(D.updatedISO);let tag=null,q='';
const $=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e};
function isNew(i){return i.added&&(today-new Date(i.added))/864e5<=D.newDays}
function chips(){const w=document.getElementById('chips');w.replaceChildren();
 const mk=(label,n,val)=>{const b=$('button','chip',label);b.appendChild($('b',null,n));b.setAttribute('aria-pressed',tag===val);
  b.onclick=()=>{tag=val;chips();render()};w.appendChild(b)};
 mk('Alla',D.items.length,null);
 D.searches.forEach(s=>mk(s,D.items.filter(i=>i.tags.includes(s)).length,s))}
function render(){const l=document.getElementById('list');l.replaceChildren();
 const t=q.toLowerCase();
 const rows=D.items.filter(i=>(!tag||i.tags.includes(tag))&&(!t||(i.title+' '+i.authors+' '+i.journal).toLowerCase().includes(t)));
 document.getElementById('count').textContent=rows.length+' artiklar';
 if(!rows.length){l.appendChild($('p','empty','Inga träffar än.'));return}
 rows.forEach(i=>{const c=$('article','card');const h=$('h2');
  if(isNew(i))h.appendChild($('span','badge','NY'));
  const a=$('a',null,i.title);a.href=i.url;a.target='_blank';a.rel='noopener';h.appendChild(a);c.appendChild(h);
  const m=$('p','meta');m.append(i.authors||'');if(i.authors)m.appendChild($('br'));
  m.appendChild($('em',null,i.journal));m.append(' · '+i.date+' · '+i.source);c.appendChild(m);
  const tg=$('div','tags');i.tags.forEach(x=>tg.appendChild($('span','tag',x)));c.appendChild(tg);l.appendChild(c)})}
document.getElementById('q').oninput=e=>{q=e.target.value;render()};
chips();render();
</script></body></html>'''


def write_html(items, labels):
    now = datetime.now(timezone.utc)
    data = json.dumps({'items': items, 'searches': labels, 'newDays': NEW_BADGE_DAYS,
                       'updatedISO': now.isoformat()}, ensure_ascii=False).replace('<', '\\u003c')
    (OUT / 'index.html').write_text(
        PAGE.replace('__UPDATED__', now.strftime('%Y-%m-%d')).replace('__DATA__', data), encoding='utf-8')


def write_rss(items):
    def pub(i):
        try:
            return f'<pubDate>{format_datetime(datetime.fromisoformat(i["date"]).replace(tzinfo=timezone.utc))}</pubDate>'
        except ValueError:
            return ''
    entries = ''.join(
        f'<item><title>{esc(i["title"])}</title><link>{esc(i["url"])}</link>'
        f'<guid isPermaLink="false">{esc(i["id"])}</guid>{pub(i)}'
        + ''.join(f'<category>{esc(t)}</category>' for t in i['tags'])
        + f'<description>{esc(i["authors"])} – {esc(i["journal"])} ({esc(i["date"])})</description></item>'
        for i in items[:100])
    (OUT / 'feed.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>'
        f'<title>Forskningsbevakning</title><link>{esc(SITE_URL) or "."}</link>'
        f'<description>Astma, allergi, KOL</description>{entries}</channel></rss>', encoding='utf-8')


def send_mail(new):
    host, user, pw = os.getenv('SMTP_HOST'), os.getenv('SMTP_USER'), os.getenv('SMTP_PASS')
    if not (NOTIFY_EMAIL and host and user and pw):
        return
    by = {}
    for i in new:
        for t in i['tags']:
            by.setdefault(t, []).append(i)
    text, body = [], []
    for t, lst in by.items():
        text.append(f'== {t} ==')
        body.append(f'<h3 style="margin:22px 0 6px;color:#0f766e">{esc(t)}</h3>')
        for i in lst:
            text.append(f'{i["title"]}\n{i["authors"]} – {i["journal"]} ({i["date"]})\n{i["url"]}\n')
            body.append(f'<p style="margin:0 0 12px"><a href="{esc(i["url"])}" style="color:#16211f;font-weight:600;text-decoration:none">{esc(i["title"])}</a><br>'
                        f'<span style="color:#5d6b68;font-size:13px">{esc(i["authors"])} · <i>{esc(i["journal"])}</i> · {esc(i["date"])}</span></p>')
    link = f'<p><a href="{esc(SITE_URL)}">Öppna sidan</a></p>' if SITE_URL else ''
    m = EmailMessage()
    m['Subject'] = f'{len(new)} nya artiklar i bevakningen'
    m['From'], m['To'] = user, NOTIFY_EMAIL
    m.set_content('\n'.join(text))
    m.add_alternative('<div style="font:15px/1.5 system-ui,sans-serif;max-width:640px">'
                      + ''.join(body) + link + '</div>', subtype='html')
    port = int(os.getenv('SMTP_PORT') or 465)
    ctx = ssl.create_default_context()
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, context=ctx)
    else:
        s = smtplib.SMTP(host, port); s.starttls(context=ctx)
    with s:
        s.login(user, pw); s.send_message(m)


def main():
    searches = load_searches()
    if not searches:
        sys.exit('Inga sökningar i searches.txt')
    found, errors, ok = {}, 0, 0
    for s in searches:
        for fn in (pubmed, preprints):
            try:
                for i in fn(s['groups']):
                    found.setdefault(i['id'], {**i, 'tags': []})
                    if s['label'] not in found[i['id']]['tags']:
                        found[i['id']]['tags'].append(s['label'])
                ok += 1
            except Exception as e:
                errors += 1
                print(f'Varning: "{s["label"]}" ({fn.__name__}) misslyckades: {e}', file=sys.stderr)
    if ok == 0:
        sys.exit('Alla sökningar misslyckades, inget uppdaterades.')

    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    stored = json.loads(ITEMS.read_text()) if ITEMS.exists() else []
    by_id = {i['id']: i for i in stored}
    for i in by_id.values():                       # äldre format utan taggar
        i.setdefault('tags', []); i.setdefault('added', '')
    today, new = datetime.now(timezone.utc).strftime('%Y-%m-%d'), []
    for id_, i in found.items():
        if id_ in by_id:
            by_id[id_]['tags'] = sorted(set(by_id[id_]['tags']) | set(i['tags']))
        elif id_ not in seen:
            i['added'] = today
            by_id[id_] = i; new.append(i)
    items = sorted(by_id.values(), key=lambda i: (i['date'], i['id']), reverse=True)[:1000]
    labels = [s['label'] for s in searches]

    SEEN.write_text(json.dumps(sorted(seen | set(found))))
    ITEMS.write_text(json.dumps(items, ensure_ascii=False, indent=1))
    write_html(items, labels); write_rss(items)
    if new:
        send_mail(new)
    print(f'{len(new)} nya, {len(items)} totalt, {errors} fel')


if __name__ == '__main__':
    main()
