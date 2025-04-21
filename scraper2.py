import requests
import json
import logging
import time
import pandas as pd
import os
import argparse # Import argparse for command-line arguments

# --- Basic Logging Setup ---
# (Keep the existing logging setup)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("scraper.log"), # Log to a file
        logging.StreamHandler() # Also log to console
    ]
)

# --- Constants ---
# (Keep existing constants: CATEGORY_API_URL, PRODUCT_API_URL, HEADERS_GET, HEADERS_POST, PAGE_SIZE, REQUEST_DELAY_SECONDS)
CATEGORY_API_URL = "https://www.woolworths.com.au/apis/ui/PiesCategoriesWithSpecials"
PRODUCT_API_URL = "https://www.woolworths.com.au/apis/ui/browse/category"
HEADERS_GET = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36',
    'Accept': 'application/json, text/plain, */*'
}
HEADERS_POST = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Content-Type': 'application/json;charset=UTF-8',
    'Origin': 'https://www.woolworths.com.au',
}
PAGE_SIZE = 36
REQUEST_DELAY_SECONDS = 3
DISCOVERED_CATEGORIES_CSV = 'output/discovered_categories.csv' # Filename for category list
FINAL_OUTPUT_CSV = 'output/woolworths_products_nutrition.csv' # Filename for final product data

# --- Category Discovery Functions (extract_recursive, get_categories) ---
# (Keep the existing functions exactly as they were)
def extract_recursive(category_node, category_list):
    node_id = category_node.get("NodeId")
    description = category_node.get("Description")
    parent_node_id = category_node.get("ParentNodeId")
    node_level = category_node.get("NodeLevel")
    url_friendly_name = category_node.get("UrlFriendlyName")

    is_valid_product_category = node_id and isinstance(node_id, str) and node_id.startswith('1_')

    if is_valid_product_category:
        category_data = {
            "id": node_id,
            "name": description,
            "parent_id": parent_node_id,
            "level": node_level,
            "url_friendly_name": url_friendly_name
        }
        category_list.append(category_data)

    children = category_node.get("Children", [])
    if children:
        for child_node in children:
            extract_recursive(child_node, category_list)

def get_categories():
    all_categories = []
    logging.info(f"Attempting to fetch category structure from {CATEGORY_API_URL}...")
    try:
        response = requests.get(CATEGORY_API_URL, headers=HEADERS_GET, timeout=30)
        response.raise_for_status()
        logging.info(f"Successfully received category data (Status: {response.status_code}).")
        data = response.json()
        logging.info("Successfully parsed category JSON response.")

        if 'Categories' in data and isinstance(data['Categories'], list):
            for top_level_category in data['Categories']:
                extract_recursive(top_level_category, all_categories)
        else:
             logging.warning("Response format unexpected: 'Categories' key not found or not a list.")
             return None

    except requests.exceptions.Timeout:
        logging.error(f"Request timed out while fetching categories from {CATEGORY_API_URL}.")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error occurred while fetching categories: {e}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Failed to decode JSON response from category API: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred during category fetching/parsing: {e}")
        return None

    if not all_categories:
        logging.warning("No valid product categories were extracted.")
    else:
        logging.info(f"Successfully extracted {len(all_categories)} potential product categories.")
    return all_categories

# --- Nutrition Parsing Function (parse_nutrition) ---
# (Keep the existing function exactly as it was)
def parse_nutrition(nutrition_string):
    if not nutrition_string or not isinstance(nutrition_string, str):
        return {}

    try:
        nutrition_data = json.loads(nutrition_string)
        attributes = nutrition_data.get('Attributes', [])
        if not attributes or not isinstance(attributes, list):
            # logging.warning(f"No 'Attributes' list found in nutrition data or format incorrect: {nutrition_string[:100]}")
            return {}

        parsed_info = {}
        for attribute in attributes:
            if not isinstance(attribute, dict): continue

            raw_name = attribute.get("Name")
            value = attribute.get("Value")

            if raw_name and isinstance(raw_name, str) and value is not None:
                clean_name = raw_name.replace(" - Total - NIP", "")
                clean_name = clean_name.replace(" Quantity Per 100g", "_per_100g")
                clean_name = clean_name.replace(" Quantity Per Serve", "_per_Serve")
                clean_name = clean_name.replace(" Quantity", "")
                clean_name = clean_name.replace(" ", "_")
                clean_name = clean_name.replace(".", "")
                clean_name = clean_name.replace("-", "_")
                clean_name = clean_name.replace("(", "")
                clean_name = clean_name.replace(")", "")
                clean_name = f"Nutr_{clean_name}"
                parsed_info[clean_name] = value
        return parsed_info

    except json.JSONDecodeError as e:
        logging.error(f"Error decoding nutritional JSON string: {e}. String snippet: {nutrition_string[:100]}...")
        return {}
    except Exception as e:
        logging.error(f"An unexpected error occurred during nutrition parsing: {e}")
        return {}


# --- Product Scraping Function (scrape_products_for_category) ---
# (Keep the existing function exactly as it was)
def scrape_products_for_category(category_info):
    category_id = category_info['id']
    category_name = category_info.get('name', category_id) # Use name if available
    logging.info(f"--- Starting scrape for Category: '{category_name}' (ID: {category_id}) ---")

    page_number = 1
    products_in_category = []
    max_retries = 3
    request_delay = REQUEST_DELAY_SECONDS # Use the constant

    while True:
        retry_count = 0
        response = None

        while retry_count < max_retries:
            logging.info(f"Requesting Page {page_number} for Category '{category_name}' (ID: {category_id})...")
            payload = {
                "categoryId": category_id,
                "pageNumber": page_number,
                "pageSize": PAGE_SIZE,
                "sortType": "TraderRelevance",
                "url": f"/shop/browse/{category_info.get('url_friendly_name', category_id)}",
                "location": f"/shop/browse/{category_info.get('url_friendly_name', category_id)}",
                "categoryVersion": "v2", "enableAdReRanking": False, "filters": [],
                "flags": {"EnablePersonalizationCategoryRestriction": True}, "formatObject": "{}",
                "gpBoost": 0, "groupEdmVariants": False, "isBundle": False, "isHideUnavailableProducts": False,
                "isMobile": False, "isRegisteredRewardCardPromotion": False, "isSpecial": False, "token": ""
            }

            try:
                response = requests.post(PRODUCT_API_URL, headers=HEADERS_POST, json=payload, timeout=45)

                if response.status_code in [500, 502, 503, 504]:
                    logging.warning(f"Server error ({response.status_code}) on page {page_number} for category {category_id}. Retrying ({retry_count+1}/{max_retries})...")
                    retry_count += 1
                    time.sleep(request_delay * (retry_count + 1))
                    continue

                response.raise_for_status()
                logging.info(f"Successfully received Page {page_number} data (Status: {response.status_code}).")
                data = response.json()
                bundles = data.get('Bundles', [])
                products_on_page = []
                if bundles and isinstance(bundles, list):
                    for bundle in bundles:
                         if isinstance(bundle, dict) and bundle.get('Products') and isinstance(bundle['Products'], list):
                             for product in bundle['Products']:
                                 if isinstance(product, dict):
                                     products_on_page.append(product)
                else:
                     logging.warning(f"'Bundles' key missing or not a list on page {page_number} for category {category_id}. Assuming end of pages.")
                     bundles = []

                if not products_on_page:
                    logging.info(f"No products found on Page {page_number} for Category '{category_name}'. End of category.")
                    time.sleep(request_delay)
                    return products_in_category

                logging.info(f"Found {len(products_on_page)} products on Page {page_number}.")
                for product in products_on_page:
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
                        'ProductURL': f"https://www.woolworths.com.au/shop/productdetails/{product.get('Stockcode')}/{product.get('UrlFriendlyName')}" if product.get('Stockcode') and product.get('UrlFriendlyName') else None,
                        'ScrapedCategoryID': category_id,
                        'ScrapedCategoryName': category_name, # Add current category name
                        'ScrapedCategoryParentID': category_info.get('parent_id'),
                        'ScrapedCategoryLevel': category_info.get('level')
                    }
                    product_row.update(parsed_nutrition)
                    products_in_category.append(product_row)
                break # Exit retry loop

            except requests.exceptions.Timeout:
                logging.warning(f"Request timed out on page {page_number} for category {category_id}. Retrying ({retry_count+1}/{max_retries})...")
                retry_count += 1
            except requests.exceptions.RequestException as e:
                logging.error(f"Request error on page {page_number} for category {category_id}: {e}. Retrying ({retry_count+1}/{max_retries})...")
                retry_count += 1
            except json.JSONDecodeError as e:
                logging.error(f"Failed to decode JSON on page {page_number} for category {category_id}: {e}. Stopping category.")
                if response and response.text: logging.debug(f"Problematic response text snippet: {response.text[:500]}")
                time.sleep(request_delay)
                return products_in_category
            except Exception as e:
                 logging.error(f"An unexpected error occurred processing page {page_number} for category {category_id}: {e}. Stopping category.")
                 time.sleep(request_delay)
                 return products_in_category

            if retry_count < max_retries: time.sleep(request_delay * (retry_count + 1))

        if retry_count == max_retries:
             logging.error(f"Max retries reached for page {page_number} of category {category_id}. Stopping scrape for this category.")
             time.sleep(request_delay)
             return products_in_category

        page_number += 1
        logging.debug(f"Waiting {request_delay} seconds before next request...")
        time.sleep(request_delay)


# --- Main Execution Block with Argument Parsing ---
if __name__ == "__main__":
    # --- Setup Argument Parser ---
    parser = argparse.ArgumentParser(description="Scrape Woolworths products and nutritional information.")
    parser.add_argument(
        '--discover-only',
        action='store_true', # Makes it a boolean flag
        help=f"Run category discovery only and save the list to {DISCOVERED_CATEGORIES_CSV}. Does not scrape products."
    )
    parser.add_argument(
        '--scrape-from-file',
        action='store_true',
        help=f"Scrape products using the category list found in {DISCOVERED_CATEGORIES_CSV}. Assumes the file exists from a previous --discover-only run."
    )
    args = parser.parse_args()

    # --- Ensure output directory exists ---
    output_dir = 'output'
    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            logging.info(f"Created output directory: {output_dir}")
        except OSError as e:
            logging.critical(f"Failed to create output directory '{output_dir}': {e}. Exiting.")
            exit()

    # --- Execute Based on Mode ---
    if args.discover_only:
        logging.info("========== Running in DISCOVER ONLY mode ==========")
        category_list = get_categories()
        if category_list:
            try:
                df_cat = pd.DataFrame(category_list)
                df_cat.to_csv(DISCOVERED_CATEGORIES_CSV, index=False, encoding='utf-8')
                logging.info(f"Successfully saved discovered categories to: {DISCOVERED_CATEGORIES_CSV}")
                logging.info("Please review this file. You can remove rows for categories you DO NOT want to scrape.")
                logging.info(f"To scrape the reviewed categories, run the script again with the --scrape-from-file argument.")
            except Exception as e:
                logging.error(f"Failed to save categories to CSV: {e}")
        else:
            logging.error("Category discovery failed. No CSV file created.")
        logging.info("========== Discover Only Mode Finished ==========")
        exit() # Stop after discovery

    elif args.scrape_from_file:
        logging.info(f"========== Running in SCRAPE FROM FILE mode using {DISCOVERED_CATEGORIES_CSV} ==========")
        # --- Load Categories from CSV ---
        try:
            if not os.path.exists(DISCOVERED_CATEGORIES_CSV):
                logging.critical(f"Category file not found: {DISCOVERED_CATEGORIES_CSV}")
                logging.critical("Please run the script with --discover-only first.")
                exit()

            df_categories_to_scrape = pd.read_csv(DISCOVERED_CATEGORIES_CSV)
            # Convert DataFrame rows back to list of dictionaries expected by the scraper function
            category_list = df_categories_to_scrape.to_dict('records')
            logging.info(f"Loaded {len(category_list)} categories to scrape from {DISCOVERED_CATEGORIES_CSV}.")
            if not category_list:
                 logging.warning("Category file loaded, but it contains no categories. Exiting.")
                 exit()

        except Exception as e:
            logging.critical(f"Failed to load or parse categories from {DISCOVERED_CATEGORIES_CSV}: {e}")
            exit()

        # --- Proceed with Product Scraping ---
        all_scraped_products = []
        total_categories = len(category_list)
        logging.info(f"Beginning product scraping for {total_categories} categories...")

        for i, category in enumerate(category_list):
            logging.info(f"--- Progress: Processing Category {i+1} / {total_categories} ---")
            # Ensure category dictionary has 'id' key
            if 'id' not in category:
                 logging.warning(f"Skipping category at index {i} due to missing 'id' key in loaded data: {category}")
                 continue
            products_from_cat = scrape_products_for_category(category) # Pass the dict directly
            if products_from_cat:
                all_scraped_products.extend(products_from_cat)
                logging.info(f"Finished category '{category.get('name', category['id'])}'. Found {len(products_from_cat)} products. Total products so far: {len(all_scraped_products)}")
            else:
                logging.info(f"Finished category '{category.get('name', category['id'])}'. No products found or errors occurred.")
            # Consider adding incremental saving here if desired

        logging.info("========== Product Scraping Completed ==========")
        logging.info(f"Total products scraped across all categories: {len(all_scraped_products)}")

        # --- Phase 5: Data Storage ---
        if all_scraped_products:
            logging.info("Preparing data for saving...")
            try:
                df = pd.DataFrame(all_scraped_products)
                desired_columns = [
                    'Stockcode', 'ProductName', 'Brand', 'Price', 'CupString', 'PackageSize', 'ProductURL',
                    'ScrapedCategoryID', 'ScrapedCategoryName', 'ScrapedCategoryParentID', 'ScrapedCategoryLevel',
                    'Nutr_ServingSize', 'Nutr_ServingsPerPack', 'Nutr_Energy_kJ_per_100g', 'Nutr_Energy_kJ_per_Serve',
                    'Nutr_Protein_g_per_100g', 'Nutr_Protein_g_per_Serve', 'Nutr_Fat_Total_g_per_100g', 'Nutr_Fat_Total_g_per_Serve',
                    'Nutr_Fat_Saturated_g_per_100g', 'Nutr_Fat_Saturated_g_per_Serve', 'Nutr_Carbohydrate_g_per_100g',
                    'Nutr_Carbohydrate_g_per_Serve', 'Nutr_Sugars_g_per_100g', 'Nutr_Sugars_g_per_Serve',
                    'Nutr_Sodium_mg_per_100g', 'Nutr_Sodium_mg_per_Serve', 'Nutr_Calcium_mg_per_100g', 'Nutr_Calcium_mg_per_Serve',
                    'Nutr_Dietary_Fibre_g_per_100g', 'Nutr_Dietary_Fibre_g_per_Serve' # Add other common ones if expected
                ]
                all_found_columns = df.columns.tolist()
                final_column_order = desired_columns + [col for col in all_found_columns if col not in desired_columns]
                df = df.reindex(columns=final_column_order)

                df.to_csv(FINAL_OUTPUT_CSV, index=False, encoding='utf-8')
                logging.info(f"Successfully saved all data to {FINAL_OUTPUT_CSV}")

            except Exception as e:
                logging.error(f"Failed to process DataFrame or save to CSV: {e}")
        else:
            logging.warning("No products were scraped. CSV file not created.")

        logging.info("========== Scrape From File Mode Finished ==========")

    else:
        # --- No valid mode selected ---
        logging.warning("No mode specified. Please run with --discover-only or --scrape-from-file.")
        parser.print_help() # Show usage instructions
        exit()