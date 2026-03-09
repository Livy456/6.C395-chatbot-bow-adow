Great question — this is fundamentally a **data merging (ETL) problem**. The key challenge is that each dataset uses a different school identifier, so you need to build a common key to join them. Here's how to do it end to end.

---

## Step 1: Download the Three Datasets

**Analyze Boston CSV**
Go to [https://data.boston.gov/dataset/public-schools](https://data.boston.gov/dataset/public-schools) and click the CSV download button. This gives you a file with columns like `SCH_NAME`, `ADDRESS`, `PHONE`, `ZIPCODE`, `LATITUDE`, `LONGITUDE`, and `GRADES`.

**DESE Profiles Export**
Go to [https://profiles.doe.mass.edu/profiles/student.aspx?orgcode=00350000&orgtypecode=5](https://profiles.doe.mass.edu/profiles/student.aspx?orgcode=00350000&orgtypecode=5), which is the Boston district student profile page. Switch the view to "Schools" using the toggle at the top, then use the **Export** button (top right of the data table) to download an Excel/CSV with per-school demographics, enrollment counts, % with disabilities, % ELL, and % economically disadvantaged. Each school will have a `DESE_ID` (a 6–8 digit state code like `00350505`).

**NCES CCD**
Go to [https://nces.ed.gov/ccd/files.asp](https://nces.ed.gov/ccd/files.asp) and download the most recent **Public School Universe Survey** file (it's a large national CSV). You'll filter it down to Boston. Key columns include `NCESSCH` (unique NCES school ID), `LEAID` (district ID — Boston's is `2502790`), `GSLO`/`GSHI` (lowest and highest grade offered), `SCHTYPE` (school type), `CHARTER_TEXT`, and `TITLEI_TEXT`.

---

## Step 2: Establish a Common Join Key

This is the hardest part. None of the three datasets share the same ID system natively, so your join key will be **school name + address string matching**.

Start with the Analyze Boston CSV as your base table since it is Boston-specific and has clean address data. Then:

**Joining DESE → Analyze Boston:** DESE exports include the school name (e.g., `"Quincy Elementary School"`) and sometimes the street address. You do a fuzzy name match — tools like Python's `rapidfuzz` library or Excel's fuzzy lookup add-in work well here. Normalize names first by lowercasing everything, stripping punctuation, and removing common words like "school" or "the." After the fuzzy match, manually review the ~10–15 cases where names differ slightly (e.g., `"Dr. William Henderson"` vs `"Henderson K-12"`).

**Joining NCES CCD → Analyze Boston:** Filter the national NCES file to `LEAID = 2502790` (Boston's district ID), which will give you only Boston schools (~120 rows). Then join on normalized school name the same way. NCES names tend to be more abbreviated, so manual review of edge cases is again worth ~30 minutes of work.

Once you've confirmed matches, add `DESE_ID` and `NCESSCH` as new columns in your base Analyze Boston table. These become your permanent cross-reference keys for future updates.

---

## Step 3: Merge the Tables in Python (or Excel)

In Python with pandas, the merge looks like this:

```python
import pandas as pd
from rapidfuzz import process, fuzz

# Load datasets
boston = pd.read_csv("analyze_boston_schools.csv")
dese = pd.read_csv("dese_profiles.csv")
nces = pd.read_csv("nces_ccd_public_schools.csv")

# Normalize school names
def normalize(name):
    return str(name).lower().strip().replace("school", "").replace(".", "").replace(",", "").strip()

boston["name_key"] = boston["SCH_NAME"].apply(normalize)
dese["name_key"] = dese["School Name"].apply(normalize)
nces["name_key"] = nces["SCHNAM"].apply(normalize)

# Filter NCES to Boston only
nces_boston = nces[nces["LEAID"] == "2502790"].copy()

# Fuzzy match DESE onto Boston base
def fuzzy_match(name, choices, threshold=85):
    result = process.extractOne(name, choices, scorer=fuzz.token_sort_ratio)
    return result[0] if result and result[1] >= threshold else None

dese_names = dese["name_key"].tolist()
boston["dese_match"] = boston["name_key"].apply(lambda x: fuzzy_match(x, dese_names))

# Merge DESE columns in
merged = boston.merge(
    dese[["name_key", "Total Enrollment", "% Students with Disabilities",
          "% ELL", "% Economically Disadvantaged"]],
    left_on="dese_match", right_on="name_key", how="left"
)

# Repeat for NCES
nces_names = nces_boston["name_key"].tolist()
merged["nces_match"] = merged["name_key_x"].apply(lambda x: fuzzy_match(x, nces_names))

final = merged.merge(
    nces_boston[["name_key", "GSLO", "GSHI", "SCHTYPE", "CHARTER_TEXT", "TITLEI_TEXT"]],
    left_on="nces_match", right_on="name_key", how="left"
)

final.to_csv("bps_combined_dataset.csv", index=False)
```

---

## Step 4: Clean and Standardize the Output

After merging, you'll want to rename columns for clarity and drop the intermediate matching keys. A clean final schema for your chatbot dataset looks like:

| Column | Source | Description |
|---|---|---|
| `school_name` | Boston | Official school name |
| `address` | Boston | Street address |
| `phone` | Boston | Contact phone number |
| `zipcode` | Boston | ZIP code |
| `latitude` / `longitude` | Boston | Geo coordinates |
| `total_enrollment` | DESE | Total student count (school size) |
| `pct_disabilities` | DESE | % students with IEPs (special needs proxy) |
| `pct_ell` | DESE | % English Language Learners |
| `pct_econ_disadvantaged` | DESE | % economically disadvantaged |
| `grade_low` / `grade_high` | NCES | Lowest and highest grade offered |
| `school_type` | NCES | Elementary, Middle, High, K–8, etc. |
| `is_charter` | NCES | Charter school flag |
| `is_title1` | NCES | Title I school flag |

---

## Step 5: Handle Unmatched Rows

After your merge, run `final[final["total_enrollment"].isna()]` to find any Boston schools that didn't match to DESE. These are usually schools that opened after the dataset was last published, schools with very unusual names, or closed schools still in one dataset. For each unmatched row, look up the school manually on the DESE profiles page and hard-code the values in. There are typically fewer than 5–10 of these.

---

## Step 6: Enrich with Registration & Grade Level Info

Since registration deadlines and specific grade configurations aren't in any CSV cleanly, the best approach for your chatbot is to add two more columns manually (or via web scraping the BPS website):

- `registration_round` — Round 1 (K0, K1, K2, Gr. 6, 7, 9) or Round 2 (all other grades), sourced from [bostonpublicschools.org/register](https://www.bostonpublicschools.org/enrollment/welcome-services/registration)
- `grades_offered` — A human-readable string like `"K2–Grade 5"`, which you can construct from `grade_low` and `grade_high` once the NCES merge is done

---

## Summary of the Full Pipeline

```
Analyze Boston CSV  ──┐
                       ├──[fuzzy name join]──► merged_bps.csv ──► chatbot-ready dataset
DESE Profiles CSV  ──┤
                       │
NCES CCD CSV       ──┘
```

The whole process in Python takes roughly **2–3 hours the first time**: ~30 minutes of data downloading and inspection, ~1 hour writing and testing the merge logic, and ~30–60 minutes of manual QA on fuzzy match edge cases. After that, re-running it when datasets update takes under 15 minutes.