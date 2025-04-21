# --- app.py ---
from flask import Flask, render_template, jsonify, request
import pandas as pd
import json
import os
import logging
import math

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration ---
UNIQUE_PRODUCTS_JSON = 'output/unique_products_with_categories_saved.json'
CATEGORY_MAPPING_CSV = 'output/category_stockcode_mapping_saved.csv'

# --- Initialize Flask App ---
app = Flask(__name__)

# --- Data Loading and Preprocessing ---
unique_products_df = pd.DataFrame()
category_map_df = pd.DataFrame()
category_hierarchy = {}
all_dietary_tags = set()

def build_category_hierarchy(df_map):
    """Builds a nested dictionary representing the category hierarchy."""
    hierarchy = {}
    # Ensure IDs and levels are strings for consistent lookup, handle missing or malformed values
    df_map['ScrapedCategoryID'] = df_map['ScrapedCategoryID'].astype(str)
    df_map['ScrapedCategoryParentID'] = df_map['ScrapedCategoryParentID'].astype(str)
    df_map['ScrapedCategoryLevel'] = df_map['ScrapedCategoryLevel'].astype(str)

    # Create a dictionary of all categories for quick lookup
    categories = {}
    for _, row in df_map[['ScrapedCategoryID', 'ScrapedCategoryName', 'ScrapedCategoryParentID', 'ScrapedCategoryLevel']].drop_duplicates().iterrows():
        cat_id = str(row['ScrapedCategoryID'])
        parent_id = str(row['ScrapedCategoryParentID']) if pd.notnull(row['ScrapedCategoryParentID']) else ''
        level = str(row['ScrapedCategoryLevel']) if pd.notnull(row['ScrapedCategoryLevel']) else ''
        # Defensive: if level is not a digit, treat as unknown
        try:
            level_int = int(level)
        except Exception:
            level_int = None
        categories[cat_id] = {
            'id': cat_id,
            'name': row['ScrapedCategoryName'],
            'parent_id': parent_id,
            'level': level_int,
            'children': {} # Initialize children dictionary
        }

    # Build the hierarchy
    for cat_id, category in categories.items():
        parent_id = category['parent_id']
        if parent_id in categories:
            categories[parent_id]['children'][cat_id] = category
        elif category['level'] == 1 or category['level'] == '1':
            hierarchy[cat_id] = category
        else:
            logging.warning(f"Category {cat_id} has parent {parent_id} which was not found, or is not a level 1 category.")

    def children_to_list(node):
        if 'children' in node and isinstance(node['children'], dict):
            # Recursively sort children dict by name, but keep as dict
            node['children'] = dict(sorted(node['children'].items(), key=lambda item: item[1]['name']))
            for child in node['children'].values():
                children_to_list(child)

    for root_node in hierarchy.values():
        children_to_list(root_node)

    sorted_hierarchy = dict(sorted(hierarchy.items(), key=lambda item: item[1]['name']))
    return sorted_hierarchy


def load_and_prepare_data():
    global unique_products_df, category_map_df, category_hierarchy, all_dietary_tags
    logging.info("Loading data...")
    try:
        # Load unique products from JSON
        with open(UNIQUE_PRODUCTS_JSON, encoding='utf-8') as f:
            unique_products = json.load(f)
        unique_products_df = pd.DataFrame(unique_products)
        logging.info(f"Loaded {len(unique_products_df)} unique product rows from {UNIQUE_PRODUCTS_JSON}")

        # Load category mapping
        category_map_df = pd.read_csv(CATEGORY_MAPPING_CSV, dtype={'Stockcode': str, 'ScrapedCategoryID': str}, low_memory=False)
        logging.info(f"Loaded {len(category_map_df)} category mapping rows from {CATEGORY_MAPPING_CSV}")

        # --- Data Cleaning & Feature Engineering ---
        # Calculate Protein per Gram (handle division by zero or NaN)
        # Need to parse PackageSize first if it contains units like 'g' or 'kg'
        def get_grams(size_str):
            if pd.isna(size_str) or not isinstance(size_str, str):
                return None
            size_str = size_str.lower().replace(' ', '')
            num_part = ''.join(filter(lambda x: x.isdigit() or x == '.', size_str))
            try:
                num = float(num_part)
                if 'kg' in size_str:
                    return num * 1000
                elif 'g' in size_str:
                    return num
                # Add other units like ml if needed, assuming density ~1 for simplicity
                elif 'ml' in size_str:
                     return num
                elif 'l' in size_str:
                     return num * 1000
                else:
                    return None # Cannot determine grams
            except ValueError:
                return None

        # Convert nutritional columns to numeric, coercing errors
        nutr_cols = ['Nutr_Protein_per_100g', 'Nutr_Protein_per_Serve', 'Nutr_Serving_Size', 'Nutr_Sugars_per_100g']
        for col in nutr_cols:
            if col in unique_products_df.columns:
                 # Attempt to clean strings like '< 1g' before converting
                 unique_products_df[col] = unique_products_df[col].astype(str).str.replace(r'[^\d.]', '', regex=True)
                 unique_products_df[col] = pd.to_numeric(unique_products_df[col], errors='coerce')

        # Attempt calculation (Protein per 100g / 100)
        if 'Nutr_Protein_per_100g' in unique_products_df.columns:
            unique_products_df['Protein_per_g'] = unique_products_df['Nutr_Protein_per_100g'] / 100.0
            unique_products_df['Protein_per_g'] = unique_products_df['Protein_per_g'].round(4) # Round for clarity
        else:
            logging.warning("Column 'Nutr_Protein_per_100g' not found. Cannot calculate Protein_per_g.")
            unique_products_df['Protein_per_g'] = float('nan')

        # Ensure Sugar per 100g is numeric
        if 'Nutr_Sugars_per_100g' in unique_products_df.columns:
            unique_products_df['Sugar_per_100g'] = pd.to_numeric(unique_products_df['Nutr_Sugars_per_100g'], errors='coerce')
        else:
            logging.warning("Column 'Nutr_Sugars_per_100g' not found.")
            unique_products_df['Sugar_per_100g'] = float('nan')

        # Guarantee calculated fields always exist
        if 'Protein_per_g' not in unique_products_df.columns:
            unique_products_df['Protein_per_g'] = float('nan')
        if 'Sugar_per_100g' not in unique_products_df.columns:
            unique_products_df['Sugar_per_100g'] = float('nan')

        # --- Extract Dietary Tags ---
        dietary_col = 'LifestyleAndDietaryStatement' # Or 'AllergyStatement' etc.
        if dietary_col in unique_products_df.columns:
            unique_products_df[dietary_col] = unique_products_df[dietary_col].fillna('').astype(str) # Handle NaN
            # Split comma-separated tags and flatten the list
            tags_list = unique_products_df[dietary_col].str.split(',').explode()
            # Clean whitespace and convert to lowercase, get unique tags
            all_dietary_tags = set(tags_list.str.strip().str.lower().unique())
            all_dietary_tags.discard('') # Remove empty string tag if present
            logging.info(f"Found dietary tags: {sorted(list(all_dietary_tags))}")
        else:
             logging.warning(f"Column '{dietary_col}' not found for dietary filtering.")
             all_dietary_tags = set()


        # --- Build Category Hierarchy ---
        logging.info("Building category hierarchy...")
        if not category_map_df.empty:
            category_hierarchy = build_category_hierarchy(category_map_df)
            logging.info("Category hierarchy built.")
        else:
            logging.error("Category mapping data is empty. Cannot build hierarchy.")

        logging.info("Data loading and preparation complete.")

    except FileNotFoundError as e:
        logging.error(f"Error loading data file: {e}. Ensure JSON and CSV files are in the 'output' directory.")
        # Optionally exit or provide default empty data
    except Exception as e:
        logging.error(f"An error occurred during data loading/preprocessing: {e}", exc_info=True)

# --- Load data on startup ---
load_and_prepare_data()

# --- Routes ---
@app.route('/')
def index():
    """Serves the main HTML page."""
    # Pass the category hierarchy and available dietary tags to the template
    return render_template('index.html',
                           category_hierarchy=category_hierarchy,
                           dietary_tags=sorted(list(all_dietary_tags)))

@app.route('/api/products/<category_id>')
def get_products_by_category(category_id):
    """API endpoint to get product data for visualization."""
    logging.info(f"API request for category ID: {category_id}")
    dietary_filter = request.args.get('dietary', None) # Get filter from query param ?dietary=vegan
    if dietary_filter:
        dietary_filter = dietary_filter.lower().strip()
        logging.info(f"Applying dietary filter: {dietary_filter}")

    products_data = []
    try:
        # Find stockcodes for the given category_id using the mapping
        if category_map_df.empty or 'ScrapedCategoryID' not in category_map_df.columns:
             logging.warning("Category map DataFrame is empty or missing required column.")
             return jsonify([])

        # Ensure consistent type for matching
        relevant_stockcodes = category_map_df[category_map_df['ScrapedCategoryID'] == str(category_id)]['Stockcode'].unique()
        logging.info(f"Found {len(relevant_stockcodes)} stockcodes for category {category_id}.")

        if len(relevant_stockcodes) > 0 and not unique_products_df.empty:
            # Filter the unique products dataframe
            category_products_df = unique_products_df[unique_products_df['Stockcode'].isin(relevant_stockcodes)].copy()
            logging.info(f"Initial product count for category: {len(category_products_df)}")

            # Apply dietary filter if provided
            if dietary_filter:
                 dietary_col = 'LifestyleAndDietaryStatement' # Use the same column as defined in load_data
                 if dietary_col in category_products_df.columns:
                     # Case-insensitive check if the tag exists in the statement string
                     category_products_df = category_products_df[
                         category_products_df[dietary_col].str.lower().str.contains(dietary_filter, na=False)
                     ]
                     logging.info(f"Product count after '{dietary_filter}' filter: {len(category_products_df)}")
                 else:
                      logging.warning(f"Dietary filter column '{dietary_col}' not found in product data. Filter ignored.")

            # Select and prepare data for the chart
            chart_data_df = category_products_df[[
                'Stockcode',
                'ProductName',
                'Protein_per_g',
                'Sugar_per_100g'
            ]].copy()

            # Drop rows where essential chart data is missing
            chart_data_df.dropna(subset=['Protein_per_g', 'Sugar_per_100g'], inplace=True)
            logging.info(f"Product count after dropping NA for chart values: {len(chart_data_df)}")

            # Convert to list of dictionaries for JSON response
            products_data = chart_data_df.to_dict('records')

        else:
             logging.info(f"No relevant stockcodes found or unique products dataframe is empty.")


    except KeyError as e:
        logging.error(f"KeyError accessing DataFrame column: {e}. Check CSV headers and code consistency.")
    except Exception as e:
        logging.error(f"Error processing API request for category {category_id}: {e}", exc_info=True)

    logging.info(f"Returning {len(products_data)} products for category {category_id} (filter: {dietary_filter})")
    return jsonify(products_data)

# --- Helper Function for Template ---
@app.template_filter('render_categories')
def render_categories_filter(hierarchy_dict):
    """Recursive function to generate HTML list for categories."""
    html = '<ul>'
    for cat_id, category in hierarchy_dict.items():
        # Use data attributes to store ID and name easily
        html += f'<li><span class="category-item" data-id="{category["id"]}" data-name="{category["name"]}">{category["name"]}</span>'
        if category.get('children'):
            # Recursively render children
            html += render_categories_filter(category['children']) # Ensure children are sorted
        html += '</li>'
    html += '</ul>'
    return html


if __name__ == '__main__':
    app.run(debug=True, port=5001) # Turn off debug mode for production