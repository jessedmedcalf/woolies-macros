# --- START OF FILE limit.py ---

# woolies_scraper.py (Session + Incremental Save + Total Count/Duplicate Detect + Max Page Limit + More Attributes + Parallel)

import requests
import json
import logging
import time
import pandas as pd
import os
import argparse
import concurrent.futures # Added for parallelization

# --- Basic Logging Setup ---
# (Keep unchanged)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s', # Added threadName
    handlers=[
        logging.FileHandler("scraper.log", mode='a'),
        logging.StreamHandler()
    ]
)


# --- Constants ---
# (Keep API URLs, BASE_URL, Headers, Config, File Paths)
CATEGORY_API_URL = "https://www.woolworths.com.au/apis/ui/PiesCategoriesWithSpecials"
PRODUCT_API_URL = "https://www.woolworths.com.au/apis/ui/browse/category"
BASE_URL = "https://www.woolworths.com.au"

SESSION_HEADERS = { # Headers for the session
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
    'Accept-Language': 'en-GB,en-US;q=0.9,en;q=0.8', 'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Connection': 'keep-alive', 'Sec-Ch-Ua': '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    'Sec-Ch-Ua-Mobile': '?0', 'Sec-Ch-Ua-Platform': '"macOS"',
}
SPECIFIC_POST_HEADERS = { # Headers specific to the POST request
    'Accept': 'application/json, text/plain, */*', 'Content-Type': 'application/json;charset=UTF-8',
    'Origin': BASE_URL, 'Sec-Fetch-Dest': 'empty', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Site': 'same-origin',
}

PAGE_SIZE = 36
REQUEST_DELAY_SECONDS = 5 # Delay *within* a single category's pagination
POST_TIMEOUT_SECONDS = 90
GET_TIMEOUT_SECONDS = 30

DISCOVERED_CATEGORIES_CSV = 'output/discovered_categories.csv'
FINAL_OUTPUT_CSV = 'output/woolworths_products_nutrition.csv'
FINAL_OUTPUT_JSONL = 'output/woolworths_products_nutrition.jsonl'
TEST_RUN_OUTPUT_CSV = 'output/woolworths_products_nutrition_test_run.csv'
TEST_RUN_OUTPUT_JSONL = 'output/woolworths_products_nutrition_test_run.jsonl'

# Test Run Configuration
TEST_RUN_CATEGORY_LIMIT = 10
TEST_RUN_PAGE_LIMIT = 5 # Keep test run page limit low

# *** Hard page limit as a safety backup ***
MAX_PAGES_PER_CATEGORY = 200

# *** Parallelization Configuration ***
MAX_WORKERS = 8 # Adjust based on your system and network. Start lower (e.g., 4-8) and increase if stable.

# --- Category Discovery Functions ---
# (Keep unchanged)
def extract_recursive(category_node, category_list):
    node_id = category_node.get("NodeId"); description = category_node.get("Description")
    parent_node_id = category_node.get("ParentNodeId"); node_level = category_node.get("NodeLevel")
    url_friendly_name = category_node.get("UrlFriendlyName")
    if node_id and isinstance(node_id, str) and node_id.startswith('1_'):
        category_list.append({
            "id": node_id, "name": description, "parent_id": parent_node_id,
            "level": node_level, "url_friendly_name": url_friendly_name})
    children = category_node.get("Children", [])
    if children:
        for child_node in children: extract_recursive(child_node, category_list)

def get_categories(session):
    all_categories = []; logging.info(f"Fetching category structure from {CATEGORY_API_URL}...")
    try:
        response = session.get(CATEGORY_API_URL, timeout=GET_TIMEOUT_SECONDS)
        response.raise_for_status(); logging.info(f"Category data received (Status: {response.status_code}).")
        data = response.json(); logging.info("Parsed category JSON.")
        if 'Categories' in data and isinstance(data['Categories'], list):
            for top_level_category in data['Categories']: extract_recursive(top_level_category, all_categories)
        else: logging.warning("Unexpected category response format."); return None
    except requests.exceptions.Timeout: logging.error(f"Timeout fetching categories."); return None
    except requests.exceptions.RequestException as e: logging.error(f"Network error fetching categories: {e}"); return None
    except json.JSONDecodeError as e: logging.error(f"JSON decode error categories: {e}"); return None
    except Exception as e: logging.error(f"Unexpected error categories: {e}"); return None
    if not all_categories: logging.warning("No valid product categories extracted.")
    else: logging.info(f"Extracted {len(all_categories)} potential product categories.")
    return all_categories

# --- Nutrition Parsing Function ---
# (Keep unchanged)
def parse_nutrition(nutrition_string):
    if not nutrition_string or not isinstance(nutrition_string, str): return {}
    try:
        nutrition_data = json.loads(nutrition_string); attributes = nutrition_data.get('Attributes', [])
        if not attributes or not isinstance(attributes, list): return {}
        parsed_info = {}
        for attribute in attributes:
            if not isinstance(attribute, dict): continue
            raw_name = attribute.get("Name"); value = attribute.get("Value")
            if raw_name and isinstance(raw_name, str) and value is not None:
                clean_name = raw_name.replace(" - Total - NIP", "").replace(" Quantity Per 100g", "_per_100g").replace(" Quantity Per Serve", "_per_Serve").replace(" Quantity", "").replace(" ", "_").replace(".", "").replace("-", "_").replace("(", "").replace(")", "")
                parsed_info[f"Nutr_{clean_name}"] = value
        return parsed_info
    except json.JSONDecodeError: logging.debug(f"Minor JSON decode error nutrition: {nutrition_string[:50]}..."); return {}
    except Exception as e: logging.error(f"Nutrition parsing error: {e}"); return {}

# --- Product Scraping Function (Unchanged - Called by Threads) ---
# Note: This function will now be executed concurrently by multiple threads.
# The REQUEST_DELAY_SECONDS applies *within* the pagination loop for a *single* category.
# Overall request rate increases due to parallel execution.
def scrape_products_for_category(session, category_info, is_test_run=False):
    category_id = category_info.get('id');
    # ** Thread Safety Note: If issues arise with shared session, create session here instead **
    # session = requests.Session()
    # session.headers.update(SESSION_HEADERS)

    if not category_id: logging.warning(f"Missing 'id' in {category_info}. Skipping."); return []
    category_name = category_info.get('name', category_id)
    category_url_part = category_info.get('url_friendly_name', category_id)
    category_page_url = f"{BASE_URL}/shop/browse/{category_url_part}"
    # Use category_name/id in log messages for clarity when parallel
    log_prefix = f"Category '{category_name}' (ID: {category_id})"
    logging.info(f"--- Starting {log_prefix} ---")

    page_number = 1
    products_in_category = []
    max_retries = 3
    request_delay = REQUEST_DELAY_SECONDS # Delay between pages *for this category*
    stockcodes_on_previous_page = set()
    calculated_last_page = None

    while True: # Pagination loop
        # *** Check Page Limits FIRST ***
        page_limit_to_use = TEST_RUN_PAGE_LIMIT if is_test_run else MAX_PAGES_PER_CATEGORY
        if page_number > page_limit_to_use:
            if is_test_run:
                 logging.warning(f"--- {log_prefix}: TEST RUN: Page limit ({page_limit_to_use}) reached.")
            else:
                 logging.error(f"--- {log_prefix}: SAFETY STOP: Reached max page limit ({page_limit_to_use}). Check API behavior.")
            break # Exit pagination loop

        retry_count = 0; response = None; made_request = False
        stockcodes_on_current_page = set()

        while retry_count < max_retries: # Retry loop
            logging.info(f"{log_prefix}: Requesting Page {page_number}. Attempt {retry_count+1}/{max_retries}")
            made_request = True; payload = { # Construct payload
                "categoryId": category_id, "pageNumber": page_number, "pageSize": PAGE_SIZE,
                "sortType": "TraderRelevance", "url": f"/shop/browse/{category_url_part}",
                "location": f"/shop/browse/{category_url_part}", "formatObject": json.dumps({"name":category_name}),
                "categoryVersion": "v2", "enableAdReRanking": False, "filters": [],
                "flags": {"EnablePersonalizationCategoryRestriction": True}, "gpBoost": 0, "groupEdmVariants": False,
                "isBundle": False, "isHideUnavailableProducts": False, "isMobile": False,
                "isRegisteredRewardCardPromotion": False, "isSpecial": False, "token": "" }
            current_post_headers = SPECIFIC_POST_HEADERS.copy(); current_post_headers['Referer'] = category_page_url

            try:
                # Using the potentially shared session object passed as an argument
                response = session.post(PRODUCT_API_URL, headers=current_post_headers, json=payload, timeout=POST_TIMEOUT_SECONDS)

                if response.status_code in [500, 502, 503, 504]: logging.warning(f"{log_prefix}: Server error ({response.status_code}) page {page_number}. Retrying..."); retry_count += 1; time.sleep(request_delay * (retry_count + 1)); continue
                response.raise_for_status(); logging.debug(f"{log_prefix}: Received Page {page_number} (Status: {response.status_code}).")
                data = response.json()

                # --- Check for Total Records ---
                if calculated_last_page is None:
                    total_records = data.get('TotalRecordCount')
                    if total_records is None: pagination_info = data.get('Pagination', {}); total_records = pagination_info.get('TotalItems', pagination_info.get('TotalRecordCount')) if isinstance(pagination_info, dict) else None
                    if total_records is not None and isinstance(total_records, int) and total_records >= 0:
                        try: calculated_last_page = max(1, (total_records + PAGE_SIZE - 1) // PAGE_SIZE if PAGE_SIZE > 0 else 1); logging.info(f"{log_prefix}: Found Total Records: {total_records}. Calculated Last Page: {calculated_last_page}")
                        except Exception: logging.warning(f"{log_prefix}: Could not calc last page from total={total_records}"); calculated_last_page = None
                    elif total_records is not None: logging.warning(f"{log_prefix}: Invalid total count value: {total_records}")
                    else: logging.debug(f"{log_prefix}: Total record count metadata not found.")

                # --- Extract Products & Stockcodes ---
                bundles = data.get('Bundles', []); products_on_page_list = []
                if bundles and isinstance(bundles, list):
                    for bundle in bundles:
                        if isinstance(bundle, dict) and bundle.get('Products') and isinstance(bundle['Products'], list):
                            for product in bundle['Products']:
                                if isinstance(product, dict):
                                    products_on_page_list.append(product)
                                    stockcode = product.get('Stockcode')
                                    if stockcode: stockcodes_on_current_page.add(stockcode)
                else: bundles = []

                # --- Stop Condition 1: Empty Page ---
                if not products_on_page_list: logging.info(f"{log_prefix}: No products found page {page_number}. End of category."); return products_in_category # Return collected products

                # --- Stop Condition 2: Duplicate Page ---
                if page_number > 1 and stockcodes_on_current_page and stockcodes_on_current_page == stockcodes_on_previous_page:
                    logging.warning(f"{log_prefix}: Duplicate page {page_number} detected. Stopping category."); return products_in_category # Return collected products

                # --- Process Products ---
                logging.debug(f"{log_prefix}: Found {len(products_on_page_list)} products page {page_number}.")
                for product in products_on_page_list:
                    additional_attrs = product.get('AdditionalAttributes', {})
                    nutrition_string = additional_attrs.get('nutritionalinformation') if additional_attrs else None
                    parsed_nutrition = parse_nutrition(nutrition_string)
                    product_row = {
                        'Stockcode': product.get('Stockcode'),
                        'ProductName': product.get('DisplayName', product.get('Name')),
                        'Brand': product.get('Brand'),
                        'Price': product.get('Price'),
                        'CupString': product.get('CupString'),
                        'PackageSize': product.get('PackageSize'),
                        'ProductURL': f"{BASE_URL}/shop/productdetails/{product.get('Stockcode')}/{product.get('UrlFriendlyName')}" if product.get('Stockcode') and product.get('UrlFriendlyName') else None,
                        'ScrapedCategoryID': category_id,
                        'ScrapedCategoryName': category_name,
                        'ScrapedCategoryParentID': category_info.get('parent_id'),
                        'ScrapedCategoryLevel': category_info.get('level'),
                        'Ingredients': additional_attrs.get('ingredients'),
                        'AllergyStatement': additional_attrs.get('allergystatement'),
                        'AllergenMayBePresent': additional_attrs.get('allergenmaybepresent'),
                        'LifestyleClaim': additional_attrs.get('lifestyleclaim'),
                        'LifestyleAndDietaryStatement': additional_attrs.get('lifestyleanddietarystatement'),
                        'HealthStarRating': additional_attrs.get('healthstarrating'),
                        'ContainsGluten': additional_attrs.get('containsgluten'),
                        'ContainsNuts': additional_attrs.get('containsnuts')
                    }
                    product_row.update(parsed_nutrition); products_in_category.append(product_row)

                # --- Stop Condition 3: Reached Calculated Last Page ---
                if calculated_last_page is not None and page_number >= calculated_last_page:
                    logging.info(f"{log_prefix}: Reached calculated last page ({calculated_last_page}). Stopping category.")
                    break # Exit pagination loop

                break # Successful page processed, break retry loop

            except requests.exceptions.Timeout: logging.warning(f"{log_prefix}: Timeout page {page_number}. Retrying..."); retry_count += 1
            except requests.exceptions.RequestException as e: logging.error(f"{log_prefix}: Request error page {page_number}: {e}. Retrying..."); retry_count += 1
            except json.JSONDecodeError as e: logging.error(f"{log_prefix}: JSON decode error page {page_number}: {e}. Stopping category."); return products_in_category # Return what we have
            except Exception as e: logging.error(f"{log_prefix}: Unexpected error page {page_number}: {e}. Stopping category."); return products_in_category # Return what we have
            if retry_count < max_retries: time.sleep(request_delay * (retry_count + 1))

        if retry_count == max_retries: logging.error(f"{log_prefix}: Max retries page {page_number}. Stopping category."); break
        if response and response.ok and not (retry_count == max_retries):
            if calculated_last_page is None or page_number < calculated_last_page: # Only increment if not at calculated end
                 stockcodes_on_previous_page = stockcodes_on_current_page
                 page_number += 1
                 # Apply delay *between* successful page requests within this category thread
                 time.sleep(request_delay)
            else: break # Break if we just processed the calculated last page
        else: logging.warning(f"{log_prefix}: Exiting pagination due to errors page {page_number}."); break

    # No delay needed here, as the main loop manages processing completed futures
    # if made_request: logging.debug(f"{log_prefix}: Waiting {request_delay}s before next action..."); time.sleep(request_delay)
    logging.info(f"--- Finished {log_prefix}. Found {len(products_in_category)} products. ---")
    return products_in_category # Return list of products for this category


# --- save_data function (Unchanged - Called Sequentially by Main Thread) ---
def save_data(data_list, csv_filename, jsonl_filename, is_first_csv_save):
    if not data_list: logging.info("No new data to save."); return
    logging.info(f"Appending {len(data_list)} products to {csv_filename} and {jsonl_filename}...")
    # CSV
    try:
        df = pd.DataFrame(data_list)
        desired_columns = [
            'Stockcode', 'ProductName', 'Brand', 'Price', 'CupString', 'PackageSize', 'ProductURL',
            'ScrapedCategoryID', 'ScrapedCategoryName', 'ScrapedCategoryParentID', 'ScrapedCategoryLevel',
            'Ingredients', 'AllergyStatement', 'AllergenMayBePresent', 'LifestyleClaim',
            'LifestyleAndDietaryStatement', 'HealthStarRating', 'ContainsGluten', 'ContainsNuts',
            'Nutr_ServingSize', 'Nutr_ServingsPerPack', 'Nutr_Energy_kJ_per_100g', 'Nutr_Energy_kJ_per_Serve',
            'Nutr_Protein_g_per_100g', 'Nutr_Protein_g_per_Serve', 'Nutr_Fat_Total_g_per_100g', 'Nutr_Fat_Total_g_per_Serve',
            'Nutr_Fat_Saturated_g_per_100g', 'Nutr_Fat_Saturated_g_per_Serve', 'Nutr_Carbohydrate_g_per_100g',
            'Nutr_Carbohydrate_g_per_Serve', 'Nutr_Sugars_g_per_100g', 'Nutr_Sugars_g_per_Serve',
            'Nutr_Sodium_mg_per_100g', 'Nutr_Sodium_mg_per_Serve', 'Nutr_Calcium_mg_per_100g', 'Nutr_Calcium_mg_per_Serve',
            'Nutr_Dietary_Fibre_g_per_100g', 'Nutr_Dietary_Fibre_g_per_Serve'
        ]
        all_found_columns = df.columns.tolist(); extra_columns = sorted([col for col in all_found_columns if col not in desired_columns])
        final_column_order = desired_columns + extra_columns; df = df.reindex(columns=final_column_order)
        file_exists = os.path.exists(csv_filename); write_header = not file_exists or is_first_csv_save
        # Ensure mode is 'a' for append, critical for parallel results being saved sequentially
        df.to_csv(csv_filename, mode='a', index=False, header=write_header, encoding='utf-8')
    except Exception as e: logging.error(f"Failed to process/save CSV batch: {e}")
    # JSONL
    try:
        with open(jsonl_filename, 'a', encoding='utf-8') as f_jsonl:
            for product_dict in data_list:
                serializable_dict = {k: v for k, v in product_dict.items() if not pd.isna(v)}
                json_string = json.dumps(serializable_dict, ensure_ascii=False)
                f_jsonl.write(json_string + '\n')
    except TypeError as e: logging.error(f"JSON serialization error: {e}. Skipping JSONL batch.")
    except Exception as e: logging.error(f"Failed to save JSONL batch: {e}")

# --- Main Execution Block (Modified for Parallelism) ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Woolworths products.");
    parser.add_argument('--discover-only', action='store_true', help=f"Discover categories, save to {DISCOVERED_CATEGORIES_CSV}.")
    parser.add_argument('--scrape-from-file', action='store_true', help=f"Scrape all categories listed in {DISCOVERED_CATEGORIES_CSV}.")
    parser.add_argument('--test-run', action='store_true', help=f"Limited test scrape (first {TEST_RUN_CATEGORY_LIMIT} cats, {TEST_RUN_PAGE_LIMIT} pages each, {MAX_WORKERS} workers) using {DISCOVERED_CATEGORIES_CSV}.")
    parser.add_argument('--max-workers', type=int, default=MAX_WORKERS, help=f"Number of parallel workers (default: {MAX_WORKERS}).") # Added max-workers arg
    args = parser.parse_args()

    # Update MAX_WORKERS if provided via command line
    MAX_WORKERS = args.max_workers
    logging.info(f"Using MAX_WORKERS = {MAX_WORKERS}")


    output_dir = 'output'
    if not os.path.exists(output_dir):
        try: os.makedirs(output_dir); logging.info(f"Created directory: {output_dir}")
        except OSError as e: logging.critical(f"Failed create dir '{output_dir}': {e}. Exiting."); exit()

    # Initialize ONE session for potential sharing (see note above)
    session = requests.Session(); session.headers.update(SESSION_HEADERS)
    logging.info("Attempting initial GET to activate session...")
    try:
        init_resp = session.get(BASE_URL, timeout=GET_TIMEOUT_SECONDS); init_resp.raise_for_status();
        logging.info(f"Initial GET OK. Session active."); time.sleep(1)
    except requests.exceptions.RequestException as e: logging.warning(f"Initial GET failed: {e}. Proceeding anyway.")

    if args.discover_only:
        logging.info("=== DISCOVER ONLY MODE ===");
        category_list = get_categories(session) # Pass session here too
        if category_list:
            try: pd.DataFrame(category_list).to_csv(DISCOVERED_CATEGORIES_CSV, index=False, encoding='utf-8'); logging.info(f"Saved categories: {DISCOVERED_CATEGORIES_CSV}")
            except Exception as e: logging.error(f"Save categories CSV error: {e}")
        else: logging.error("Category discovery failed.");
        logging.info("=== Discover Finished ==="); exit()

    elif args.scrape_from_file or args.test_run:
        is_test = args.test_run
        run_mode = "TEST RUN" if is_test else "FULL SCRAPE"
        output_csv_filename = TEST_RUN_OUTPUT_CSV if is_test else FINAL_OUTPUT_CSV
        output_jsonl_filename = TEST_RUN_OUTPUT_JSONL if is_test else FINAL_OUTPUT_JSONL
        logging.info(f"=== {run_mode} using {DISCOVERED_CATEGORIES_CSV} with up to {MAX_WORKERS} workers ===")

        # File existence check and overwrite prompt
        files_to_check = [output_csv_filename, output_jsonl_filename]
        existing_files = [f for f in files_to_check if os.path.exists(f)]
        overwrite_files = False
        if existing_files:
            logging.warning(f"Output file(s) exist: {', '.join(existing_files)}")
            try:
                user_input = input(f"Overwrite existing output files? (y/N): ").lower()
                if user_input == 'y':
                    overwrite_files = True
                    for f in existing_files:
                        try: os.remove(f); logging.info(f"Removed: {f}")
                        except OSError as e: logging.error(f"Could not remove {f}: {e}. Appending instead.")
            except EOFError: # Handle non-interactive environments
                 logging.warning("Non-interactive environment detected. Appending to existing files.")


        # Load categories
        try:
            if not os.path.exists(DISCOVERED_CATEGORIES_CSV): logging.critical(f"{DISCOVERED_CATEGORIES_CSV} missing."); exit()
            df_categories = pd.read_csv(DISCOVERED_CATEGORIES_CSV); category_list = df_categories.to_dict('records')
            if not category_list: logging.warning("Category file empty. Exiting."); exit()
            logging.info(f"Loaded {len(category_list)} categories from {DISCOVERED_CATEGORIES_CSV}.")
        except Exception as e: logging.critical(f"Failed load {DISCOVERED_CATEGORIES_CSV}: {e}"); exit()

        # Apply test run limit if needed
        if is_test:
            limit = TEST_RUN_CATEGORY_LIMIT
            if len(category_list) > limit: logging.warning(f"--- TEST: Limiting to first {limit} categories. ---"); category_list = category_list[:limit]
            else: logging.info(f"--- TEST: Processing all {len(category_list)} loaded categories. ---")

        # --- Parallel Scraping Logic ---
        total_scraped_count = 0
        categories_processed_count = 0
        # Determine if the first write needs a header - crucial before starting threads
        is_first_batch_save = not os.path.exists(output_csv_filename) or overwrite_files

        # Use ThreadPoolExecutor for parallel category scraping
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Store futures keyed by category ID for potential reference (optional)
            # future_to_category = {executor.submit(scrape_products_for_category, session, category, is_test): category['id'] for category in category_list}
            futures = [executor.submit(scrape_products_for_category, session, category, is_test) for category in category_list]
            total_categories = len(futures)
            logging.info(f"Submitted {total_categories} categories to the executor.")

            # Process results as they complete
            for future in concurrent.futures.as_completed(futures):
                categories_processed_count += 1
                try:
                    # Get the result (list of product dicts) from the completed future
                    products_from_cat = future.result()

                    if products_from_cat:
                        # Save data sequentially in the main thread
                        save_data(products_from_cat, output_csv_filename, output_jsonl_filename, is_first_batch_save)
                        # After the first successful save, subsequent saves should not write the header again
                        is_first_batch_save = False # This flag is managed by the main thread
                        total_scraped_count += len(products_from_cat)
                        logging.info(f"Saved {len(products_from_cat)} products. Total scraped: {total_scraped_count}. ({categories_processed_count}/{total_categories} categories completed)")
                    else:
                        # Log even if no products were found for this category
                        logging.info(f"Category finished with no new products. ({categories_processed_count}/{total_categories} categories completed)")

                except Exception as exc:
                    # Log exceptions raised by the scrape_products_for_category function
                    # Attempt to find which category caused it (more complex without the future_to_category mapping)
                    logging.error(f"A category scraping task generated an exception: {exc}", exc_info=True) # Add traceback
                    # You could try to retrieve the category info if you stored it, e.g.:
                    # category_id = future_to_category[future]
                    # logging.error(f'Category ID {category_id} generated an exception: {exc}')


        logging.info("=== Product Scraping Completed ===");
        logging.info(f"Total products saved: {total_scraped_count}")
        if total_scraped_count > 0: logging.info(f"Data saved to {output_csv_filename} and {output_jsonl_filename}")
        else: logging.warning("No products scraped.");
        logging.info(f"=== {run_mode} Finished ===")

    else:
        logging.warning("No mode specified. Use --discover-only, --scrape-from-file, or --test-run.");
        parser.print_help();
        exit()

# --- END OF FILE limit.py ---