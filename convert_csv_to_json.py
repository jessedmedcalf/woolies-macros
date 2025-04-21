import csv
import json

csv_file = 'output/unique_products_with_categories_saved.csv'
json_file = 'output/unique_products_with_categories_saved.json'

data = []
with open(csv_file, encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        data.append(row)

with open(json_file, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
