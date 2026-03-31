"""Shopify GraphQL Admin API service — draft orders.

Auth: Client Credentials flow (post Jan 2026)
  POST https://{shop}.myshopify.com/admin/oauth/access_token
  Content-Type: application/x-www-form-urlencoded
  grant_type=client_credentials&client_id=...&client_secret=...

Token is valid for 24h, auto-refreshed 5 min before expiry.
"""

import httpx
import logging
import time
from config import SHOPIFY_STORE, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET, SHOPIFY_GRAPHQL_URL

logger = logging.getLogger(__name__)

# ─── Token Management ─────────────────────────────────────────

_token_cache = {
    "access_token": None,
    "expires_at": 0,
}


async def _get_access_token() -> str:
    """Get a valid access token, refreshing if needed."""
    now = time.time()

    # Return cached token if still valid (refresh 5 min before expiry)
    if _token_cache["access_token"] and _token_cache["expires_at"] > now + 300:
        return _token_cache["access_token"]

    logger.info("Requesting new Shopify access token via client credentials...")

    url = f"https://{SHOPIFY_STORE}/admin/oauth/access_token"

    # MUST be form-encoded, not JSON
    form_data = {
        "grant_type": "client_credentials",
        "client_id": SHOPIFY_CLIENT_ID,
        "client_secret": SHOPIFY_CLIENT_SECRET,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                data=form_data,  # form-encoded
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()

        token = data.get("access_token", "")
        expires_in = data.get("expires_in", 86399)

        if not token:
            logger.error(f"No access_token in response: {data}")
            raise ValueError("Empty access_token from Shopify")

        _token_cache["access_token"] = token
        _token_cache["expires_at"] = now + expires_in

        logger.info(f"Shopify token obtained, expires in {expires_in}s, scopes: {data.get('scope', '?')}")
        return token

    except Exception as e:
        logger.error(f"Failed to get Shopify access token: {e}")
        # Return cached token as fallback
        if _token_cache["access_token"]:
            logger.warning("Using expired cached token as fallback")
            return _token_cache["access_token"]
        raise


async def _get_headers() -> dict:
    """Get request headers with fresh access token."""
    token = await _get_access_token()
    return {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": token,
    }


# ─── GraphQL helper ──────────────────────────────────────────


async def _graphql(query: str, variables: dict = None) -> dict:
    """Execute a GraphQL query against Shopify Admin API."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    headers = await _get_headers()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(SHOPIFY_GRAPHQL_URL, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


# ─── Draft Order Create ──────────────────────────────────────

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
    """Create a Shopify draft order. Returns dict with id, name, invoiceUrl, totalPrice or error."""

    shopify_line_items = []
    for item in line_items:
        if item.get("variant_id") and str(item["variant_id"]).startswith("gid://"):
            li = {
                "variantId": item["variant_id"],
                "quantity": item["quantity"],
            }
            if item.get("applied_discount"):
                li["appliedDiscount"] = item["applied_discount"]
        else:
            # Custom line item — title + price, no variant needed
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

    try:
        data = await _graphql(DRAFT_ORDER_CREATE, {"input": input_data})

        # Check for top-level GraphQL errors
        if "errors" in data:
            error_msg = "; ".join(e.get("message", str(e)) for e in data["errors"])
            logger.error(f"Shopify GraphQL errors: {error_msg}")
            return {"error": error_msg}

        result = data.get("data", {}).get("draftOrderCreate", {})
        user_errors = result.get("userErrors", [])
        if user_errors:
            error_msg = "; ".join(e["message"] for e in user_errors)
            logger.error(f"Shopify userErrors: {error_msg}")
            return {"error": error_msg}

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


# ─── Draft Order Invoice Send ────────────────────────────────

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

    try:
        data = await _graphql(DRAFT_ORDER_INVOICE_SEND, variables)

        if "errors" in data:
            error_msg = "; ".join(e.get("message", str(e)) for e in data["errors"])
            return {"error": error_msg}

        result = data.get("data", {}).get("draftOrderInvoiceSend", {})
        user_errors = result.get("userErrors", [])
        if user_errors:
            return {"error": "; ".join(e["message"] for e in user_errors)}
        return {"success": True}
    except Exception as e:
        logger.error(f"Shopify invoice error: {e}")
        return {"error": str(e)}
