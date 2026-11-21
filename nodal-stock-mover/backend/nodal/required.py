"""Dataset names and columns required for Brief B. No heavy imports: the light public server needs this too."""

REQUIRED = {
    "stores": ["store_id", "city", "state", "zone", "tier", "stock_capacity_units", "opened_on"],
    "items": ["sku_id", "style_id", "style_name", "category", "size", "mrp", "launch_date", "is_core_style", "is_packaging"],
    "bills": ["bill_id", "bill_date", "store_id"],
    "bill_lines": ["bill_id", "sku_id", "qty", "mrp", "selling_price"],
    "stock_snapshot": ["as_of_date", "location_id", "sku_id", "on_hand", "in_transit", "in_transit_arrives_on", "oldest_stock_received_on"],
    "stock_weekly": ["week_ending", "store_id", "sku_id", "closing_on_hand", "days_in_stock"],
    "warehouse_inbound": ["expected_date", "sku_id", "qty", "status"],
    "calendar": ["date", "state", "festival", "is_wedding_date"],
    "rules": ["rule", "detail"],
    "min_display": ["tier", "category", "min_display_units"],
    "lead_times": ["route", "days"],
    "flagship_protected": ["store_id", "rank", "style_id"],
}


class DataError(Exception):
    """Raised with an actionable message when required data is missing or malformed."""
