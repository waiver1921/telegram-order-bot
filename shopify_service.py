"""Shopify GraphQL Admin API service — draft orders."""

import httpx
import logging
from config import SHOPIFY_GRAPHQL_URL, SHOPIFY_ACCESS_TOKEN

logger = logging.getLogger(__name__)

HEADERS = {
    "Content-Type": "application/json",
    "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
}

# ─── Draft Order ──────────────────────────────────────────────

DRAFT_ORDER_CREATE = """
mutation draftOrderCreate($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder {
      id
      name
      invoiceUrl
      status
      totalPriceSet {
        shopMoney {
          amount
          currencyCode
        }
      }
    }
    userErrors {
      message
      field
    }
  }
}
"""


async def create_draft_order(
    customer_id: str | None,
    line_items: list[dict],
    shipping_address: dict,
    note: str = "",
    tags: list[str] | None = None,
) -> dict:
    """
    Create a Shopify draft order.

    line_items: list of dicts with keys:
      - variant_id: str (Shopify GID)
      - quantity: int
      - applied_discount: dict | None  (title, value, value_type)

    shipping_address: dict with keys:
      firstName, lastName, company, address1, city, zip, countryCode

    Returns dict with id, name, invoiceUrl, totalPrice or error.
    """
    shopify_line_items = []
    for item in line_items:
        if item.get("variant_id") and item["variant_id"].startswith("gid://"):
            # Variant-based line item
            li = {
                "variantId": item["variant_id"],
                "quantity": item["quantity"],
            }
            if item.get("applied_discount"):
                li["appliedDiscount"] = item["applied_discount"]
        else:
            # Custom line item (no variant in Shopify)
            li = {
                "title": item.get("title", "Product"),
                "quantity": item["quantity"],
                "originalUnitPrice": str(item.get("custom_price", "0.00")),
            }
        shopify_line_items.append(li)

    input_data = {
        "lineItems": shopify_line_items,
        "shippingAddress": shipping_address,
        "note": note,
        "tags": tags or ["telegram-bot"],
        "shippingLine": {
            "title": "Доставка",
            "price": "0.00",
        },
    }
    if customer_id:
        input_data["customerId"] = customer_id

    variables = {"input": input_data}
    payload = {"query": DRAFT_ORDER_CREATE, "variables": variables}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(SHOPIFY_GRAPHQL_URL, json=payload, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()

        result = data.get("data", {}).get("draftOrderCreate", {})
        errors = result.get("userErrors", [])
        if errors:
            logger.error(f"Shopify userErrors: {errors}")
            return {"error": "; ".join(e["message"] for e in errors)}

        draft = result.get("draftOrder", {})
        return {
            "id": draft.get("id", ""),
            "name": draft.get("name", ""),
            "invoiceUrl": draft.get("invoiceUrl", ""),
            "totalPrice": draft.get("totalPriceSet", {}).get("shopMoney", {}).get("amount", "0"),
        }
    except Exception as e:
        logger.error(f"Shopify API error: {e}")
        return {"error": str(e)}


# ─── Invoice Send ─────────────────────────────────────────────

DRAFT_ORDER_INVOICE_SEND = """
mutation draftOrderInvoiceSend($id: ID!, $email: DraftOrderInvoiceInput!) {
  draftOrderInvoiceSend(id: $id, email: $email) {
    draftOrder {
      id
      status
    }
    userErrors {
      message
      field
    }
  }
}
"""


async def send_invoice(draft_order_id: str, to_email: str, subject: str, message: str) -> dict:
    """Send invoice email for a draft order."""
    variables = {
        "id": draft_order_id,
        "email": {
            "to": to_email,
            "subject": subject,
            "customMessage": message,
        },
    }
    payload = {"query": DRAFT_ORDER_INVOICE_SEND, "variables": variables}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(SHOPIFY_GRAPHQL_URL, json=payload, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()

        result = data.get("data", {}).get("draftOrderInvoiceSend", {})
        errors = result.get("userErrors", [])
        if errors:
            return {"error": "; ".join(e["message"] for e in errors)}
        return {"success": True}
    except Exception as e:
        logger.error(f"Shopify invoice error: {e}")
        return {"error": str(e)}
