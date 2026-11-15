import time
import pandas as pd
from playwright.sync_api import sync_playwright

URL = "https://www.cardgamecorner.com/it/buylist-2026?game=pokemon"

def scrape_buylist():
    cards = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        print(f"Apertura {URL}...")
        page.goto(URL, wait_until="networkidle", timeout=60000)
        time.sleep(3)

        # Chiude eventuali banner cookie/popup
        for selector in ["button:has-text('Accetta')", "button:has-text('Chiudi')", ".close"]:
            try:
                if page.locator(selector).first.is_visible():
                    page.locator(selector).first.click()
                    time.sleep(1)
            except Exception:
                pass

        # Disattiva eventuali filtri "Solo Hot Cards" per mostrare tutto il catalogo
        try:
            tutto_btn = page.locator("text=Tutto").first
            if tutto_btn.is_visible():
                tutto_btn.click()
                time.sleep(3)
        except Exception:
            pass

        # Scorre la pagina fino in fondo per caricare tutte le carte (infinite scroll / paginazione)
        last_height = page.evaluate("document.body.scrollHeight")
        scroll_attempts = 0
        while scroll_attempts < 100:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)

            # Se c'è un pulsante "Carica altro" o "Successiva", lo clicca
            next_btn = page.locator("button:has-text('Carica'), a:has-text('Successiva'), .pagination .next").first
            if next_btn.is_visible():
                try:
                    next_btn.click()
                    time.sleep(2.5)
                except Exception:
                    pass

            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == last_height and not next_btn.is_visible():
                break
            last_height = new_height
            scroll_attempts += 1
            print(f"Scorrimento {scroll_attempts} - Altezza pagina: {new_height}")

        # Estrazione di tutte le card renderizzate nel DOM
        items = page.evaluate("""() => {
            const results = [];
            // Seleziona tutti i blocchi che contengono un prezzo in Euro
            const allElements = document.querySelectorAll('div, li, tr');
            allElements.forEach(el => {
                const text = el.innerText || '';
                if (text.includes('€') && text.length < 350 && el.children.length >= 2) {
                    const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
                    const priceLine = lines.find(l => l.includes('€'));
                    if (priceLine && lines.length >= 2) {
                        results.push({
                            raw: lines.join(' | '),
                            price: priceLine
                        });
                    }
                }
            });
            return results;
        }""")

        for item in items:
            parts = [x.strip() for x in item["raw"].split("|")]
            # Filtra voci di menu o duplicati annidati
            if any(x in item["raw"] for x in ["Vedi carrello", "Login", "Filtri"]):
                continue
            key = item["raw"]
            if key not in seen:
                seen.add(key)
                cards.append({
                    "Dettagli Estratti": item["raw"],
                    "Carta / Info": parts[0] if len(parts) > 0 else "",
                    "Espansione / Extra": parts[1] if len(parts) > 1 else "",
                    "Prezzo Buylist": item["price"]
                })

        browser.close()

    df = pd.DataFrame(cards)
    output_file = "CardGameCorner_Buylist_Completa.xlsx"
    df.to_excel(output_file, index=False)
    print(f"Fatto! Estratte {len(df)} righe salvate in {output_file}")

if __name__ == "__main__":
    scrape_buylist()
