import time
import pandas as pd
from playwright.sync_api import sync_playwright

URL = "https://www.cardgamecorner.com/it/buylist-2026?game=pokemon"

def scrape_all_pokemon_buylist():
    all_cards = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        print(f"Apertura {URL}...")
        page.goto(URL, wait_until="networkidle", timeout=60000)
        time.sleep(4)

        # Chiude eventuali banner cookie
        for selector in ["button:has-text('Accetta')", "button:has-text('Chiudi')", ".close"]:
            try:
                if page.locator(selector).first.is_visible():
                    page.locator(selector).first.click()
                    time.sleep(1)
            except Exception:
                pass

        # Scorre la pagina ripetutamente per caricare tutto il catalogo dinamico (infinite scroll)
        last_height = page.evaluate("document.body.scrollHeight")
        for i in range(250):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.5)
            
            # Clicca eventuali pulsanti "Carica altro" o "Successiva"
            for btn_selector in ["button:has-text('Carica')", "a:has-text('Successiva')", ".load-more", ".pagination .next"]:
                try:
                    btn = page.locator(btn_selector).first
                    if btn.is_visible():
                        btn.click()
                        time.sleep(1.5)
                except Exception:
                    pass

            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == last_height and i > 15:
                break
            last_height = new_height

        # Estrazione di tutti i blocchi carta dal DOM
        cards_raw = page.evaluate("""() => {
            const results = [];
            const elements = document.querySelectorAll('div, li, tr');
            elements.forEach(el => {
                const text = el.innerText || '';
                if (text.includes('€') && text.length < 350 && el.children.length >= 1) {
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

        print(f"Estratti {len(cards_raw)} elementi grezzi. Pulizia dati in corso...")

        for item in cards_raw:
            full = item["fullText"]
            price = item["price"]
            
            # Esclude menu e voci di navigazione
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
    print(f"Fatto! Salvate {len(df)} carte nel file Excel.")

if __name__ == "__main__":
    scrape_all_pokemon_buylist()
