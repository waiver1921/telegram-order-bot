"""Google Sheets service — read/write clients, catalog, orders, sales reps."""

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from typing import Optional
import logging

from config import GOOGLE_SHEETS_CREDS_FILE, GOOGLE_SHEET_ID

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_sheet():
    client = _get_client()
    return client.open_by_key(GOOGLE_SHEET_ID)


# ─── Sales Reps ───────────────────────────────────────────────

def get_sales_rep(telegram_id: int) -> Optional[dict]:
    """Find sales rep by telegram_id. Returns dict or None."""
    try:
        sheet = _get_sheet()
        ws = sheet.worksheet("Sales Reps")
        records = ws.get_all_records()
        for row in records:
            if str(row.get("telegram_id")) == str(telegram_id):
                return row
    except Exception as e:
        logger.error(f"Error fetching sales rep: {e}")
    return None


# ─── Clients ──────────────────────────────────────────────────

def search_clients(query: str) -> list[dict]:
    """Search clients by name, contact_person, phone, email (case-insensitive partial)."""
    try:
        sheet = _get_sheet()
        ws = sheet.worksheet("Клиенты")
        records = ws.get_all_records()
        q = query.lower().strip()
        results = []
        for row in records:
            searchable = " ".join([
                str(row.get("name", "")),
                str(row.get("contact_person", "")),
                str(row.get("phone", "")),
                str(row.get("email", "")),
            ]).lower()
            if q in searchable:
                results.append(row)
        return results
    except Exception as e:
        logger.error(f"Error searching clients: {e}")
        return []


def get_client_by_id(client_id: str) -> Optional[dict]:
    """Get a single client by client_id."""
    try:
        sheet = _get_sheet()
        ws = sheet.worksheet("Клиенты")
        records = ws.get_all_records()
        for row in records:
            if str(row.get("client_id")) == str(client_id):
                return row
    except Exception as e:
        logger.error(f"Error fetching client: {e}")
    return None


def get_client_by_telegram_id(telegram_id: int) -> Optional[dict]:
    """Get client by telegram_id."""
    try:
        sheet = _get_sheet()
        ws = sheet.worksheet("Клиенты")
        records = ws.get_all_records()
        for row in records:
            if str(row.get("telegram_id")) == str(telegram_id):
                return row
    except Exception as e:
        logger.error(f"Error fetching client by tg id: {e}")
    return None


def update_client_after_order(client_id: str, order_summary: str, telegram_id: int = None):
    """Update last_order_date, usual_order, and optionally telegram_id."""
    try:
        sheet = _get_sheet()
        ws = sheet.worksheet("Клиенты")
        records = ws.get_all_records()
        headers = ws.row_values(1)

        for i, row in enumerate(records, start=2):  # row 1 is header
            if str(row.get("client_id")) == str(client_id):
                col_map = {h: idx + 1 for idx, h in enumerate(headers)}
                ws.update_cell(i, col_map["last_order_date"], datetime.now().strftime("%Y-%m-%d"))
                ws.update_cell(i, col_map["usual_order"], order_summary)
                if telegram_id and not row.get("telegram_id"):
                    ws.update_cell(i, col_map["telegram_id"], str(telegram_id))
                break
    except Exception as e:
        logger.error(f"Error updating client: {e}")


# ─── Catalog ──────────────────────────────────────────────────

def get_catalog() -> list[dict]:
    """Get all products from catalog, sorted by sort_order."""
    try:
        sheet = _get_sheet()
        ws = sheet.worksheet("Каталог")
        records = ws.get_all_records()
        records.sort(key=lambda r: int(r.get("sort_order", 999)))
        return records
    except Exception as e:
        logger.error(f"Error fetching catalog: {e}")
        return []


def get_categories() -> list[str]:
    """Get unique categories from catalog."""
    catalog = get_catalog()
    seen = []
    for item in catalog:
        cat = item.get("category", "")
        if cat and cat not in seen:
            seen.append(cat)
    return seen


def get_products_by_category(category: str) -> list[dict]:
    """Get unique product names within a category."""
    catalog = get_catalog()
    seen = []
    results = []
    for item in catalog:
        if item.get("category") == category and item.get("name") not in seen:
            seen.append(item.get("name"))
            results.append(item)
    return results


def get_variants(category: str, name: str) -> list[dict]:
    """Get variants (sizes) for a specific product."""
    catalog = get_catalog()
    return [
        item for item in catalog
        if item.get("category") == category and item.get("name") == name
        and str(item.get("in_stock", "")).upper() == "TRUE"
    ]


def get_price(product: dict, price_group: str) -> float:
    """Get the price for a product based on price_group."""
    col = f"price_{price_group}"
    try:
        return float(product.get(col, product.get("price_retail", 0)))
    except (ValueError, TypeError):
        return float(product.get("price_retail", 0))


# ─── Orders ───────────────────────────────────────────────────

def save_order(order_data: dict):
    """Append a new order row to the Orders sheet."""
    try:
        sheet = _get_sheet()
        ws = sheet.worksheet("Заказы")
        headers = ws.row_values(1)
        row = [order_data.get(h, "") for h in headers]
        ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"Order saved: {order_data.get('order_id')}")
    except Exception as e:
        logger.error(f"Error saving order: {e}")


def get_next_order_id() -> str:
    """Generate next order ID like ORD-2026-0042."""
    try:
        sheet = _get_sheet()
        ws = sheet.worksheet("Заказы")
        records = ws.get_all_records()
        year = datetime.now().year
        max_num = 0
        for row in records:
            oid = str(row.get("order_id", ""))
            if oid.startswith(f"ORD-{year}-"):
                try:
                    num = int(oid.split("-")[-1])
                    max_num = max(max_num, num)
                except ValueError:
                    pass
        return f"ORD-{year}-{max_num + 1:04d}"
    except Exception as e:
        logger.error(f"Error generating order ID: {e}")
        return f"ORD-{datetime.now().year}-0001"
