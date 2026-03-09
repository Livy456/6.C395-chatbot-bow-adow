import pandas as pd
from rapidfuzz import process, fuzz

# Load raw datasets
public_schools_file = "data_cleaning/raw data/public_schools.csv"

boston = pd.read_csv(public_schools_file)
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