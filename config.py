import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_SHEETS_CREDS_FILE = os.getenv("GOOGLE_SHEETS_CREDS_FILE", "credentials.json")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

SHOPIFY_STORE = os.getenv("SHOPIFY_STORE")
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/2025-01/graphql.json"
