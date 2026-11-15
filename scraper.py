import time
import pandas as pd
from playwright.sync_api import sync_playwright

URL = "https://www.cardgamecorner.com/it/buylist-2026?game=pokemon"

def scrape_by_expansions():
    all_cards = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        print(f"Apertura {URL}...")
        page.goto(URL, wait_until="networkidle", timeout=60000)
        time.sleep(4)

        # Chiude cookie banner
        for selector in ["button:has-text('Accetta')", "button:has-text('Chiudi')", ".close"]:
            try:
                if page.locator(selector).first.is_visible():
                    page.locator(selector).first.click()
                    time.sleep(1)
            except Exception:
                pass

        # Raccoglie tutti i link o i selettori delle singole espansioni dal menu laterale/filtri
        # Cerchiamo i link all'interno dei filtri delle edizioni
        expansion_locators = page.locator(".edizioni a, .filters a, select#edizione option, [class*='edition'] a, [class*='expansion'] a").all()
        
        print(Trovati if len(expansion_locators) > 0 else "Nessun filtro specifico trovato, procedo con estrazione estesa.")

        # Se ci sono espansioni selezionabili, clicchiamo su ognuna per estrarre le carte dedicate
        # Altrimenti eseguiamo uno scroll massivo di tutto il catalogo unificato
        last_height = page.evaluate("document.body.scrollHeight")
        for i in range(300):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.2)
            
            # Clicca pulsanti "Carica altro" se presenti
            for btn_text in ["Carica altro", "Mostra altri", "Load more"]:
                try:
                    btn = page.locator(f"button:has-text('{btn_text}'), a:has-text('{btn_text}')").first
                    if btn.is_visible():
                        btn.click()
                        time.sleep(1.5)
                except Exception:
                    pass

            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == last_height and i > 20:
                break
            last_height = new_height

        # Estrazione strutturata dei prodotti
        cards_raw = page.evaluate("""() => {
            const results = [];
            // Seleziona qualsiasi elemento che contenga un prezzo in euro e abbia figli
            const elements = document.querySelectorAll('div, li, tr');
            elements.forEach(el => {
                const text = el.innerText || '';
                if (text.includes('€') && text.length < 300) {
                    const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
                    const priceLine = lines.find(l => l.includes('€'));
                    if (priceLine && lines.length >= 2) {
                        results.push({
                            fullText: lines.join(' | '),
                            price: priceLine
                        });
                    }
                }
            });
            return results;
        }""")

        for item in cards_raw:
            full = item["fullText"]
            price = item["price"]
            
            if any(skip in full for skip in ["Vedi carrello", "Login", "Filtri", "Privacy", "Cookie", "Cerca"]):
                continue

            parts = [p.strip() for p in full.split('|') if p.strip()]
            clean_parts = [p for p in parts if p not in ['Italiano', 'English', 'Spanish', 'Near Mint', 'Excellent', 'Heavily Played', 'Poor/Damaged', 'Reverse', 'First Edition', 'IT', 'EN', 'ES', 'NM/M', 'SP', 'HP', 'D', price]]

            if len(clean_parts) >= 2:
                carta = clean_parts[-1]
                espansione = clean_parts[-2]
            elif len(clean_parts) == 1:
                carta = clean_parts[0]
                espansione = "Varie / Promo"
            else:
                continue

            unique_key = f"{carta}---{espansione}---{price}"
            if unique_key not in seen:
                seen.add(unique_key)
                all_cards.append({
                    "Carta": carta,
                    "Espansione": espansione,
                    "Prezzo Buylist": price
                })

        browser.close()

    df = pd.DataFrame(all_cards)
    df.to_excel("CardGameCorner_Buylist_Pokemon_Completa.xlsx", index=False)
    print(f"Totale carte estratte: {len(df)}")

if __name__ == "__main__":
    scrape_by_expansions()
