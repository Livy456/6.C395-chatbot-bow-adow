"""
DESE Boston Public Schools — Wide-Format Profile Scraper
=========================================================
Produces a single CSV where EACH ROW = ONE SCHOOL and
EACH COLUMN = ONE FEATURE.

Output columns (in order):
  org_code
  school_name
  school_address
  school_phone_number
  principal_name
  principal_email
  school_type
  enrollment
  grades_served
  student_teacher_ratio
  --- Race / Ethnicity ---
  race_african_american_count
  race_african_american_pct
  race_american_indian_alaskan_native_count
  race_american_indian_alaskan_native_pct
  race_asian_count
  race_asian_pct
  race_hispanic_latino_count
  race_hispanic_latino_pct
  race_multi_race_non_hispanic_count
  race_multi_race_non_hispanic_pct
  race_native_hawaiian_pacific_islander_count
  race_native_hawaiian_pacific_islander_pct
  race_white_count
  race_white_pct
  --- Selected Populations ---
  pop_high_needs_count
  pop_high_needs_pct
  pop_english_learners_count
  pop_english_learners_pct
  pop_first_language_not_english_count
  pop_first_language_not_english_pct
  pop_low_income_count
  pop_low_income_pct
  pop_students_with_disabilities_count
  pop_students_with_disabilities_pct

Requirements:
    pip install requests beautifulsoup4 selenium webdriver-manager lxml pandas

Usage:
    python dese_bps_wide_scraper.py

Output:
    dese_bps_all_schools.csv
"""

import re
import csv
import time
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL             = "https://profiles.doe.mass.edu"
BOSTON_DISTRICT_CODE = "00350000"
OUTPUT_CSV     = "dese_bps_all_schools.csv"
REQUEST_DELAY  = 1.2   # seconds between HTTP requests (be polite)
SELENIUM_DELAY = 2.5

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── Final output column order ─────────────────────────────────────────────────
OUTPUT_COLUMNS = [
    "org_code",
    "school_name",
    "school_address",
    "school_phone_number",
    "principal_name",
    "principal_email",
    "school_type",
    "enrollment",
    "grades_served",
    "student_teacher_ratio",
    # Race / Ethnicity
    "race_african_american_count",
    "race_african_american_pct",
    "race_american_indian_alaskan_native_count",
    "race_american_indian_alaskan_native_pct",
    "race_asian_count",
    "race_asian_pct",
    "race_hispanic_latino_count",
    "race_hispanic_latino_pct",
    "race_multi_race_non_hispanic_count",
    "race_multi_race_non_hispanic_pct",
    "race_native_hawaiian_pacific_islander_count",
    "race_native_hawaiian_pacific_islander_pct",
    "race_white_count",
    "race_white_pct",
    # Selected Populations
    "pop_high_needs_count",
    "pop_high_needs_pct",
    "pop_english_learners_count",
    "pop_english_learners_pct",
    "pop_first_language_not_english_count",
    "pop_first_language_not_english_pct",
    "pop_low_income_count",
    "pop_low_income_pct",
    "pop_students_with_disabilities_count",
    "pop_students_with_disabilities_pct",
]

# ── Keyword → column-prefix mapping ──────────────────────────────────────────
# Each entry: (list-of-keywords-to-match, column_prefix)
# The scraper checks if ANY keyword appears (case-insensitive) in a row label.

RACE_MAP = [
    (["african american", "black"],                          "race_african_american"),
    (["american indian", "alaskan native", "alaska native"], "race_american_indian_alaskan_native"),
    (["asian"],                                              "race_asian"),
    (["hispanic", "latino"],                                 "race_hispanic_latino"),
    (["multi-race", "multi race", "two or more",
      "non-hispanic", "not hispanic"],                       "race_multi_race_non_hispanic"),
    (["native hawaiian", "pacific islander"],                "race_native_hawaiian_pacific_islander"),
    (["white"],                                              "race_white"),
]

POPULATION_MAP = [
    (["high need"],                                          "pop_high_needs"),
    (["english learner", "english language learner", "ell"], "pop_english_learners"),
    (["first language not english", "flne"],                 "pop_first_language_not_english"),
    (["low income", "economically disadvantaged"],           "pop_low_income"),
    (["students with disabilities", "with disabilities",
      "special education"],                                  "pop_students_with_disabilities"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────────────────────────────────────

def blank_row(org_code: str = "") -> dict:
    """Return an empty wide-format row dict with all columns set to ''."""
    row = {col: "" for col in OUTPUT_COLUMNS}
    row["org_code"] = org_code
    return row


def clean_pct(text: str) -> str:
    """Extract a numeric percentage string like '72.5' from raw cell text."""
    text = text.replace(",", "")
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%?", text)
    return m.group(1) if m else ""


def clean_count(text: str) -> str:
    """Extract an integer count string from raw cell text."""
    text = text.replace(",", "").replace("%", "").strip()
    m = re.search(r"(\d+)", text)
    return m.group(1) if m else ""


def match_keywords(label: str, keywords: list[str]) -> bool:
    label_lower = label.lower()
    return any(kw in label_lower for kw in keywords)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 – Discover all Boston school org codes
# ─────────────────────────────────────────────────────────────────────────────

def discover_org_codes() -> list[tuple[str, str]]:
    """
    Return the full list of Boston Public School (org_code, school_name) pairs.

    The DESE dropdown does not embed org codes in <option> values (they use
    sequential integers), and every navigation link on a school's page points
    back to that same school — so dynamic discovery always collapses to a
    single school.  We therefore go straight to the verified hardcoded list.
    """
    schools = _hardcoded_bps_orgcodes()
    log.info("Using hardcoded BPS school list: %d schools.", len(schools))
    return schools


def _hardcoded_bps_orgcodes() -> list[tuple[str, str]]:
    return [
        ("00350302", "Adams Elementary School"),
        ("00350525", "Albert D Holland School of Technology"),
        ("00350066", "Alighieri Dante Montessori School"),
        ("00350541", "Another Course To College"),
        ("00350003", "Baldwin Early Learning Pilot Academy"),
        ("00350278", "Bates Elementary School"),
        ("00350021", "Beethoven Elementary School"),
        ("00350390", "Blackstone Elementary School"),
        ("00350548", "Boston Adult Tech Academy"),
        ("00350546", "Boston Arts Academy"),
        ("00350755", "Boston Collaborative High School"),
        ("00350507", "Boston International High School & Newcomers Academy"),
        ("00350545", "Boston Latin Academy"),
        ("00350560", "Boston Latin School"),
        ("00350012", "Boston Teachers Union Elementary Pilot School"),
        ("00350215", "Bradley Elementary School"),
        ("00350505", "Brighton High School"),
        ("00350036", "Carter School"),
        ("00350360", "Channing Elementary School"),
        ("00350515", "Charlestown High School"),
        ("00350154", "Chittick Elementary School"),
        ("00350298", "Clap Elementary School"),
        ("00350518", "Community Academy"),
        ("00350581", "Community Academy of Science and Health"),
        ("00350146", "Condon K-8 School"),
        ("00350122", "Conley Elementary School"),
        ("00350020", "Curley K-8 School"),
        ("00350074", "Dearborn STEM Academy"),
        ("00350268", "Dever Elementary School"),
        ("00350009", "East Boston Early Education Center"),
        ("00350530", "East Boston High School"),
        ("00350375", "Edison Elementary School"),
        ("00350096", "Eliot K-8 Innovation School"),
        ("00350072", "Ellis Elementary School"),
        ("00350008", "Ellison-Parks Early Education School"),
        ("00350535", "English High School"),
        ("00350088", "Everett Elementary School"),
        ("00350522", "Excel High School"),
        ("00350540", "Fenway High School"),
        ("00350326", "Gardner Pilot Academy"),
        ("00350543", "Greater Egleston High School"),
        ("00350308", "Greenwood Sarah K-8 School"),
        ("00350135", "Grew Elementary School"),
        ("00350062", "Guild Elementary School"),
        ("00350243", "Hale Elementary School"),
        ("00350077", "Haley Pilot School"),
        ("00350200", "Harvard-Kent Elementary School"),
        ("00350010", "Haynes Early Education Center"),
        ("00350266", "Henderson K-12 Inclusion School Lower"),
        ("00350426", "Henderson K-12 Inclusion School Upper"),
        ("00350153", "Hennigan Elementary School"),
        ("00350691", "Hernandez Elementary School"),
        ("00350015", "Higginson Inclusion K0-2 School"),
        ("00350377", "Higginson-Lewis K-8 School"),
        ("00350138", "Holmes Elementary School"),
        ("00350750", "Horace Mann School for the Deaf Hard of Hearing"),
        ("00350182", "Hurley K-8 School"),
        ("00350166", "Kennedy John F Elementary School"),
        ("00350264", "Kennedy Patrick J Elementary School"),
        ("00350328", "Kenny Elementary School"),
        ("00350190", "Kilmer K-8 School"),
        ("00350376", "King Elementary School"),
        ("00350001", "Lee Academy"),
        ("00350183", "Lee K-8 School"),
        ("00350262", "Lyndon K-8 School"),
        ("00350004", "Lyon Elementary School"),
        ("00350655", "Lyon High School"),
        ("00350537", "Madison Park Technical Vocational High School"),
        ("00350184", "Manning Elementary School"),
        ("00350549", "Margarita Muniz Academy"),
        ("00350656", "Mario Umana Academy"),
        ("00350304", "Mason Elementary School"),
        ("00350227", "Mather Elementary School"),
        ("00350016", "Mattahunt Elementary School"),
        ("00350080", "McKay K-8 School"),
        ("00350363", "Melvin H. King South End Academy"),
        ("00350100", "Mendell Elementary School"),
        ("00350378", "Mildred Avenue K-8 School"),
        ("00350237", "Mozart Elementary School"),
        ("00350240", "Murphy K-8 School"),
        ("00350542", "New Mission High School"),
        ("00350575", "O'Bryant School of Math & Science"),
        ("00350141", "O'Donnell Elementary School"),
        ("00350258", "Ohrenberger School"),
        ("00350257", "Orchard Gardens K-8 School"),
        ("00350156", "Otis Elementary School"),
        ("00350231", "Perkins Elementary School"),
        ("00350255", "Perry Elementary School"),
        ("00350286", "Quincy Elementary School"),
        ("00350565", "Quincy Upper School"),
        ("00350116", "Roosevelt K-8 School"),
        ("00350366", "Russell Elementary School"),
        ("00350558", "Ruth Batson Academy"),
        ("00350017", "Sarah Roberts Elementary School"),
        ("00350014", "Shaw-Taylor Elementary School"),
        ("00350690", "Snowden International High School"),
        ("00350657", "TechBoston Academy"),
        ("00350229", "Tobin K-8 School"),
        ("00350370", "Trotter Elementary School"),
        ("00350181", "Tynan Elementary School"),
        ("00350167", "UP Academy Holland"),
        ("00350346", "Warren-Prescott K-8 School"),
        ("00350374", "Winship Elementary School"),
        ("00350180", "Winthrop Elementary School"),
        ("00350380", "Young Achievers Elementary School"),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 – Scrape general/contact page  (requests + BS4)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_general_page(org_code: str, row: dict) -> dict:
    """
    Fetch the General/Contact page and populate:
      school_name, school_address, school_phone_number,
      principal_name, principal_email,
      school_type, enrollment, grades_served, student_teacher_ratio

    Root cause of the previous bug
    --------------------------------
    DESE uses an <hr> element as the visual "---" divider between a label and
    its value inside each <td>:

        <td>Enrollment <hr/> 222</td>

    BeautifulSoup's get_text() silently drops <hr> (it is a void element with
    no text content), so the combined cell text became just "Enrollment 222"
    with no "---" in it.  The old guard `if "---" not in cell_text: continue`
    therefore skipped every stat cell, leaving all three fields empty.

    Fix
    ----
    Use get_text(separator=" ") so each cell is a single space-joined string,
    then match on whether the text STARTS WITH the known keyword and extract
    the trailing value — no reliance on "---" appearing at all.
    """
    url = (
        f"{BASE_URL}/general/general.aspx"
        f"?topNavID=1&leftNavId=100&orgcode={org_code}&orgtypecode=6"
    )
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("  [%s] General page error: %s", org_code, exc)
        return row

    soup      = BeautifulSoup(resp.text, "lxml")
    full_text = soup.get_text(separator="\n")

    # ── School name ───────────────────────────────────────────────────────
    for h1 in soup.find_all("h1"):
        text = h1.get_text(strip=True)
        if text and "Massachusetts School" not in text:
            row["school_name"] = re.sub(r"\s*\(\d{8}\)\s*$", "", text).strip()
            break

    # ── Address (near Google Maps link) ──────────────────────────────────
    maps = soup.find("a", href=re.compile(r"google\.com/maps"))
    if maps:
        row["school_address"] = maps.get_text(separator=" ", strip=True)

    # ── Phone (first 617-xxx-xxxx found in page) ──────────────────────────
    phones = list(dict.fromkeys(
        re.findall(r"6\d{2}[-.\s]\d{3}[-.\s]\d{4}", full_text)
    ))
    if phones:
        row["school_phone_number"] = phones[0]

    # ── Principal email ───────────────────────────────────────────────────
    for a in soup.find_all("a", href=re.compile(r"mailto:")):
        href = a["href"].replace("mailto:", "").strip()
        if "@" in href:
            row["principal_email"] = href
            break

    # ── Principal name ────────────────────────────────────────────────────
    m = re.search(
        r"Principal\s*[:\-]\s*([A-Z][A-Za-z\s'\-\.]+?)(?:\n|$)",
        full_text, re.MULTILINE
    )
    if m:
        row["principal_name"] = m.group(1).strip()

    # ── Summary stats: Enrollment / Grades Served / Student–Teacher Ratio ─
    #
    # HTML structure (simplified):
    #   <td>School Type <hr/> Public</td>
    #   <td>Enrollment <hr/> 222</td>
    #   <td>Grades Served <hr/> PK - 06</td>
    #   <td>Student / Teacher Ratio <hr/> 7.1 to 1</td>
    #
    # get_text(separator=" ") joins child nodes with a space, so <hr/> becomes
    # a space gap → "Enrollment  222".  We normalise whitespace then check
    # whether the cell starts with a known keyword and grab the remainder.

    for td in soup.find_all("td"):
        # Join with space so <hr/> becomes a whitespace gap, not nothing.
        raw = td.get_text(separator=" ", strip=True)
        # Collapse multiple spaces / tabs into one.
        text = re.sub(r"\s+", " ", raw).strip()
        text_lower = text.lower()

        # --- School Type ---
        if text_lower.startswith("school type") and not row.get("school_type"):
            val = text[len("school type"):].strip().lstrip("-— ").strip()
            if val:
                row["school_type"] = val

        # --- Enrollment ---
        # Cell text: "Enrollment 222"  or  "2025-26 Enrollment 222"
        elif "enrollment" in text_lower and not row.get("enrollment"):
            m = re.search(r"\b(\d[\d,]*)\b", text)
            if m:
                row["enrollment"] = m.group(1).replace(",", "")

        # --- Grades Served ---
        elif text_lower.startswith("grades served") and not row.get("grades_served"):
            val = text[len("grades served"):].strip().lstrip("-— ").strip()
            if val:
                row["grades_served"] = val

        # --- Student / Teacher Ratio ---
        elif (
            "student" in text_lower
            and "teacher" in text_lower
            and "ratio" in text_lower
            and not row.get("student_teacher_ratio")
        ):
            m = re.search(r"(\d+\.?\d*\s*to\s*1)", text, re.IGNORECASE)
            if m:
                row["student_teacher_ratio"] = m.group(1).strip()

    # ── Final safety net: scan full page text with multiline regex ─────────
    # Catches any layout variant where stats are not in their own <td>.
    if not row.get("enrollment"):
        m = re.search(r"Enrollment[^0-9]{0,30}?(\d[\d,]+)", full_text, re.DOTALL)
        if m:
            row["enrollment"] = m.group(1).replace(",", "").strip()

    if not row.get("grades_served"):
        m = re.search(
            r"Grades\s+Served[^A-Za-z0-9]{0,20}([A-Z0-9][^\n]{1,30})",
            full_text, re.DOTALL
        )
        if m:
            row["grades_served"] = m.group(1).strip()

    if not row.get("student_teacher_ratio"):
        m = re.search(
            r"Student\s*/\s*Teacher\s+Ratio[^0-9]{0,30}?(\d+\.?\d*\s*to\s*1)",
            full_text, re.DOTALL | re.IGNORECASE
        )
        if m:
            row["student_teacher_ratio"] = m.group(1).strip()

    if not row.get("school_type"):
        m = re.search(
            r"School\s+Type[^A-Za-z]{0,20}([A-Za-z][^\n]{1,30})",
            full_text, re.DOTALL
        )
        if m:
            row["school_type"] = m.group(1).strip()

    return row


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – Scrape student demographics page  (requests + BS4)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_demographics_from_soup(soup: BeautifulSoup, row: dict) -> dict:
    """
    Walk every <table> in *soup* and map row labels to the correct
    wide-format column using RACE_MAP and POPULATION_MAP.

    Expected table structure on DESE student pages:
      | Label           | Percent  | Count |
      | Hispanic        | 72.5 %   | 160   |
    or the percent/count columns may be swapped. We detect which is which
    by checking whether a cell contains a '%' symbol.
    """
    for tbl in soup.find_all("table"):
        for tr in tbl.find_all("tr"):
            cells = [
                td.get_text(separator=" ", strip=True)
                for td in tr.find_all(["td", "th"])
            ]
            if len(cells) < 2:
                continue

            label = cells[0]

            # Identify which cells are pct vs count
            pct_val   = ""
            count_val = ""
            for cell in cells[1:]:
                if "%" in cell or (
                    re.search(r"\d", cell) and float(
                        re.sub(r"[^\d.]", "", cell or "0") or "0"
                    ) <= 100
                    and "." in cell
                ):
                    if not pct_val:
                        pct_val = clean_pct(cell)
                elif re.search(r"\d", cell):
                    if not count_val:
                        count_val = clean_count(cell)

            # If both candidates look like integers, use position heuristic:
            # DESE usually orders: Label | Pct | Count
            if not pct_val and len(cells) >= 2:
                pct_val   = clean_pct(cells[1])
            if not count_val and len(cells) >= 3:
                count_val = clean_count(cells[2])

            # Match against race groups
            for keywords, prefix in RACE_MAP:
                if match_keywords(label, keywords):
                    if pct_val and not row.get(f"{prefix}_pct"):
                        row[f"{prefix}_pct"]   = pct_val
                    if count_val and not row.get(f"{prefix}_count"):
                        row[f"{prefix}_count"] = count_val
                    break

            # Match against selected populations
            for keywords, prefix in POPULATION_MAP:
                if match_keywords(label, keywords):
                    if pct_val and not row.get(f"{prefix}_pct"):
                        row[f"{prefix}_pct"]   = pct_val
                    if count_val and not row.get(f"{prefix}_count"):
                        row[f"{prefix}_count"] = count_val
                    break

    return row


def scrape_student_page(org_code: str, row: dict) -> dict:
    """
    Fetch /profiles/student.aspx and parse race/ethnicity + populations
    directly into *row*.
    """
    url = (
        f"{BASE_URL}/profiles/student.aspx"
        f"?orgcode={org_code}&orgtypecode=6"
    )
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("  [%s] Student page error: %s", org_code, exc)
        return row

    soup = BeautifulSoup(resp.text, "lxml")
    return _parse_demographics_from_soup(soup, row)


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 – Selenium fallback for JS-rendered demographic charts
# ─────────────────────────────────────────────────────────────────────────────

def build_driver() -> "webdriver.Chrome":
    opts = ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(f"user-agent={HTTP_HEADERS['User-Agent']}")
    svc = ChromeService(ChromeDriverManager().install())
    return webdriver.Chrome(service=svc, options=opts)


def _demographics_filled(row: dict) -> bool:
    """Return True if at least 4 demographic fields were populated."""
    demo_cols = [c for c in OUTPUT_COLUMNS
                 if c.startswith("race_") or c.startswith("pop_")]
    filled = sum(1 for c in demo_cols if row.get(c))
    return filled >= 4


def scrape_demographics_selenium(
    org_code: str,
    row: dict,
    driver: "webdriver.Chrome",
) -> dict:
    """
    Load the school's student page in a real browser, wait for charts,
    then parse the rendered HTML.  Also attempts to read Chart.js JSON
    embedded in <script> blocks.
    """
    url = (
        f"{BASE_URL}/profiles/student.aspx"
        f"?orgcode={org_code}&orgtypecode=6"
    )
    driver.get(url)

    # Wait for at least one table or canvas to appear
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "table, canvas, .chart-container")
            )
        )
    except Exception:
        pass
    time.sleep(SELENIUM_DELAY)

    html = driver.page_source
    soup = BeautifulSoup(html, "lxml")

    # Strategy A: parse rendered HTML tables (same logic as static scraper)
    row = _parse_demographics_from_soup(soup, row)

    # Strategy B: extract Chart.js JSON from inline <script> blocks
    if not _demographics_filled(row):
        for script in soup.find_all("script"):
            src = script.string or ""
            if "labels" not in src and "datasets" not in src:
                continue

            labels_m  = re.search(r"['\"]?labels['\"]?\s*:\s*\[([^\]]+)\]",  src)
            data_m    = re.search(r"['\"]?data['\"]?\s*:\s*\[([^\]]+)\]",    src)
            if not labels_m or not data_m:
                continue

            labels = re.findall(r"['\"]([^'\"]+)['\"]", labels_m.group(1))
            values = re.findall(r"[\d.]+",               data_m.group(1))

            for label, val in zip(labels, values):
                pct = val if float(val) <= 100 else ""

                for keywords, prefix in RACE_MAP:
                    if match_keywords(label, keywords):
                        if pct and not row.get(f"{prefix}_pct"):
                            row[f"{prefix}_pct"] = pct
                        break

                for keywords, prefix in POPULATION_MAP:
                    if match_keywords(label, keywords):
                        if pct and not row.get(f"{prefix}_pct"):
                            row[f"{prefix}_pct"] = pct
                        break

    return row


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 – Regex fallback on raw page text
# ─────────────────────────────────────────────────────────────────────────────

def scrape_demographics_regex_fallback(org_code: str, row: dict) -> dict:
    """
    Last-resort: download the student page as text and use regex to hunt
    for known group names followed by numbers.
    """
    url = (
        f"{BASE_URL}/profiles/student.aspx"
        f"?orgcode={org_code}&orgtypecode=6"
    )
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return row

    text = BeautifulSoup(resp.text, "lxml").get_text(separator="\n")

    def _search(label: str) -> tuple[str, str]:
        """Return (pct, count) strings near *label* in the text."""
        pattern = rf"{re.escape(label)}[^\n]{{0,80}}?(\d{{1,3}}\.?\d*)\s*%[^\n]{{0,40}}?(\d+)"
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1), m.group(2)
        # Try reversed order (count then pct)
        pattern2 = rf"{re.escape(label)}[^\n]{{0,80}}?(\d+)[^\n]{{0,40}}?(\d{{1,3}}\.?\d*)\s*%"
        m2 = re.search(pattern2, text, re.IGNORECASE)
        if m2:
            return m2.group(2), m2.group(1)
        # Pct only
        m3 = re.search(
            rf"{re.escape(label)}[^\n]{{0,80}}?(\d{{1,3}}\.?\d*)\s*%",
            text, re.IGNORECASE
        )
        if m3:
            return m3.group(1), ""
        return "", ""

    FALLBACK_LABELS = {
        "race_african_american":               ["African American", "Black"],
        "race_american_indian_alaskan_native": ["American Indian", "Alaskan Native"],
        "race_asian":                          ["Asian"],
        "race_hispanic_latino":                ["Hispanic", "Latino"],
        "race_multi_race_non_hispanic":        ["Multi-Race", "Two or More"],
        "race_native_hawaiian_pacific_islander": ["Native Hawaiian", "Pacific Islander"],
        "race_white":                          ["White"],
        "pop_high_needs":                      ["High Needs"],
        "pop_english_learners":                ["English Learner", "ELL"],
        "pop_first_language_not_english":      ["First Language Not English", "FLNE"],
        "pop_low_income":                      ["Low Income", "Economically Disadvantaged"],
        "pop_students_with_disabilities":      ["Students with Disabilities"],
    }

    for prefix, label_list in FALLBACK_LABELS.items():
        if row.get(f"{prefix}_pct"):
            continue  # already populated
        for label in label_list:
            pct, count = _search(label)
            if pct:
                row[f"{prefix}_pct"]   = pct
                row[f"{prefix}_count"] = count
                break

    return row


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 – Write the final wide CSV
# ─────────────────────────────────────────────────────────────────────────────

def write_wide_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    log.info("Saved %d school rows → %s", len(rows), path)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # 1. Discover schools
    schools = discover_org_codes()
    log.info("Scraping %d BPS schools …\n", len(schools))

    # 2. Launch Selenium (optional)
    driver = None
    if SELENIUM_AVAILABLE:
        log.info("Starting headless Chrome …")
        try:
            driver = build_driver()
        except Exception as exc:
            log.warning("Selenium unavailable: %s", exc)
    else:
        log.warning("selenium not installed — JS chart data will be skipped.")

    all_rows: list[dict] = []

    for idx, (org_code, fallback_name) in enumerate(schools, 1):
        log.info("[%d/%d]  %s  (%s)", idx, len(schools), fallback_name, org_code)

        row = blank_row(org_code)

        # ── General / contact page ────────────────────────────────────────
        row = scrape_general_page(org_code, row)

        # Use fallback name if page didn't return a name
        if not row["school_name"]:
            row["school_name"] = fallback_name

        time.sleep(REQUEST_DELAY)

        # ── Student demographics — primary (static HTML) ──────────────────
        row = scrape_student_page(org_code, row)
        time.sleep(REQUEST_DELAY)

        # ── Demographics — Selenium fallback ─────────────────────────────
        if not _demographics_filled(row) and driver:
            log.info("  → Selenium fallback for demographics")
            try:
                row = scrape_demographics_selenium(org_code, row, driver)
            except Exception as exc:
                log.warning("  Selenium error: %s", exc)

        # ── Demographics — regex last resort ─────────────────────────────
        if not _demographics_filled(row):
            log.info("  → Regex fallback for demographics")
            row = scrape_demographics_regex_fallback(org_code, row)

        demo_cols  = [c for c in OUTPUT_COLUMNS if c.startswith("race_") or c.startswith("pop_")]
        demo_count = sum(1 for c in demo_cols if row.get(c))
        log.info(
            "  ✔ %-45s | Enroll: %-5s | Grades: %-8s | Demo fields: %d/%d",
            row["school_name"][:45],
            row["enrollment"],
            row["grades_served"],
            demo_count,
            len(demo_cols),
        )

        all_rows.append(row)

    # 3. Close browser
    if driver:
        driver.quit()

    # 4. Save CSV
    write_wide_csv(all_rows, OUTPUT_CSV)

    # 5. Summary
    filled = sum(1 for r in all_rows if _demographics_filled(r))
    print("\n══ Scrape Complete ══════════════════════════════════════════════")
    print(f"  Total schools scraped:             {len(all_rows)}")
    print(f"  Schools with demographic data:     {filled}")
    print(f"  Columns per row:                   {len(OUTPUT_COLUMNS)}")
    print(f"  Output file: {OUTPUT_CSV}")
    print("═════════════════════════════════════════════════════════════════\n")


if __name__ == "__main__":
    main()
