const BRANDS_PATH = "/middleby/data/catalog/shop_brands.json";
const PRODUCTS_PATH = "/middleby/data/catalog/shop_products.json";

const cache = { brands: null, products: null };

function normalizeBrandKey(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[®™]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

async function loadJson(path) {
  const res = await fetch(path);
  if (!res.ok) return [];
  const json = await res.json();
  return Array.isArray(json) ? json : [];
}

export async function loadMiddlebyBrands() {
  if (cache.brands) return cache.brands;
  cache.brands = await loadJson(BRANDS_PATH);
  return cache.brands;
}

export async function loadMiddlebyProducts() {
  if (cache.products) return cache.products;
  cache.products = await loadJson(PRODUCTS_PATH);
  return cache.products;
}

export async function findMiddlebyBrand(name) {
  const q = normalizeBrandKey(name);
  if (!q) return null;
  const brands = await loadMiddlebyBrands();
  return (
    brands.find((b) => normalizeBrandKey(b.brand_name) === q) ||
    brands.find((b) => normalizeBrandKey(b.brand_slug) === q) ||
    brands.find((b) => normalizeBrandKey(b.brand_name).includes(q)) ||
    brands.find((b) => q.includes(normalizeBrandKey(b.brand_name))) ||
    null
  );
}

export async function findMiddlebyProduct(sku) {
  const q = String(sku || "").trim().toLowerCase();
  if (!q) return null;
  const products = await loadMiddlebyProducts();
  return (
    products.find((p) => String(p.sku || "").toLowerCase() === q) ||
    products.find((p) => String(p.sku || "").toLowerCase().includes(q)) ||
    null
  );
}
