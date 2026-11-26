#!/usr/bin/env python3
"""Prova di lettura di alcune espansioni, SENZA scrivere nulla su Supabase.

Apre il sito come fa lo scanner continuo, legge le espansioni indicate e salva, per ognuna,
uno screenshot, il testo della pagina e lo stato letto dal sito (contatori, filtri, errori di rete).
Serve a capire perche lo scanner ha trovato "nessun risultato" per molte espansioni.
Lo lancia a mano il workflow "Prova lettura espansione" (.github/workflows/prova_lettura.yml).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

DEFAULT = 'Dragons Exalted|151|Evolving Skies'


def find_edition(editions, name):
    wanted = str(name).strip().casefold()
    for edition in editions:
        if str(edition['name']).strip().casefold() == wanted:
            return edition
    return None


def safe_name(label):
    return ''.join(c if c.isalnum() else '_' for c in str(label))[:40] or 'x'


def snapshot(page, out, label, read_state):
    """Salva screenshot, testo e stato della pagina; restituisce le informazioni utili."""
    name = safe_name(label)
    info = {}
    try:
        state = page.evaluate(read_state, {'probe': True})
        for key in ('page', 'pages', 'pending', 'no_results', 'data_empty', 'only_hot', 'filters', 'counters'):
            info[key] = state.get(key)
        info['righe_visibili'] = len(state.get('rows') or [])
    except Exception as error:
        info['errore_lettura_stato'] = f'{type(error).__name__}: {error}'
    monitor = getattr(page, '_buylist_monitor', None)
    if monitor is not None:
        info['risposte_riuscite'] = getattr(monitor, 'successful_responses', None)
        info['errori_di_rete'] = list(getattr(monitor, 'errors', []) or [])
    try:
        page.screenshot(path=str(Path(out, name + '.png')))
    except Exception as error:
        info['errore_screenshot'] = f'{type(error).__name__}: {error}'
    try:
        text = page.evaluate('() => document.body.innerText') or ''
        Path(out, name + '.txt').write_text(text[:5000], encoding='utf-8')
    except Exception as error:
        info['errore_testo'] = f'{type(error).__name__}: {error}'
    return info


def run(site, names, timeout, out, scan, read_state):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    editions = site.open()
    print(f'Espansioni elencate dal sito: {len(editions)}', flush=True)
    results = []
    for name in names:
        edition = find_edition(editions, name)
        if edition is None:
            similar = [e['name'] for e in editions if str(name).strip().casefold() in str(e['name']).casefold()]
            print(f'  {name}: NON TROVATA tra le espansioni del sito. Simili: {similar[:8]}', flush=True)
            results.append({'espansione': name, 'esito': 'NON TROVATA', 'simili': similar[:8]})
            continue
        pages, started, error = [], time.monotonic(), None
        try:
            cards, page_count = scan(site, {'id': edition['id'], 'name': edition['name']},
                                     lambda n, c: pages.append(len(c)), lambda: None, timeout)
            outcome = 'CARTE LETTE' if cards else 'VUOTA'
        except Exception as exc:
            cards, page_count, outcome = 0, 0, 'ERRORE'
            error = f'{type(exc).__name__}: {exc}'
        info = snapshot(site.page, out, name, read_state)
        row = {'espansione': edition['name'], 'esito': outcome, 'carte': cards, 'pagine': page_count,
               'secondi': round(time.monotonic() - started, 1), 'errore': error, 'sito': info}
        results.append(row)
        print(f"  {edition['name']}: {outcome} - {cards} carte in {page_count} pagine, {row['secondi']} s"
              f" | contatori sito: {info.get('counters')} | risposte riuscite: {info.get('risposte_riuscite')}"
              + (f' | {error}' if error else ''), flush=True)
        if outcome == 'ERRORE':
            editions = site.open()
    Path(out, 'riepilogo.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--espansioni', default=os.environ.get('ESPANSIONI') or DEFAULT,
                        help='nomi separati da | (default: %s)' % DEFAULT)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--out', default='prova')
    args = parser.parse_args()
    names = [n.strip() for n in args.espansioni.split('|') if n.strip()]
    if not names:
        sys.exit('Indica almeno un nome di espansione.')
    from playwright.sync_api import sync_playwright
    from continuo import Site, scan_query
    from scraper import READ_STATE
    with sync_playwright() as playwright:
        site = Site(playwright, args.timeout)
        try:
            run(site, names, args.timeout, args.out, scan_query, READ_STATE)
        finally:
            site.close()


if __name__ == '__main__':
    main()
