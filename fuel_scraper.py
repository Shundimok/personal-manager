"""
fuel_scraper.py
================
Haalt de actuele brandstofprijzen van het Q8-station in Antwerpen
(Schijnpoortweg 18-20, 2060 Antwerpen) van carbu.com en slaat ze op in
`fuel_prices.json`, in dezelfde map als dit script.

Brandstoffen die worden bijgehouden:
    - Super 95 (E10)
    - Super 98 (E5)
    - Diesel (B7)

WERKWIJZE (bewust markup-onafhankelijk)
----------------------------------------
Op de carbu.com-pagina staat elke brandstofsoort als tekstblok in deze
volgorde: naam -> prijs (bv. "1,811 €/L") -> datum (bv. "01/07/26"), of
naam -> "-" -> "Prijs niet beschikbaar" als er geen prijs gekend is.

BELANGRIJK: bovenaan de pagina staat ook een brandstofkeuzelijst (voor de
vergelijkingstool) die dezelfde namen ("Super 95 (E10)", "Diesel (B7)", ...)
al een eerste keer vermeldt, VOOR het eigenlijke prijzenblok verderop op de
pagina. Als je zomaar het eerste voorkomen van een naam neemt, beland je in
die keuzelijst en vind je geen prijs. Dit script lost dat op door, als het
eerste voorkomen geen prijs oplevert, automatisch naar het volgende
voorkomen van diezelfde naam te zoeken, tot het wél een prijs (of expliciet
"niet beschikbaar") vindt.

VEREISTEN
---------
    pip install requests beautifulsoup4

GEBRUIK
-------
    python fuel_scraper.py

Om de prijzen automatisch actueel te houden voor het dashboard, plan dit
script best elke X minuten in:
    - Windows: Taakplanner ("Create Basic Task" -> trigger elke 5-15 min)
    - macOS/Linux: cron (bv. "*/10 * * * * python3 /pad/naar/fuel_scraper.py")
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://carbu.com/belgie/index.php/station/q8/antwerpen/2060/1591"
STATION_NAAM = "Q8 Antwerpen — Schijnpoortweg 18-20, 2060 Antwerpen"
OUTPUT_FILE = Path(__file__).resolve().parent / "fuel_prices.json"

# Exacte labels zoals ze vandaag op de carbu.com-pagina staan.
# Als carbu.com deze tekst ooit wijzigt, pas ze hier aan.
FUEL_LABELS = {
    "super95": "Super 95 (E10)",
    "super98": "Super 98 (E5)",
    "diesel": "Diesel (B7)",
}

PRICE_PATTERN = re.compile(r"(\d+,\d{2,3})\s*€\s*/?\s*L", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\b(\d{2}/\d{2}/\d{2})\b")

# Hoeveel tekstnodes we maximaal vooruitkijken vanaf een label-voorkomen.
# Klein gehouden zodat we de keuzelijst bovenaan (waar geen prijs op volgt)
# snel afwijzen en naar het volgende voorkomen van het label springen.
MAX_LOOKAHEAD = 8

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.text


def try_extract_from_node(start_node, other_labels):
    """
    Probeert vanaf één specifiek label-voorkomen een prijs + datum te lezen,
    binnen een klein aantal stappen (MAX_LOOKAHEAD). Geeft None terug als dit
    voorkomen geen bruikbare prijs oplevert (bv. omdat het de keuzelijst is).
    """
    node = start_node
    price_value = None
    date_value = None
    unavailable = False
    steps = 0

    while steps < MAX_LOOKAHEAD:
        node = node.find_next(string=True)
        steps += 1
        if node is None:
            break
        text = node.strip()
        if not text:
            continue

        # Een ander brandstoflabel vóór we iets bruikbaars vonden -> dit was
        # niet de juiste plek (waarschijnlijk de keuzelijst bovenaan).
        if text in other_labels:
            return None

        if price_value is None and not unavailable:
            match = PRICE_PATTERN.search(text)
            if match:
                price_value = float(match.group(1).replace(",", "."))
                continue
            if text == "-" or "niet beschikbaar" in text.lower():
                unavailable = True
                continue

        if date_value is None:
            match = DATE_PATTERN.search(text)
            if match:
                date_value = match.group(1)

        if (price_value is not None or unavailable) and (date_value is not None or unavailable):
            break

    if price_value is None and not unavailable:
        return None  # dit voorkomen leverde niets op -> waarschijnlijk de keuzelijst

    if unavailable:
        return {"price": None, "page_date": date_value, "error": "prijs niet beschikbaar op carbu.com"}
    return {"price": price_value, "page_date": date_value, "error": None}


def find_price_for_label(soup: BeautifulSoup, label: str, other_labels):
    """
    Zoekt ALLE voorkomens van `label` op de pagina (er is er minstens één in
    de keuzelijst bovenaan, en één in het echte prijzenblok verderop), en
    probeert voorkomen per voorkomen tot er een bruikbare prijs gevonden
    wordt.
    """
    occurrences = soup.find_all(string=lambda t: t and t.strip() == label)
    if not occurrences:
        return {"price": None, "page_date": None, "error": "label niet gevonden op pagina"}

    for occurrence in occurrences:
        result = try_extract_from_node(occurrence, other_labels)
        if result is not None:
            return result

    return {"price": None, "page_date": None, "error": "geen enkel voorkomen van dit label leverde een prijs op"}


def scrape_fuel_prices() -> dict:
    html = fetch_html(URL)
    soup = BeautifulSoup(html, "html.parser")

    result = {
        "station": STATION_NAAM,
        "source_url": URL,
        "last_update": datetime.now().isoformat(timespec="seconds"),
        "fuels": {},
    }

    all_labels = list(FUEL_LABELS.values())
    for key, label in FUEL_LABELS.items():
        other_labels = [l for l in all_labels if l != label]
        info = find_price_for_label(soup, label, other_labels)
        result["fuels"][key] = {
            "label": label,
            "unit": "€/L",
            **info,
        }

    return result


def main():
    try:
        data = scrape_fuel_prices()
    except requests.RequestException as exc:
        # Ook bij een netwerkfout schrijven we een geldig JSON-bestand, zodat
        # het dashboard een duidelijke melding kan tonen in plaats van vast
        # te lopen op een ontbrekend of kapot bestand.
        data = {
            "station": STATION_NAAM,
            "source_url": URL,
            "last_update": datetime.now().isoformat(timespec="seconds"),
            "fuels": {},
            "error": f"Kon carbu.com niet bereiken: {exc}",
        }
        OUTPUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"FOUT: {exc}", file=sys.stderr)
        sys.exit(1)

    OUTPUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Opgeslagen in: {OUTPUT_FILE}")
    for info in data["fuels"].values():
        if info.get("price") is not None:
            print(f"  {info['label']:<16} {info['price']:.3f} €/L  (pagina-datum: {info.get('page_date')})")
        else:
            print(f"  {info['label']:<16} niet beschikbaar  ({info.get('error')})")


if __name__ == "__main__":
    main()
