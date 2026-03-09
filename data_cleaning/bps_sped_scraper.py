"""
BPS Special Education Programs Scraper
=======================================
Scrapes the 10 special needs program tables from:
  https://www.bostonpublicschools.org/academics/specialized-services/programs-services

Requirements:
    pip install selenium beautifulsoup4 pandas webdriver-manager

Usage:
    python bps_sped_scraper.py

Output:
    bps_special_education_programs.csv
"""

import time
import csv
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from bs4 import BeautifulSoup

# ── Selenium imports ──────────────────────────────────────────────────────────
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    print("Missing dependencies. Please run:")
    print("  pip install selenium beautifulsoup4 pandas webdriver-manager")
    sys.exit(1)


# ── Configuration ─────────────────────────────────────────────────────────────

URL = "https://www.bostonpublicschools.org/academics/specialized-services/programs-services"

OUTPUT_CSV = "bps_special_education_programs.csv"

# The 10 target table headings (as they appear on the page).
# Keys are normalized lowercase versions used for matching;
# values are the canonical display names for the CSV.
TARGET_TABLES = {
    "early childhood center - based":          "Early Childhood Center - Based",
    "aba-based classrooms":                    "ABA-Based Classrooms",
    "emotional impairment (ei) - externalizing":
                                               "Emotional Impairment (EI) - Externalizing",
    "emotional impairment (ei) - internalizing":
                                               "Emotional Impairment (EI) - Internalizing",
    "mild intellectual disabilities":          "Mild Intellectual Disabilities",
    "moderate intellectual disabilities":      "Moderate Intellectual Disabilities",
    "severe intellectual disabilities/multiple disabilities":
                                               "Severe Intellectual Disabilities/Multiple Disabilities",
    "specific learning disabilities":          "Specific Learning Disabilities",
    "public day schools":                      "Public Day Schools",
    "special admission schools":               "Special Admission Schools",
}

# Maximum seconds to wait for the page JS to render tables
PAGE_LOAD_TIMEOUT = 30


# ── Selenium helpers ──────────────────────────────────────────────────────────

def build_driver() -> webdriver.Chrome:
    """Return a headless Chrome WebDriver."""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def fetch_rendered_html(url: str, timeout: int = PAGE_LOAD_TIMEOUT) -> str:
    """
    Load *url* with Selenium, wait until at least one <table> is present in
    the DOM, then return the fully-rendered page source.
    """
    driver = build_driver()
    try:
        print(f"[1/3] Loading page: {url}")
        driver.get(url)

        # Wait for at least one <table> tag to appear
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.TAG_NAME, "table"))
        )

        # Extra pause to let any lazy-loaded content finish rendering
        time.sleep(3)

        # Scroll to bottom to trigger any scroll-based lazy loaders
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        html = driver.page_source
        print("[1/3] Page loaded successfully.")
        return html

    finally:
        driver.quit()


# ── HTML parsing ──────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Lowercase, collapse whitespace, strip."""
    return re.sub(r"\s+", " ", text).strip().lower()


def parse_table(table_tag) -> list[dict]:
    """
    Convert a <table> BeautifulSoup tag into a list of row dicts.
    Handles colspan / rowspan via a fill-forward grid algorithm.
    Returns [] if the table has no header row.
    """
    rows = table_tag.find_all("tr")
    if not rows:
        return []

    # ── Build a 2-D grid to handle merged cells ───────────────────────────
    grid: list[list[str]] = []
    span_map: dict[tuple[int, int], str] = {}   # (row_i, col_i) -> value

    for row_i, tr in enumerate(rows):
        cells = tr.find_all(["th", "td"])
        col_i = 0
        row_data: list[str] = []

        for cell in cells:
            # Skip columns already claimed by a previous rowspan
            while (row_i, col_i) in span_map:
                row_data.append(span_map.pop((row_i, col_i)))
                col_i += 1

            text = cell.get_text(separator=" ", strip=True)
            colspan = int(cell.get("colspan", 1))
            rowspan = int(cell.get("rowspan", 1))

            for c_offset in range(colspan):
                row_data.append(text)
                for r_offset in range(1, rowspan):
                    span_map[(row_i + r_offset, col_i + c_offset)] = text
            col_i += colspan

        # Flush any remaining rowspan values at the end of the row
        while (row_i, col_i) in span_map:
            row_data.append(span_map.pop((row_i, col_i)))
            col_i += 1

        grid.append(row_data)

    if not grid:
        return []

    # ── Use first row as headers ──────────────────────────────────────────
    raw_headers = grid[0]
    # Deduplicate header names (e.g. two "School" columns → "School", "School_2")
    seen: dict[str, int] = {}
    headers: list[str] = []
    for h in raw_headers:
        clean = h.strip() or "Column"
        if clean in seen:
            seen[clean] += 1
            headers.append(f"{clean}_{seen[clean]}")
        else:
            seen[clean] = 1
            headers.append(clean)

    records: list[dict] = []
    for data_row in grid[1:]:
        # Pad or trim row to match header width
        padded = data_row + [""] * (len(headers) - len(data_row))
        padded = padded[: len(headers)]
        records.append(dict(zip(headers, padded)))

    return records


def extract_program_tables(html: str) -> list[dict]:
    """
    Parse the rendered HTML and extract all 10 target program tables.
    Returns a flat list of dicts, each row tagged with 'Program_Type'.
    """
    soup = BeautifulSoup(html, "html.parser")

    # BPS/Finalsite pages often wrap each section in a <div> with a heading
    # followed by a <table>.  Strategy:
    #   1. Walk all headings (h1–h4) and <strong> / <b> tags.
    #   2. When a heading text matches a target table name, find the next
    #      sibling <table> (or the nearest descendant table in the same block).

    all_records: list[dict] = []
    matched_tables: set[str] = set()

    # Collect every element that could be a section heading
    heading_tags = soup.find_all(
        lambda tag: tag.name in {"h1", "h2", "h3", "h4", "h5", "strong", "b", "p"}
        and tag.get_text(strip=True)
    )

    for heading in heading_tags:
        heading_text = normalize(heading.get_text())

        matched_key = None
        for key in TARGET_TABLES:
            if key in heading_text or heading_text in key:
                matched_key = key
                break

        if matched_key is None or matched_key in matched_tables:
            continue

        program_label = TARGET_TABLES[matched_key]
        print(f"  ✔ Found section: '{program_label}'")

        # Search for the next <table> in the same parent or nearby siblings
        table_tag = None

        # Case 1: table is a next sibling of the heading
        for sibling in heading.find_next_siblings():
            if sibling.name == "table":
                table_tag = sibling
                break
            # Stop if we hit another heading-like element
            if sibling.name in {"h1", "h2", "h3", "h4"}:
                break

        # Case 2: table is inside the parent container (common with div-wrappers)
        if table_tag is None:
            parent = heading.parent
            if parent:
                table_tag = parent.find("table")

        # Case 3: first table after the heading anywhere in the document
        if table_tag is None:
            table_tag = heading.find_next("table")

        if table_tag is None:
            print(f"  ⚠  No table found for '{program_label}' — section may be empty.")
            continue

        rows = parse_table(table_tag)
        if not rows:
            print(f"  ⚠  Table for '{program_label}' parsed as empty.")
            continue

        # Tag every row with the program type
        for row in rows:
            row["Program_Type"] = program_label

        all_records.extend(rows)
        matched_tables.add(matched_key)

    return all_records


# ── Fallback: direct table search by caption / aria-label ────────────────────

def fallback_extract(html: str) -> list[dict]:
    """
    Secondary strategy: scan ALL <table> tags and use their <caption> or the
    nearest heading to label them, then keep only the 10 target ones.
    """
    soup = BeautifulSoup(html, "html.parser")
    all_records: list[dict] = []

    for table_tag in soup.find_all("table"):
        # Try caption first
        caption = table_tag.find("caption")
        label_text = normalize(caption.get_text()) if caption else ""

        # Try nearest preceding heading
        if not label_text:
            prev = table_tag.find_previous(
                lambda t: t.name in {"h1", "h2", "h3", "h4", "strong", "b"}
            )
            label_text = normalize(prev.get_text()) if prev else ""

        matched_key = None
        for key in TARGET_TABLES:
            if key in label_text or label_text in key:
                matched_key = key
                break

        if matched_key is None:
            continue

        program_label = TARGET_TABLES[matched_key]
        rows = parse_table(table_tag)
        for row in rows:
            row["Program_Type"] = program_label
        all_records.extend(rows)

    return all_records


# ── CSV output ────────────────────────────────────────────────────────────────

def save_csv(records: list[dict], path: str) -> None:
    """Write *records* to a CSV at *path*, with Program_Type as the first column."""
    if not records:
        print("[!] No records to save.")
        return

    # Collect all unique column names; put Program_Type first
    all_cols: list[str] = ["Program_Type"]
    for rec in records:
        for col in rec:
            if col != "Program_Type" and col not in all_cols:
                all_cols.append(col)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    print(f"[3/3] Saved {len(records)} rows → {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # Step 1: Render the page with Selenium
    html = fetch_rendered_html(URL)

    # Step 2: Extract the 10 target tables
    print("[2/3] Parsing HTML for target tables …")
    records = extract_program_tables(html)

    if len(records) == 0:
        print("  Primary extraction found 0 records — trying fallback parser …")
        records = fallback_extract(html)

    found_programs = {r["Program_Type"] for r in records}
    missing = set(TARGET_TABLES.values()) - found_programs
    if missing:
        print(f"\n  ⚠  Could not find data for {len(missing)} table(s):")
        for m in sorted(missing):
            print(f"     • {m}")
        print(
            "\n  This may mean the page structure changed or those sections are"
            " empty / behind a login.  All found data will still be saved.\n"
        )
    else:
        print(f"  ✔ All 10 program tables found ({len(records)} total rows).")

    # Step 3: Save to CSV
    save_csv(records, OUTPUT_CSV)

    # Optional: pretty-print a summary
    if records:
        df = pd.DataFrame(records)
        print("\n── Summary ──────────────────────────────────────────────────────")
        summary = (
            df.groupby("Program_Type")
            .size()
            .reset_index(name="Row Count")
            .sort_values("Program_Type")
        )
        print(summary.to_string(index=False))
        print("─────────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()