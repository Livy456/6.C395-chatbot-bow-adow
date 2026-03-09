# Boston Public Schools Web Scraper

This document explains how the Boston Public Schools (BPS) web scraper works: where data comes from, what it extracts, and how the outputs are produced.

## Overview

The scraper is built with **requests** and **BeautifulSoup** (with optional **gdown** and **pdfplumber** for the PDF source). It does three main things:

1. **Build a list of BPS schools** from the official [School Preview Days PDF](https://drive.google.com/file/d/1M-fkgEs1gGrhA60-Yrc-rinfng14hnlV/view) (hosted on Google Drive) and save it as a JSON file.
2. **Visit each school’s website** and scrape information that is useful for families (grades, dates, programs, contact info, etc.).
3. **Write one CSV file** where each row is a school and each column is a feature (grades, start/end dates, contact, address, etc.).

No browser automation (e.g. Scrapy with JS rendering or Selenium) is used; the scraper only uses HTTP requests and HTML parsing. Some BPS pages load content with JavaScript, so not every field will be filled for every school when that content is not present in the initial HTML.

---

## Step 1: Getting the List of Schools and Saving JSON

### Source

- **Primary:** The [School Preview Days 2025–2026 PDF](https://drive.google.com/file/d/1M-fkgEs1gGrhA60-Yrc-rinfng14hnlV/view) on Google Drive.
- The script downloads this PDF (using **gdown** when available) and parses it with **pdfplumber**.

### How it works

1. **Download the PDF**
   - The script uses the Google Drive file ID from the share link to download the file (e.g. via `gdown`) into the `output` folder (e.g. `output/school_preview_days.pdf`).
   - If `gdown` or `pdfplumber` is not installed, or the download fails, the scraper falls back to other ways to get a school list (see below).

2. **Parse the PDF**
   - **pdfplumber** is used to:
     - Extract **tables** from each page. Rows that look like school names (e.g. contain “School” or “Academy”) are treated as one school per row.
     - Extract **plain text** line by line. Lines that look like school names are also added.
   - For each school name, the script tries to find a **website link**:
     - First it looks for URLs in the PDF that point to `bostonpublicschools.org` (or similar).
     - If none is found, it builds a BPS school URL from the name, e.g.  
       `Otis Elementary School` → `https://www.bostonpublicschools.org/school/otis-elementary-school`.

3. **Deduplication**
   - Schools are deduplicated by school name so each school appears once in the list.

4. **Fallback if the PDF is not used**
   - If the PDF cannot be downloaded or parsed (e.g. missing libraries or network issues), the scraper can:
     - Try to collect links from the BPS school listings page (`/schools-container/schools-listings`), or
     - Use a small built-in list of example BPS schools so the rest of the pipeline still runs.

5. **JSON output**
   - The result is saved as **`output/bps_schools.json`** with this shape:
     - `"schools"`: array of objects.
     - Each object has:
       - **`school`**: school name (string).
       - **`website_link`**: URL of the school’s page (string).

---

## Step 2: Scraping Each School’s Website

For every school in the list from Step 1, the scraper:

1. Sends a **GET** request to `website_link` with a browser-like **User-Agent** (and optional headers) so the server returns HTML.
2. Parses the HTML with **BeautifulSoup**.
3. Fills in as many of the following fields as possible from the page (or from embedded JSON in `<script>` tags):

| Field | What the scraper looks for |
|-------|----------------------------|
| **grades_allowed** | Text patterns like “Grades K–5”, “serves … grades”, or “K-12”. |
| **school_start_date** | Phrases like “first day of school”, “start …” followed by a date. |
| **school_end_date** | Phrases like “last day”, “end of school” followed by a date. |
| **programs** | Mentions of “program”, “dual language”, “STEM”, “arts”, etc. |
| **special_needs_accommodations** | “Special education”, “504”, “IEP”, “special needs”. |
| **contact_info** | Phone numbers (e.g. (617) 635-xxxx), or telephone from JSON-LD. |
| **address** | US-style street address + city/state/zip, or address from JSON-LD. |
| **extracurricular_activities** | “After school”, “extracurricular”, “clubs”, “sports”. |
| **number_students_enrolled** | Numbers near “students enrolled” or “enrollment”. |
| **other_pertinent_info** | Meta description (e.g. `og:description`) or short summary. |

If the page content is loaded only via JavaScript (e.g. a single-page app), the initial HTML may not contain this text; in those cases the scraper will leave the field empty or set a short note (e.g. “see school website”) when it only finds a keyword. No headless browser is used, so JS-rendered content is not executed.

---

## Step 3: Saving Everything to CSV

After processing all schools:

- One **CSV** file is written: **`output/bps_schools_details.csv`**.
- **Each row** = one school.
- **Columns** = the fields above (e.g. `school`, `website_link`, `grades_allowed`, `school_start_date`, …).
- Encoding is UTF-8 so names and addresses display correctly.
- If a value is missing, the cell is empty (or contains a short note when only a keyword was found).

---

## How to Run the Scraper

1. **Install dependencies** (from the project root or `webscraper` folder):
   ```bash
   pip install -r requirements.txt
   ```
   Important packages: `requests`, `beautifulsoup4`, and (for the PDF) `gdown`, `pdfplumber`.

2. **Run the script:**
   ```bash
   python webscraper/scraper.py
   ```
   Or from inside `webscraper`:
   ```bash
   python scraper.py
   ```

3. **Outputs:**
   - `webscraper/output/bps_schools.json` — list of schools and website links.
   - `webscraper/output/bps_schools_details.csv` — one row per school, one column per feature.

The script adds a short delay (e.g. 0.5 seconds) between school requests to reduce load on the server.

---

## Summary

| Step | Input | Output |
|------|--------|--------|
| 1 | Google Drive PDF (School Preview Days) | `bps_schools.json` (school name + website link per school) |
| 2 | Each school’s `website_link` | Fetched HTML (and optional JSON in page) |
| 3 | Scraped fields for all schools | `bps_schools_details.csv` (one row per school, one column per feature) |

The scraper uses only **requests** and **BeautifulSoup** for HTTP and HTML; **gdown** and **pdfplumber** are used only to obtain and parse the PDF from the given Google Drive link. A markdown file (this document) describes how the web scraper works end to end.
