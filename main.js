// Utility to fetch JSON files
async function fetchJSON(path) {
  const response = await fetch(path);
  return await response.json();
}

// Build category tree from product_to_categories_mapping.json
async function buildCategoryTree() {
  const mapping = await fetchJSON('output/product_to_categories_mapping.json');
  const catMap = {};
  const catTree = {};
  // Flatten all categories
  Object.values(mapping).forEach(catArr => {
    catArr.forEach(cat => {
      catMap[cat.ScrapedCategoryID] = cat;
      cat.children = cat.children || [];
    });
  });
  // Build parent-child relationships
  Object.values(catMap).forEach(cat => {
    if (cat.ScrapedCategoryParentID && catMap[cat.ScrapedCategoryParentID]) {
      catMap[cat.ScrapedCategoryParentID].children.push(cat);
    } else {
      catTree[cat.ScrapedCategoryID] = cat;
    }
  });
  return catTree;
}

function renderCategorySelector(tree, container, onSelect) {
  container.innerHTML = '';
  let path = [];
  let currentNode = null;

  function getChildren(node) {
    return node && node.children ? node.children : Object.values(tree);
  }

  function renderBreadcrumb() {
    const crumb = document.createElement('div');
    crumb.className = 'cat-breadcrumb';
    // Reset button (to root)
    if (path.length > 0) {
      const resetBtn = document.createElement('button');
      resetBtn.textContent = 'All';
      resetBtn.className = 'cat-reset-btn';
      resetBtn.onclick = () => {
        path = [];
        currentNode = null;
        renderSelector();
        onSelect(null);
        updateView();
      };
      crumb.appendChild(resetBtn);
      crumb.appendChild(document.createTextNode(' > '));
    }
    path.forEach((node, idx) => {
      const span = document.createElement('span');
      span.textContent = node.ScrapedCategoryName;
      span.className = 'cat-crumb';
      if (idx < path.length - 1) {
        span.style.cursor = 'pointer';
        span.onclick = () => {
          path = path.slice(0, idx + 1);
          currentNode = path[path.length - 1] || null;
          renderSelector();
          onSelect(currentNode);
          updateView();
        };
      } else {
        span.style.fontWeight = 'bold';
        span.style.textDecoration = 'underline dotted #bbb';
      }
      crumb.appendChild(span);
      if (idx < path.length - 1) {
        crumb.appendChild(document.createTextNode(' > '));
      }
    });

    return crumb;
  }

  function renderSelector() {
    container.innerHTML = '';
    if (path.length > 0) {
      container.appendChild(renderBreadcrumb());
    }
    const children = getChildren(currentNode);
    if (!children.length) return;
    const label = document.createElement('label');
    label.textContent = `Category Level ${path.length + 1}`;
    label.style.display = 'block';
    label.style.fontWeight = 'bold';
    label.style.marginTop = path.length > 0 ? '8px' : '0';
    const select = document.createElement('select');
    select.innerHTML = '<option value="">Select...</option>';
    children.forEach(child => {
      const option = document.createElement('option');
      option.value = child.ScrapedCategoryID;
      option.textContent = child.ScrapedCategoryName;
      select.appendChild(option);
    });
    select.onchange = () => {
      const selectedID = select.value;
      const selectedNode = children.find(c => c.ScrapedCategoryID === selectedID);
      if (selectedNode) {
        path.push(selectedNode);
        currentNode = selectedNode;
        renderSelector();
        onSelect(selectedNode);
        updateView();
      }
    };
    container.appendChild(label);
    container.appendChild(select);
  }

  renderSelector();
}

// Dietary toggles
const dietaryOptions = [
  { key: 'Vegetarian', label: 'Vegetarian' },
  { key: 'Vegan', label: 'Vegan' },
  { key: 'Gluten Free', label: 'Gluten Free' },
  { key: 'Nut Free', label: 'Nut Free' },
];

function renderDietaryToggles(container, onChange) {
  container.innerHTML = '';
  dietaryOptions.forEach(opt => {
    const label = document.createElement('label');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = opt.key;
    checkbox.onchange = onChange;
    label.appendChild(checkbox);
    label.appendChild(document.createTextNode(' ' + opt.label));
    container.appendChild(label);
  });
}

// Filter products by category and dietary toggles
function filterProducts(products, mapping, selectedCat, dietaryFilters) {
  let filteredProducts = products;
  if (selectedCat) {
    // Find all stockcodes for the selected category (direct or descendant)
    const relevantStockcodes = Object.entries(mapping)
      .filter(([stockcode, cats]) => cats.some(cat => cat.ScrapedCategoryID === selectedCat.ScrapedCategoryID))
      .map(([stockcode]) => stockcode);
    filteredProducts = filteredProducts.filter(prod => relevantStockcodes.includes(prod.Stockcode));
  }
  // Apply dietary filters
  return filteredProducts.filter(prod => {
    if (dietaryFilters.includes('Vegetarian') && !(prod.LifestyleAndDietaryStatement || '').toLowerCase().includes('vegetarian')) return false;
    if (dietaryFilters.includes('Vegan') && !(prod.LifestyleAndDietaryStatement || '').toLowerCase().includes('vegan')) return false;
    if (dietaryFilters.includes('Gluten Free') && !(prod.AllergyStatement || '').toLowerCase().includes('gluten free')) return false;
    if (dietaryFilters.includes('Nut Free') && (prod.ContainsNuts || '').toLowerCase() === 'true') return false;
    return true;
  });
}

function renderVisualization(container, products, xField, yField, sizeField, fieldMeta, reverseX, reverseY, reverseSize) {
  container.innerHTML = '';
  if (!products.length) {
    container.textContent = 'No products found for this category and filters.';
    return;
  }
  const width = 600, height = 600, pad = 45;
  // Find axis max/min
  const maxX = Math.max(...products.map(p => parseFloat(p[xField]) || 0), 20);
  const maxY = Math.max(...products.map(p => parseFloat(p[yField]) || 0), 20);
  // Matrix container
  const matrix = document.createElement('div');
  matrix.className = 'matrix-container';
  matrix.style.width = width + 'px';
  matrix.style.height = height + 'px';
  // Axes
  const axes = document.createElement('div');
  axes.className = 'matrix-axes';
  const xAxis = document.createElement('div');
  xAxis.className = 'matrix-x-axis';
  axes.appendChild(xAxis);
  const yAxis = document.createElement('div');
  yAxis.className = 'matrix-y-axis';
  axes.appendChild(yAxis);
  matrix.appendChild(axes);
  // X axis label
  const xLabelDiv = document.createElement('div');
  xLabelDiv.className = 'matrix-axis-header' + (reverseX ? ' reversed' : '');
  xLabelDiv.textContent = fieldMeta[xField]?.label || xField;
  xLabelDiv.style.left = width / 2 + 'px';
  xLabelDiv.style.bottom = (pad - 16) + 'px';
  xLabelDiv.style.transform = 'translateX(-50%)';
  matrix.appendChild(xLabelDiv);

  // Y axis label
  const yLabelDiv = document.createElement('div');
  yLabelDiv.className = 'matrix-axis-header' + (reverseY ? ' reversed' : '');
  yLabelDiv.textContent = fieldMeta[yField]?.label || yField;
  yLabelDiv.style.top = height / 2 + 'px';
  yLabelDiv.style.left = (pad - 16) + 'px';
  yLabelDiv.style.transform = 'translateY(-50%) rotate(-90deg)';
  matrix.appendChild(yLabelDiv);
  // Tick labels (0 and max)
  const x0 = document.createElement('div');
  x0.className = 'matrix-tick-label';
  x0.style.left = (pad-10) + 'px';
  x0.style.top = (height - pad + 4) + 'px';
  x0.textContent = '0';
  matrix.appendChild(x0);
  const xMax = document.createElement('div');
  xMax.className = 'matrix-tick-label';
  xMax.style.left = (width - pad - 8) + 'px';
  xMax.style.top = (height - pad + 4) + 'px';
  xMax.textContent = maxX.toFixed(1);
  matrix.appendChild(xMax);
  const y0 = document.createElement('div');
  y0.className = 'matrix-tick-label';
  y0.style.left = (pad - 28) + 'px';
  y0.style.top = (height - pad - 10) + 'px';
  y0.textContent = '0';
  matrix.appendChild(y0);
  const yMax = document.createElement('div');
  yMax.className = 'matrix-tick-label';
  yMax.style.left = (pad - 32) + 'px';
  yMax.style.top = (pad - 10) + 'px';
  yMax.textContent = maxY.toFixed(1);
  matrix.appendChild(yMax);
  // Tooltip
  const tooltip = document.createElement('div');
  tooltip.className = 'matrix-tooltip';
  matrix.appendChild(tooltip);
  // Plot dots
  // Determine min/max for size field
  let minSize = 8, maxSize = 32;
  let minVal = Math.min(...products.map(p => parseFloat(p[sizeField]) || 0));
  let maxVal = Math.max(...products.map(p => parseFloat(p[sizeField]) || 0));
  if (minVal === maxVal) { minVal = 0; maxVal = minVal + 1; }
  products.forEach(prod => {
    let xVal = parseFloat(prod[xField]) || 0;
    let yVal = parseFloat(prod[yField]) || 0;
    let sizeVal = parseFloat(prod[sizeField]) || 0;
    const x = pad + ((reverseX ? (maxX - xVal) : xVal) / maxX) * (width - 2*pad);
    const y = height - pad - ((reverseY ? (maxY - yVal) : yVal) / maxY) * (height - 2*pad);
    let rawSize = sizeVal;
    let sizeNorm = (rawSize - minVal) / (maxVal - minVal);
    if (reverseSize) sizeNorm = 1 - sizeNorm;
    let size = isNaN(sizeNorm) ? minSize : minSize + sizeNorm * (maxSize - minSize);
    size = Math.max(minSize, Math.min(maxSize, size));
    const dot = document.createElement('div');
    dot.className = 'matrix-dot';
    dot.style.left = `${x}px`;
    dot.style.top = `${y}px`;
    dot.style.width = `${size}px`;
    dot.style.height = `${size}px`;

    // Add click to open Woolworths product link
    dot.onclick = (e) => {
      e.stopPropagation();
      let url = prod.WoolworthsUrl;
      if (!url && prod.Stockcode) {
        url = `https://www.woolworths.com.au/shop/productdetails/${prod.Stockcode}`;
      }
      if (url) {
        window.open(url, '_blank', 'noopener');
      }
    };
    dot.onmouseenter = () => {
      tooltip.innerHTML = `<b>${prod.ProductName}</b><br>${fieldMeta[xField]?.label || xField}: ${prod[xField] || 0}<br>${fieldMeta[yField]?.label || yField}: ${prod[yField] || 0}<br>${fieldMeta[sizeField]?.label || sizeField}: ${prod[sizeField] || 0}`;
      tooltip.style.display = 'block';
      const matrixRect = matrix.getBoundingClientRect();
      const dotRect = dot.getBoundingClientRect();
      let left = dotRect.right - matrixRect.left + 8;
      let top = dotRect.top - matrixRect.top - 4;
      const tooltipRect = tooltip.getBoundingClientRect();
      const matrixWidth = matrixRect.width;
      const matrixHeight = matrixRect.height;
      if (left + tooltipRect.width > matrixWidth) {
        left = dotRect.left - matrixRect.left - tooltipRect.width - 8;
      }
      if (top + tooltipRect.height > matrixHeight) {
        top = matrixHeight - tooltipRect.height - 8;
      }
      if (top < 0) top = 4;
      if (left < 0) left = 4;
      tooltip.style.left = left + 'px';
      tooltip.style.top = top + 'px';
    };
    dot.onmouseleave = () => { tooltip.style.display = 'none'; };
    matrix.appendChild(dot);
  });
  container.appendChild(matrix);
}

async function main() {
  const [products, mapping, catTree] = await Promise.all([
    fetchJSON('output/unique_products_with_categories_saved.json'),
    fetchJSON('output/product_to_categories_mapping.json'),
    buildCategoryTree()
  ]);

  // --- CATEGORY AUTOCOMPLETE ---
  // Flatten all categories from mapping
  const allCategories = [];
  const seenCatIDs = new Set();
  Object.values(mapping).forEach(catArr => {
    catArr.forEach(cat => {
      if (!seenCatIDs.has(cat.ScrapedCategoryID)) {
        allCategories.push({
          id: cat.ScrapedCategoryID,
          name: cat.ScrapedCategoryName,
          parent: cat.ScrapedCategoryParentID,
          level: cat.ScrapedCategoryLevel
        });
        seenCatIDs.add(cat.ScrapedCategoryID);
      }
    });
  });
  const searchInput = document.getElementById('category-search');
  const suggestionsBox = document.getElementById('category-suggestions');
  let suggestions = [];
  let selectedSuggestionIdx = -1;
  searchInput.addEventListener('input', function() {
    const val = this.value.trim().toLowerCase();
    if (!val) {
      suggestionsBox.style.display = 'none';
      return;
    }
    suggestions = allCategories.filter(cat => cat.name.toLowerCase().includes(val));
    suggestionsBox.innerHTML = '';
    suggestions.slice(0, 12).forEach((cat, i) => {
      const div = document.createElement('div');
      div.className = 'suggestion' + (i === selectedSuggestionIdx ? ' active' : '');
      div.textContent = cat.name + (cat.level ? ` (Level ${cat.level})` : '');
      div.onmousedown = (e) => {
        e.preventDefault(); // Prevent input blur before click
        searchInput.value = cat.name;
        suggestionsBox.style.display = 'none';
        selectedCat = { ScrapedCategoryID: cat.id, ScrapedCategoryName: cat.name };
        selectedSuggestionIdx = -1;
        updateView();
      };
      suggestionsBox.appendChild(div);
    });
    suggestionsBox.style.display = suggestions.length ? 'block' : 'none';
  });
  searchInput.addEventListener('keydown', function(e) {
    if (!suggestions.length) return;
    if (e.key === 'ArrowDown') {
      selectedSuggestionIdx = Math.min(suggestions.length - 1, selectedSuggestionIdx + 1);
      updateSuggestionsActive();
      e.preventDefault();
    } else if (e.key === 'ArrowUp') {
      selectedSuggestionIdx = Math.max(0, selectedSuggestionIdx - 1);
      updateSuggestionsActive();
      e.preventDefault();
    } else if (e.key === 'Enter' && selectedSuggestionIdx >= 0) {
      suggestionsBox.children[selectedSuggestionIdx].click();
      e.preventDefault();
    }
  });
  function updateSuggestionsActive() {
    Array.from(suggestionsBox.children).forEach((el, i) => {
      el.classList.toggle('active', i === selectedSuggestionIdx);
    });
  }
  searchInput.addEventListener('blur', function() {
    setTimeout(() => { suggestionsBox.style.display = 'none'; }, 100);
  });

  let selectedCat = null;
  let dietaryFilters = [];
  let xField = 'protein_per_100g';
  let yField = 'Nutr_Sugars_per_100g';
  let sizeField = 'protein_per_dollar';
  // Field metadata for dropdowns
  const fieldMeta = {
    protein_per_100g: { label: 'Protein per 100g' },
    protein_per_dollar: { label: 'Protein per $' },
    protein_sugar_ratio: { label: 'Protein:Sugar Ratio' },
    kcal_per_g: { label: 'Kcal per Gram' },
    kcal_per_dollar: { label: 'Kcal per $' },
    protein_as_pct_of_calories: { label: '% Calories from Protein' },
    pct_calories_from_protein: { label: '% Calories from Protein' },
    pct_calories_from_fat: { label: '% Calories from Fat' },
    pct_calories_from_carbohydrates: { label: '% Calories from Carbs' },
    Nutr_Sugars_per_100g: { label: 'Sugar per 100g' },
    Nutr_Fat_Total_per_100g: { label: 'Fat per 100g' },
    Nutr_Carbohydrate_per_100g: { label: 'Carbs per 100g' },
    Price: { label: 'Price ($)' },
    Nutr_Energy_kJ_per_100g: { label: 'Energy (kJ/100g)' },
    HealthStarRating: { label: 'Health Star Rating' },
    // Add more if needed
  };
  // Find all numeric fields in products
  const numericFields = Object.keys(products[0] || {}).filter(k => typeof products[0][k] === 'number' || !isNaN(parseFloat(products[0][k])));
  // Populate dropdowns
  const xSel = document.getElementById('x-axis-select');
  const ySel = document.getElementById('y-axis-select');
  const sizeSel = document.getElementById('size-axis-select');
  const reverseXBtn = document.getElementById('reverse-x');
  const reverseYBtn = document.getElementById('reverse-y');
  const reverseSizeBtn = document.getElementById('reverse-size');
  let reverseX = false, reverseY = true, reverseSize = false;
  function populateAxisDropdown(sel, current) {
    sel.innerHTML = '';
    numericFields.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f;
      opt.textContent = fieldMeta[f]?.label || f;
      if (f === current) opt.selected = true;
      sel.appendChild(opt);
    });
  }
  populateAxisDropdown(xSel, xField);
  populateAxisDropdown(ySel, yField);
  populateAxisDropdown(sizeSel, sizeField);
  xSel.onchange = () => { xField = xSel.value; updateView(); };
  ySel.onchange = () => { yField = ySel.value; updateView(); };
  sizeSel.onchange = () => { sizeField = sizeSel.value; updateView(); };

  // Axis header reversal highlighting
  const xLabel = document.querySelector('.axis-row .axis-header.x');
  const yLabel = document.querySelector('.axis-row .axis-header.y');
  const sizeLabel = document.querySelector('.axis-row .axis-header.size');
  function updateAxisHeaderStyles() {
    xLabel.classList.toggle('reversed', reverseX);
    yLabel.classList.toggle('reversed', reverseY);
    sizeLabel.classList.toggle('reversed', reverseSize);
  }
  // Patch updateView to always update axis header styles
  const oldUpdateView = updateView;
  updateView = function() {
    if (typeof oldUpdateView === 'function') oldUpdateView.apply(this, arguments);
    updateAxisHeaderStyles();
  };
  updateAxisHeaderStyles();
  function updateReverseButtonStyles() {
    reverseXBtn.classList.toggle('reversed', reverseX);
    reverseYBtn.classList.toggle('reversed', reverseY);
    reverseSizeBtn.classList.toggle('reversed', reverseSize);
  }
  reverseXBtn.onclick = () => { reverseX = !reverseX; updateReverseButtonStyles(); updateView(); };
  reverseYBtn.onclick = () => { reverseY = !reverseY; updateReverseButtonStyles(); updateView(); };
  reverseSizeBtn.onclick = () => { reverseSize = !reverseSize; updateReverseButtonStyles(); updateView(); };
  // Set initial style
  updateReverseButtonStyles();

  const catSelContainer = document.getElementById('category-selector');
  const dietaryContainer = document.getElementById('dietary-toggles');
  const visContainer = document.getElementById('visualization');
  renderCategorySelector(catTree, catSelContainer, cat => {
    selectedCat = cat;
    updateView();
  });
  renderDietaryToggles(dietaryContainer, () => {
    dietaryFilters = Array.from(dietaryContainer.querySelectorAll('input:checked')).map(cb => cb.value);
    updateView();
  });
  let productKeyword = '';
  function updateView() {
    let filtered;
    if (!selectedCat) {
      // No category selected: show all products, filtered by dietary and keyword
      filtered = products;
      if (dietaryFilters && dietaryFilters.length > 0) {
        filtered = filterProducts(filtered, mapping, null, dietaryFilters);
      }
      if (productKeyword && productKeyword.trim() !== '') {
        const kw = productKeyword.toLowerCase();
        filtered = filtered.filter(p => {
          return (
            (p.ProductName && p.ProductName.toLowerCase().includes(kw)) ||
            (p.Brand && p.Brand.toLowerCase().includes(kw)) ||
            (p.Description && p.Description.toLowerCase().includes(kw))
          );
        });
      }
      if (!filtered.length) {
        visContainer.textContent = 'No products found.';
        return;
      }
    } else {
      filtered = filterProducts(products, mapping, selectedCat, dietaryFilters);
      if (productKeyword && productKeyword.trim() !== '') {
        const kw = productKeyword.toLowerCase();
        filtered = filtered.filter(p => {
          return (
            (p.ProductName && p.ProductName.toLowerCase().includes(kw)) ||
            (p.Brand && p.Brand.toLowerCase().includes(kw)) ||
            (p.Description && p.Description.toLowerCase().includes(kw))
          );
        });
      }
      if (!filtered.length) {
        visContainer.textContent = 'Please select a category.';
        return;
      }
    }
    renderVisualization(
      visContainer,
      filtered,
      xField,
      yField,
      sizeField,
      fieldMeta,
      reverseX,
      reverseY,
      reverseSize
    );
  }
  // Keyword search event
  const productKeywordInput = document.getElementById('product-keyword-search');
  if (productKeywordInput) {
    let debounceTimer;
    productKeywordInput.addEventListener('input', function() {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        productKeyword = this.value.trim();
        updateView();
      }, 200);
    });
    // Show all products at start
    updateView();
  }  
}

// Ensure updateView is called after page load if a category is already selected
window.addEventListener('DOMContentLoaded', () => {
  if (typeof selectedCat !== 'undefined' && selectedCat) {
    updateView();
  }
});

main();
