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
        time.sleep(4)

        # Chiude eventuali banner cookie
        for selector in ["button:has-text('Accetta')", "button:has-text('Chiudi')", ".close"]:
            try:
                if page.locator(selector).first.is_visible():
                    page.locator(selector).first.click()
                    time.sleep(1)
            except Exception:
                pass

        # Clicca sul filtro "Tutto" se presente per sbloccare l'intero catalogo
        try:
            tutto_btn = page.locator("text=Tutto").first
            if tutto_btn.is_visible():
                tutto_btn.click()
                time.sleep(3)
        except Exception:
            pass

        # Scorre la pagina ripetutamente per caricare tutto il catalogo dinamico
        last_height = page.evaluate("document.body.scrollHeight")
        scroll_attempts = 0
        while scroll_attempts < 150:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2.5)

            # Clicca su eventuali pulsanti "Carica altro"
            for btn_selector in ["button:has-text('Carica')", "a:has-text('Successiva')", ".load-more", ".pagination .next"]:
                try:
                    btn = page.locator(btn_selector).first
                    if btn.is_visible():
                        btn.click()
                        time.sleep(2)
                except Exception:
                    pass

            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                # Prova un secondo scroll di sicurezza
                page.evaluate("window.scrollTo(0, document.body.scrollHeight - 500)")
                time.sleep(1.5)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)
                new_height_check = page.evaluate("document.body.scrollHeight")
                if new_height_check == last_height:
                    break
            last_height = new_height_check if 'new_height_check' in locals() else new_height
            scroll_attempts += 1
            print(f"Scorrimento n. {scroll_attempts} completato...")

        # Estrazione mirata dei box prodotto
        extracted_items = page.evaluate("""() => {
            const results = [];
            // Cerca tutti i contenitori tipici di card/prodotti nel sito
            const items = document.querySelectorAll('.product-item, .card-item, div[class*="card"], div[class*="product"], li[class*="item"]');
            
            items.forEach(el => {
                const text = el.innerText || '';
                if (text.includes('€')) {
                    results.push(text);
                }
            });
            
            // Fallback se i selettori specifici non bastano: analizza tutti i blocchi con prezzo
            if (results.length < 10) {
                const allDivs = document.querySelectorAll('div');
                allDivs.forEach(el => {
                    const t = el.innerText || '';
                    if (t.includes('€') && t.length < 400 && el.children.length >= 2) {
                        results.push(t);
                    }
                });
            }
            return results;
        }""")

        print(f"Trovati {len(extracted_items)} elementi grezzi. Elaborazione in corso...")

        for item in extracted_items:
            # Pulisce e formatta le righe
            lines = [l.strip() for l in item.split('\n') if l.strip()]
            price_line = next((l for l in lines if '€' in l), None)
            
            if price_line and len(lines) >= 2:
                # Esclude menu e voci di navigazione
                if any(kw in item for kw in ["Vedi carrello", "Login", "Filtri", "Privacy", "Cookie"]):
                    continue
                
                # Identifica il nome della carta (di solito penultima o terzultima riga prima del prezzo)
                clean_lines = [l for l in lines if l not in ['Italiano', 'English', 'Spanish', 'Near Mint', 'Excellent', 'Heavily Played', 'Poor/Damaged', 'Reverse', 'First Edition', 'IT', 'EN', 'ES', 'NM/M', 'SP', 'HP', 'D'] and '€' not in l]
                
                if len(clean_lines) >= 2:
                    carta = clean_lines[-1]
                    espansione = clean_lines[-2]
                elif len(clean_lines) == 1:
                    carta = clean_lines[0]
                    espansione = "Catalogo Generale"
                else:
                    continue

                # Pulisce il prezzo lasciando solo il numero/valuta
                row_key = f"{carta}|{espansione}|{price_line}"
                if row_key not in seen:
                    seen.add(row_key)
                    cards.append({
                        "Carta": carta,
                        "Espansione": espansione,
                        "Prezzo Buylist": price_line
                    })

        browser.close()

    df = pd.DataFrame(cards)
    output_file = "CardGameCorner_Buylist_Completa.xlsx"
    df.to_excel(output_file, index=False)
    print(f"Fatto! Estratte con successo {len(df)} carte uniche salvate in {output_file}")

if __name__ == "__main__":
    scrape_buylist()
