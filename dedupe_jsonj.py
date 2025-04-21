# --- START OF FILE dedupe_json.py ---

# Step 1: De-duplicate and Create Unique Product (JSONL) & Category Mapping (CSV) Files
# Reads data from the JSONL output file, avoiding CSV parsing issues.
# Outputs unique products to JSONL.

import pandas as pd
import json
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration ---
INPUT_JSONL = 'output/woolworths_products_nutrition.jsonl' # Input JSONL file
OUTPUT_DIR = 'output'
# --- MODIFIED: Define JSONL output for unique products ---
UNIQUE_PRODUCTS_JSONL = os.path.join(OUTPUT_DIR, 'unique_products_with_categories.jsonl')
# --- Keep CSV for mapping/utility files ---
CATEGORY_MAPPING_CSV = os.path.join(OUTPUT_DIR, 'category_stockcode_mapping.csv')
UNIQUE_STOCKCODES_CSV = os.path.join(OUTPUT_DIR, 'unique_stockcodes_for_rescraping.csv')

# Columns containing the category information that varies
CATEGORY_COLUMNS = ['ScrapedCategoryID', 'ScrapedCategoryName', 'ScrapedCategoryParentID', 'ScrapedCategoryLevel']

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

logging.info(f"Loading potentially duplicated data from JSONL file: {INPUT_JSONL}")

try:
    # Read JSONL file
    df = pd.read_json(INPUT_JSONL, lines=True, dtype={'Stockcode': str})
    logging.info(f"Loaded {len(df)} records from JSONL.")

    # --- Data Cleaning ---
    original_count = len(df)
    df.dropna(subset=['Stockcode'], inplace=True)
    if len(df) < original_count:
        logging.warning(f"Dropped {original_count - len(df)} records with missing Stockcode.")

    # --- Create Output 1: Unique Products with Aggregated Categories (Now as DataFrame) ---
    logging.info("Generating unique product data with aggregated category info...")

    found_category_cols_agg = []
    for col in CATEGORY_COLUMNS:
        if col in df.columns:
            df[col] = df[col].fillna('')
            found_category_cols_agg.append(col)
        else:
             logging.warning(f"(Aggregation) Expected category column '{col}' not found.")

    if not found_category_cols_agg:
        logging.warning("No category columns found for aggregation. 'All_Categories_Info' will be empty.")

    def process_group_for_unique_product(group):
        result_row = group.iloc[0].copy()
        if found_category_cols_agg:
            categories_df = group[found_category_cols_agg].copy().drop_duplicates()
            # Store the actual list of dictionaries, not a JSON string yet
            result_row['All_Categories_Info'] = categories_df.astype(str).to_dict('records')
        else:
            result_row['All_Categories_Info'] = []
        result_row = result_row.drop(labels=found_category_cols_agg, errors='ignore')
        result_row = result_row.drop(labels=['Stockcode'], errors='ignore')
        return result_row

    # Apply the grouping and aggregation
    df_unique_products = df.groupby('Stockcode', sort=False, group_keys=False).apply(process_group_for_unique_product)

    # Reset index to bring 'Stockcode' back as a column
    df_unique_products = df_unique_products.reset_index()

    # --- MODIFICATION: Save unique products to JSONL instead of CSV ---
    logging.info(f"Generated {len(df_unique_products)} unique product records.")
    logging.info(f"Saving unique product data to JSONL: {UNIQUE_PRODUCTS_JSONL}")
    try:
        with open(UNIQUE_PRODUCTS_JSONL, 'w', encoding='utf-8') as f_jsonl:
            # Convert DataFrame rows to dictionaries
            for product_dict in df_unique_products.to_dict('records'):
                 # Clean dictionary for JSON serialization (remove NaN which json.dumps doesn't like)
                 # The 'All_Categories_Info' field already contains the list of dicts directly
                 serializable_dict = {k: v for k, v in product_dict.items() if not pd.isna(v)}
                 # Convert the dictionary to a JSON string
                 json_string = json.dumps(serializable_dict, ensure_ascii=False)
                 # Write the JSON string as a line in the JSONL file
                 f_jsonl.write(json_string + '\n')
        logging.info(f"Successfully saved unique product data to: {UNIQUE_PRODUCTS_JSONL}")
    except Exception as e:
        logging.error(f"Failed to save unique products JSONL: {e}", exc_info=True)
    # --- END MODIFICATION ---


    # --- Create Output 2: Category-Stockcode Mapping (Keep as CSV) ---
    logging.info("Generating category-stockcode mapping file...")
    map_cols_to_keep = ['Stockcode'] + CATEGORY_COLUMNS
    map_cols_to_keep = [col for col in map_cols_to_keep if col in df.columns]
    if 'Stockcode' in map_cols_to_keep and 'ScrapedCategoryID' in map_cols_to_keep:
        df_mapping = df[map_cols_to_keep].copy()
        df_mapping.dropna(subset=['Stockcode', 'ScrapedCategoryID'], inplace=True)
        original_mapping_rows = len(df_mapping)
        df_mapping.drop_duplicates(subset=['Stockcode', 'ScrapedCategoryID'], inplace=True)
        logging.info(f"Generated {len(df_mapping)} unique category-stockcode mappings (removed {original_mapping_rows - len(df_mapping)} duplicate mappings).")
        df_mapping.to_csv(CATEGORY_MAPPING_CSV, index=False, encoding='utf-8')
        logging.info(f"Saved category-stockcode mapping to: {CATEGORY_MAPPING_CSV}")
    else:
        logging.warning("Could not generate category mapping file because 'Stockcode' or 'ScrapedCategoryID' columns were missing.")

    # --- Create Output 3: Unique Stockcodes for Re-scraping (Keep as CSV) ---
    logging.info("Generating unique stockcodes file...")
    unique_stockcodes = df_unique_products[['Stockcode']].copy() # Get stockcodes from the unique df
    unique_stockcodes.to_csv(UNIQUE_STOCKCODES_CSV, index=False, encoding='utf-8')
    logging.info(f"Saved {len(unique_stockcodes)} unique stockcodes for re-scraping to: {UNIQUE_STOCKCODES_CSV}")

except FileNotFoundError:
    logging.error(f"ERROR: Input JSONL file not found: {INPUT_JSONL}")
except Exception as e:
    logging.error(f"An unexpected error occurred during processing: {e}", exc_info=True)

# --- END OF FILE dedupe_json.py ---