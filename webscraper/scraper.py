# webscraper/scraper.py
"""
Boston Public Schools web scraper.
1. Fetches school list from BPS PDF (Google Drive) and saves to JSON.
2. Scrapes each school's website for details and saves to CSV.
"""

import json
import csv
import re
import os
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Optional: PDF and Google Drive (install gdown, pdfplumber if using PDF source)
try:
    import gdown
    import pdfplumber
    HAS_PDF_DEPS = True
except ImportError:
    HAS_PDF_DEPS = False

# Output paths (relative to script or project root)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
SCHOOLS_JSON = os.path.join(OUTPUT_DIR, "bps_schools.json")
SCHOOLS_CSV = os.path.join(OUTPUT_DIR, "bps_schools_details.csv")

# Sources
GDRIVE_PDF_URL = "https://drive.google.com/file/d/1M-fkgEs1gGrhA60-Yrc-rinfng14hnlV/view"
GDRIVE_FILE_ID = "1M-fkgEs1gGrhA60-Yrc-rinfng14hnlV"
BPS_BASE_URL = "https://www.bostonpublicschools.org"
BPS_SCHOOL_LIST_URL = "https://www.bostonpublicschools.org/schools-container/schools-listings"

# Request headers to mimic a browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def slugify(name: str) -> str:
    """Convert school name to URL slug (e.g. 'Otis Elementary School' -> 'otis-elementary-school')."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s)
    return s.strip("-") if s else ""


def download_pdf_from_gdrive(file_id: str, dest_path: str) -> bool:
    """Download a file from Google Drive by file ID. Returns True on success."""
    if not HAS_PDF_DEPS:
        print("Install gdown to download from Google Drive: pip install gdown")
        return False
    url = f"https://drive.google.com/uc?id={file_id}"
    try:
        gdown.download(url, dest_path, quiet=False)
        return os.path.isfile(dest_path)
    except Exception as e:
        print(f"Gdown failed: {e}")
        return False


def extract_schools_from_pdf(pdf_path: str) -> list[dict]:
    """
    Parse PDF and extract school names and any website links.
    Returns list of {"school": name, "website_link": url}.
    """
    if not HAS_PDF_DEPS:
        return []
    schools = []
    seen = set()
    url_pattern = re.compile(
        r"https?://(?:www\.)?(?:bostonpublicschools\.org|bps\.org)[^\s\)\]\"]*", re.I
    )
    # Also match generic URLs that might be school pages
    any_url_pattern = re.compile(r"https?://[^\s\)\]\"]+")
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            # Extract all URLs from this page
            urls = url_pattern.findall(text)
            urls_any = any_url_pattern.findall(text)
            # Try tables for structured school names
            tables = page.extract_tables()
            for table in tables or []:
                for row in table:
                    if not row:
                        continue
                    cell = (row[0] if row else "").strip() if isinstance(row[0], str) else ""
                    if not cell or len(cell) < 3:
                        continue
                    # Skip headers and non-school rows
                    if cell.lower().startswith(("school", "date", "preview", "name", "district")):
                        continue
                    # Likely a school name (contains "School" or looks like a name)
                    if "School" in cell or "Academy" in cell or "Acad" in cell:
                        name = cell
                        if name not in seen:
                            seen.add(name)
                            link = ""
                            for u in urls:
                                if "school" in u.lower() or slugify(name) in u.lower():
                                    link = u
                                    break
                            if not link and urls_any:
                                link = urls_any[0]
                            if not link:
                                link = f"{BPS_BASE_URL}/school/{slugify(name)}"
                            schools.append({"school": name, "website_link": link})
            # Fallback: line-by-line for school-like names
            for line in text.splitlines():
                line = line.strip()
                if not line or len(line) < 4:
                    continue
                if "School" in line or "Academy" in line:
                    # Avoid duplicates and header-like lines
                    if line in seen or line.lower().startswith(("school name", "schools", "page")):
                        continue
                    seen.add(line)
                    link = f"{BPS_BASE_URL}/school/{slugify(line)}"
                    for u in urls:
                        if slugify(line) in u:
                            link = u
                            break
                    schools.append({"school": line, "website_link": link})
    # Dedupe by school name
    by_name = {}
    for s in schools:
        by_name[s["school"]] = s
    return list(by_name.values())


def fetch_school_list_from_pdf_source() -> list[dict]:
    """
    Step 1: Get school list from the Google Drive PDF.
    Returns list of {"school": str, "website_link": str}.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf_path = os.path.join(OUTPUT_DIR, "school_preview_days.pdf")
    if not os.path.isfile(pdf_path):
        if not download_pdf_from_gdrive(GDRIVE_FILE_ID, pdf_path):
            # Fallback: minimal list from BPS directory page (may be incomplete if JS-rendered)
            return fetch_school_list_fallback()
    schools = extract_schools_from_pdf(pdf_path)
    if not schools:
        schools = fetch_school_list_fallback()
    return schools


def fetch_school_list_fallback() -> list[dict]:
    """
    Fallback when PDF is unavailable: scrape BPS listings page for school links.
    BPS listings may be JS-rendered; this collects any anchor links to /school/.
    """
    schools = []
    try:
        r = requests.get(BPS_SCHOOL_LIST_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "/school/" in href and "schools-container" not in href:
                full_url = urljoin(BPS_BASE_URL, href)
                name = (a.get_text() or "").strip()
                if not name:
                    # Derive name from path: /school/otis-elementary-school -> Otis Elementary School
                    path = urlparse(full_url).path
                    name = path.rstrip("/").split("/")[-1].replace("-", " ").title()
                if name and full_url:
                    schools.append({"school": name, "website_link": full_url})
        # Dedupe by URL
        seen_urls = set()
        unique = []
        for s in schools:
            if s["website_link"] not in seen_urls:
                seen_urls.add(s["website_link"])
                unique.append(s)
        schools = unique
    except Exception as e:
        print(f"Fallback school list failed: {e}")
    if not schools:
        # Hardcode a few known BPS schools so the pipeline still runs
        for name in ["Otis Elementary School", "Boston Latin School", "Murphy School"]:
            schools.append({
                "school": name,
                "website_link": f"{BPS_BASE_URL}/school/{slugify(name)}",
            })
    return schools


def scrape_school_details(school_name: str, school_url: str) -> dict:
    """
    Step 2: Fetch school page and extract structured info for parents.
    BPS school pages are often JS-rendered; we extract what we can from HTML and any embedded JSON.
    """
    out = {
        "school": school_name,
        "website_link": school_url,
        "grades_allowed": "",
        "school_start_date": "",
        "school_end_date": "",
        "programs": "",
        "special_needs_accommodations": "",
        "contact_info": "",
        "address": "",
        "extracurricular_activities": "",
        "number_students_enrolled": "",
        "other_pertinent_info": "",
    }
    try:
        r = requests.get(school_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(separator=" ", strip=True)

        # Try to find JSON-LD or __NEXT_DATA__ / window.__data for structured info
        for script in soup.find_all("script", type=re.compile(r"application/ld\+json|application/json")):
            try:
                data = json.loads(script.string or "{}")
                if isinstance(data, dict):
                    if data.get("@type") == "EducationalOrganization" or "name" in data:
                        out["address"] = out["address"] or data.get("address", {}).get("streetAddress", "") if isinstance(data.get("address"), dict) else str(data.get("address", ""))
                        out["contact_info"] = out["contact_info"] or data.get("telephone", "")
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and (item.get("@type") == "EducationalOrganization" or "name" in item):
                            out["address"] = out["address"] or (item.get("address", {}) or {}).get("streetAddress", "") if isinstance(item.get("address"), dict) else ""
                            out["contact_info"] = out["contact_info"] or item.get("telephone", "")
            except (json.JSONDecodeError, TypeError):
                pass

        # Look for common patterns in text
        if "grades" in text.lower() or "grade" in text.lower():
            for m in re.finditer(r"(?:grades?|serves)\s*[:\s]*([K0-9\-,\s]+?)(?:\s*[;\.\n]|\s+School)", text, re.I):
                g = m.group(1).strip()
                if g and len(g) < 30:
                    out["grades_allowed"] = g
                    break
        if not out["grades_allowed"]:
            for m in re.finditer(r"([K\-12]+(?:\s*[–\-]\s*[K\-12]+)?)", text):
                g = m.group(1).strip()
                if 1 <= len(g) <= 20:
                    out["grades_allowed"] = g
                    break

        # Start/end dates
        for m in re.finditer(r"(?:start|begins?|first day)\s*(?:of school)?\s*[:\s]*([A-Za-z]+\s+\d{1,2},?\s*\d{4})", text, re.I):
            out["school_start_date"] = m.group(1).strip()
            break
        for m in re.finditer(r"(?:end|last day|closes?)\s*[:\s]*([A-Za-z]+\s+\d{1,2},?\s*\d{4})", text, re.I):
            out["school_end_date"] = m.group(1).strip()
            break

        # Phone and address from common patterns
        phone = re.search(r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}", text)
        if phone and not out["contact_info"]:
            out["contact_info"] = phone.group(0)
        addr = re.search(r"\d+\s+[A-Za-z0-9\s,]+(?:Street|St|Avenue|Ave|Blvd|Road|Rd|Way|Drive|Dr|Boston|Roxbury|Dorchester|MA)\s*,?\s*\d{5}", text)
        if addr and not out["address"]:
            out["address"] = addr.group(0).strip()

        # Programs, special needs, extracurriculars (keyword search)
        if "special education" in text.lower() or "special needs" in text.lower() or "504" in text or "IEP" in text:
            out["special_needs_accommodations"] = "Special education/504/IEP support mentioned on site; see school website for details."
        if "enrollment" in text.lower() or "enrolled" in text.lower():
            num = re.search(r"(?:approximately|about|~)?\s*(\d{2,5})\s*(?:students?|enrolled)", text, re.I)
            if num:
                out["number_students_enrolled"] = num.group(1).strip()
        if "after school" in text.lower() or "extracurricular" in text.lower() or "clubs" in text.lower() or "sports" in text.lower():
            out["extracurricular_activities"] = "After school/extracurricular activities mentioned; see school website for full list."
        # Programs (e.g. dual language, STEM)
        if "program" in text.lower() or "dual language" in text.lower() or "stem" in text.lower() or "arts" in text.lower():
            out["programs"] = "Programs mentioned on site; see school website for full list."

        # Meta description as fallback "other" info
        meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        if meta and meta.get("content") and not out["other_pertinent_info"]:
            out["other_pertinent_info"] = meta["content"][:500]
    except requests.RequestException as e:
        out["other_pertinent_info"] = f"Error fetching page: {e}"
    return out


def run_step1_save_json(schools: list[dict]) -> None:
    """Save school list to JSON file."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(SCHOOLS_JSON, "w", encoding="utf-8") as f:
        json.dump({"schools": schools}, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(schools)} schools to {SCHOOLS_JSON}")


def run_step2_step3_save_csv(schools: list[dict]) -> None:
    """Visit each school URL, scrape details, and append to CSV."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fieldnames = [
        "school", "website_link", "grades_allowed", "school_start_date", "school_end_date",
        "programs", "special_needs_accommodations", "contact_info", "address",
        "extracurricular_activities", "number_students_enrolled", "other_pertinent_info",
    ]
    with open(SCHOOLS_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for i, s in enumerate(schools):
            print(f"Scraping ({i+1}/{len(schools)}): {s['school']}")
            row = scrape_school_details(s["school"], s["website_link"])
            writer.writerow(row)
            time.sleep(0.5)  # Be polite to the server
    print(f"Saved details to {SCHOOLS_CSV}")


def main():
    print("Step 1: Fetching list of Boston Public Schools from PDF...")
    schools = fetch_school_list_from_pdf_source()
    if not schools:
        print("No schools found. Check PDF link and dependencies (gdown, pdfplumber).")
        return
    run_step1_save_json(schools)
    print("Step 2 & 3: Scraping each school page and saving to CSV...")
    run_step2_step3_save_csv(schools)
    print("Done.")


if __name__ == "__main__":
    main()
