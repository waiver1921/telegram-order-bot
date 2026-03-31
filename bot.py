"""
Telegram Order Bot — Sales Rep Flow (Phase 1)

Features:
- Auth by telegram_id (Sales Reps sheet)
- Client search in Google Sheets
- New client creation
- Catalog browsing (category → product → variant → quantity)
- Price by client's price_group
- Cart assembly
- Address selection
- Draft order creation in Shopify
- Order logging in Google Sheets
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import TELEGRAM_BOT_TOKEN
import sheets_service as sheets
import shopify_service as shopify

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Conversation states ──────────────────────────────────────

(
    MAIN_MENU,
    SEARCH_CLIENT,
    SELECT_CLIENT,
    CLIENT_CARD,
    SELECT_CATEGORY,
    SELECT_PRODUCT,
    SELECT_VARIANT,
    SELECT_QUANTITY,
    CART,
    SELECT_ADDRESS,
    ENTER_ADDRESS,
    CONFIRM_ORDER,
    # New client creation states
    NEW_CLIENT_NAME,
    NEW_CLIENT_CONTACT,
    NEW_CLIENT_PHONE,
    NEW_CLIENT_EMAIL,
    NEW_CLIENT_PRICE_GROUP,
    NEW_CLIENT_ADDRESS,
    NEW_CLIENT_CONFIRM,
) = range(19)

# ─── Helpers ──────────────────────────────────────────────────


def build_kb(buttons: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Build inline keyboard from list of rows of (text, callback_data)."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in buttons]
    )


def format_cart(cart: list[dict]) -> str:
    """Format cart items as readable text."""
    if not cart:
        return "Корзина пуста."
    lines = []
    total = 0.0
    for i, item in enumerate(cart, 1):
        subtotal = item["price"] * item["quantity"]
        total += subtotal
        lines.append(f"{i}. {item['display_name']} × {item['quantity']} — €{subtotal:.2f} (€{item['price']:.2f}/шт)")
    lines.append(f"\n💰 Итого: €{total:.2f}")
    return "\n".join(lines)


def cart_total(cart: list[dict]) -> float:
    return sum(item["price"] * item["quantity"] for item in cart)


def order_items_summary(cart: list[dict]) -> str:
    """Compact summary for sheets log."""
    parts = []
    for item in cart:
        parts.append(f"{item['display_name']} x{item['quantity']} @€{item['price']:.2f}")
    return ", ".join(parts)


# ─── Global back_main handler ────────────────────────────────


async def global_back_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Universal handler for back_main from any state."""
    query = update.callback_query
    await query.answer()
    return await _go_main_menu(query, context)


# ─── /start ───────────────────────────────────────────────────


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point — check if user is a sales rep."""
    tg_id = update.effective_user.id
    rep = sheets.get_sales_rep(tg_id)

    if not rep:
        await update.message.reply_text(
            "⛔ Доступ запрещён.\n"
            "Этот бот только для менеджеров. Ваш Telegram ID не найден в базе.\n"
            f"Ваш ID: `{tg_id}`",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["rep"] = rep
    context.user_data["cart"] = []
    context.user_data["client"] = None
    context.user_data["address"] = None

    name = rep.get("name", "менеджер")
    kb = build_kb([
        [("🆕 Новый заказ", "new_order")],
        [("📋 Мои заказы", "my_orders")],
    ])
    await update.message.reply_text(f"Привет, {name}! Что делаем?", reply_markup=kb)
    return MAIN_MENU


# ─── Main Menu ────────────────────────────────────────────────


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "new_order":
        context.user_data["cart"] = []
        context.user_data["client"] = None
        context.user_data["address"] = None
        kb = build_kb([
            [("🔍 Поиск клиента", "search_client")],
            [("➕ Новый клиент", "new_client")],
        ])
        await query.edit_message_text("Для кого заказ?", reply_markup=kb)
        return SEARCH_CLIENT

    elif query.data == "my_orders":
        await query.edit_message_text("🚧 Раздел «Мои заказы» будет в Фазе 2.")
        return MAIN_MENU

    return MAIN_MENU


# ─── Client Search ────────────────────────────────────────────


async def search_client_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔍 Введите имя, телефон или компанию:")
    return SELECT_CLIENT


async def search_client_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed a search query."""
    query_text = update.message.text.strip()
    results = sheets.search_clients(query_text)

    if not results:
        kb = build_kb([
            [("🔍 Искать снова", "search_client")],
            [("➕ Новый клиент", "new_client")],
            [("↩️ Главное меню", "back_main")],
        ])
        await update.message.reply_text("Ничего не найдено.", reply_markup=kb)
        return SEARCH_CLIENT

    # Store results for selection
    context.user_data["search_results"] = results

    buttons = []
    for i, client in enumerate(results[:10]):
        label = f"{client.get('name', '?')} — {client.get('price_group', '?')}"
        addr = client.get("address_1", "")
        if addr:
            label += f" — {addr[:30]}"
        buttons.append([(label, f"pick_client_{i}")])
    buttons.append([("🔍 Искать снова", "search_client"), ("➕ Новый", "new_client")])
    buttons.append([("↩️ Главное меню", "back_main")])

    kb = build_kb(buttons)
    await update.message.reply_text(f"Найдено ({len(results)}):", reply_markup=kb)
    return SELECT_CLIENT


async def pick_client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User selected a client from search results."""
    query = update.callback_query
    await query.answer()

    if query.data == "search_client":
        await query.edit_message_text("🔍 Введите имя, телефон или компанию:")
        return SELECT_CLIENT

    if query.data == "new_client":
        await query.edit_message_text("➕ Введите название компании / имя клиента:")
        context.user_data["new_client"] = {}
        return NEW_CLIENT_NAME

    idx = int(query.data.replace("pick_client_", ""))
    results = context.user_data.get("search_results", [])
    if idx >= len(results):
        await query.edit_message_text("Ошибка. Попробуйте поиск заново.")
        return SEARCH_CLIENT

    client = results[idx]
    context.user_data["client"] = client
    return await _show_client_card(query, context, client)


async def _show_client_card(query, context, client) -> int:
    """Display client card with actions."""
    name = client.get("name", "—")
    group = client.get("price_group", "—")
    addr1 = client.get("address_1", "—")
    last = client.get("last_order_date", "—")
    usual = client.get("usual_order", "—")

    text = (
        f"🏢 {name}\n"
        f"Группа: {group}\n"
        f"Адрес: {addr1}\n"
        f"Последний заказ: {last}\n"
        f"Обычный заказ: {usual}"
    )

    kb = build_kb([
        [("🆕 Новый заказ", "start_order")],
        [("🔍 Другой клиент", "search_client")],
        [("↩️ Главное меню", "back_main")],
    ])
    await query.edit_message_text(text, reply_markup=kb)
    return CLIENT_CARD


async def client_card_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "start_order":
        context.user_data["cart"] = []
        return await _show_categories(query, context)
    elif query.data == "search_client":
        await query.edit_message_text("🔍 Введите имя, телефон или компанию:")
        return SELECT_CLIENT

    return CLIENT_CARD


# ─── New Client Creation ─────────────────────────────────────


async def new_client_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start new client flow — from callback button."""
    query = update.callback_query
    await query.answer()
    context.user_data["new_client"] = {}
    await query.edit_message_text("➕ Введите название компании / имя клиента:")
    return NEW_CLIENT_NAME


async def new_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Got company/client name."""
    name = update.message.text.strip()
    context.user_data["new_client"]["name"] = name
    await update.message.reply_text(f"✅ Компания: {name}\n\nВведите контактное лицо (имя и фамилия):")
    return NEW_CLIENT_CONTACT


async def new_client_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Got contact person."""
    contact = update.message.text.strip()
    context.user_data["new_client"]["contact_person"] = contact
    await update.message.reply_text(f"✅ Контакт: {contact}\n\nВведите телефон:")
    return NEW_CLIENT_PHONE


async def new_client_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Got phone."""
    phone = update.message.text.strip()
    context.user_data["new_client"]["phone"] = phone
    kb = build_kb([[("⏩ Пропустить", "skip_email")]])
    await update.message.reply_text(f"✅ Телефон: {phone}\n\nВведите email:", reply_markup=kb)
    return NEW_CLIENT_EMAIL


async def new_client_email_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Got email as text."""
    email = update.message.text.strip()
    context.user_data["new_client"]["email"] = email
    kb = build_kb([
        [("retail", "pg_retail"), ("vip", "pg_vip")],
        [("b2b_standard", "pg_b2b_standard"), ("b2b_gold", "pg_b2b_gold")],
    ])
    await update.message.reply_text("Выберите ценовую группу:", reply_markup=kb)
    return NEW_CLIENT_PRICE_GROUP


async def new_client_email_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skip email."""
    query = update.callback_query
    await query.answer()
    context.user_data["new_client"]["email"] = ""
    kb = build_kb([
        [("retail", "pg_retail"), ("vip", "pg_vip")],
        [("b2b_standard", "pg_b2b_standard"), ("b2b_gold", "pg_b2b_gold")],
    ])
    await query.edit_message_text("Выберите ценовую группу:", reply_markup=kb)
    return NEW_CLIENT_PRICE_GROUP


async def new_client_price_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Got price group."""
    query = update.callback_query
    await query.answer()
    group = query.data.replace("pg_", "")
    context.user_data["new_client"]["price_group"] = group
    await query.edit_message_text(
        f"✅ Группа: {group}\n\nВведите адрес доставки (улица, индекс, город):"
    )
    return NEW_CLIENT_ADDRESS


async def new_client_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Got address — show confirmation."""
    address = update.message.text.strip()
    nc = context.user_data["new_client"]
    nc["address_1"] = address

    text = (
        f"📋 Новый клиент:\n\n"
        f"🏢 {nc.get('name', '—')}\n"
        f"👤 {nc.get('contact_person', '—')}\n"
        f"📞 {nc.get('phone', '—')}\n"
        f"📧 {nc.get('email', '—') or '—'}\n"
        f"💰 {nc.get('price_group', '—')}\n"
        f"📍 {address}\n\n"
        f"Всё верно?"
    )
    kb = build_kb([
        [("✅ Сохранить", "save_client")],
        [("✏️ Начать заново", "redo_client")],
        [("❌ Отмена", "back_main")],
    ])
    await update.message.reply_text(text, reply_markup=kb)
    return NEW_CLIENT_CONFIRM


async def new_client_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save or redo new client."""
    query = update.callback_query
    await query.answer()

    if query.data == "back_main":
        return await _go_main_menu(query, context)

    if query.data == "redo_client":
        context.user_data["new_client"] = {}
        await query.edit_message_text("➕ Введите название компании / имя клиента:")
        return NEW_CLIENT_NAME

    if query.data == "save_client":
        nc = context.user_data["new_client"]
        client_id = sheets.get_next_client_id()
        client_data = {
            "client_id": client_id,
            "name": nc.get("name", ""),
            "contact_person": nc.get("contact_person", ""),
            "phone": nc.get("phone", ""),
            "email": nc.get("email", ""),
            "telegram_id": "",
            "price_group": nc.get("price_group", "retail"),
            "address_1": nc.get("address_1", ""),
            "address_2": "",
            "address_label_1": "",
            "address_label_2": "",
            "notes": "",
            "shopify_customer_id": "",
            "usual_order": "",
            "last_order_date": "",
        }
        result = sheets.create_client(client_data)
        if result:
            context.user_data["client"] = client_data
            text = (
                f"✅ Клиент «{nc.get('name')}» создан! ({client_id})\n\n"
                f"🏢 {nc.get('name')}\n"
                f"Группа: {nc.get('price_group')}\n"
                f"Адрес: {nc.get('address_1')}"
            )
            kb = build_kb([
                [("🆕 Новый заказ", "start_order")],
                [("🔍 Другой клиент", "search_client")],
                [("↩️ Главное меню", "back_main")],
            ])
            await query.edit_message_text(text, reply_markup=kb)
            return CLIENT_CARD
        else:
            await query.edit_message_text("❌ Ошибка при сохранении. Попробуйте снова.")
            return await _go_main_menu_msg(query, context)

    return NEW_CLIENT_CONFIRM


# ─── Catalog ──────────────────────────────────────────────────


async def _show_categories(query, context) -> int:
    """Show category buttons."""
    categories = sheets.get_categories()
    if not categories:
        await query.edit_message_text("Каталог пуст. Проверьте лист «Каталог» в Google Sheets.")
        return MAIN_MENU

    ICONS = {"Икра": "🐟", "Crème Fraîche": "🥄", "Наборы": "🎁"}
    buttons = []
    row = []
    for cat in categories:
        icon = ICONS.get(cat, "📦")
        row.append((f"{icon} {cat}", f"cat_{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # If cart not empty, show cart button
    cart = context.user_data.get("cart", [])
    if cart:
        buttons.append([("🛒 Корзина ({})".format(len(cart)), "show_cart")])
    buttons.append([("↩️ Назад", "back_to_client")])

    kb = build_kb(buttons)
    await query.edit_message_text("Выберите категорию:", reply_markup=kb)
    return SELECT_CATEGORY


async def category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "show_cart":
        return await _show_cart(query, context)
    if query.data == "back_to_client":
        client = context.user_data.get("client")
        if client:
            return await _show_client_card(query, context, client)
        return await _go_main_menu(query, context)

    category = query.data.replace("cat_", "")
    context.user_data["current_category"] = category

    products = sheets.get_products_by_category(category)
    if not products:
        await query.edit_message_text(f"В категории «{category}» нет товаров.")
        return SELECT_CATEGORY

    buttons = [[(p.get("name", "?"), f"prod_{p.get('name')}")] for p in products]
    buttons.append([("↩️ Назад к категориям", "back_categories")])

    kb = build_kb(buttons)
    await query.edit_message_text(f"📦 {category} — выберите:", reply_markup=kb)
    return SELECT_PRODUCT


async def product_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "back_categories":
        return await _show_categories(query, context)

    product_name = query.data.replace("prod_", "")
    category = context.user_data.get("current_category", "")
    context.user_data["current_product_name"] = product_name

    variants = sheets.get_variants(category, product_name)
    if not variants:
        await query.edit_message_text(f"Нет вариантов в наличии для «{product_name}».")
        return SELECT_PRODUCT

    client = context.user_data.get("client", {})
    price_group = client.get("price_group", "retail")

    buttons = []
    for v in variants:
        price = sheets.get_price(v, price_group)
        label = f"{v.get('variant', '?')} — €{price:.2f}"
        buttons.append([(label, f"var_{v.get('product_id')}")])
    buttons.append([("↩️ Назад", "back_products")])

    kb = build_kb(buttons)
    await query.edit_message_text(f"🐟 {product_name} — выберите размер:", reply_markup=kb)
    return SELECT_VARIANT


async def variant_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "back_products":
        category = context.user_data.get("current_category", "")
        products = sheets.get_products_by_category(category)
        buttons = [[(p.get("name", "?"), f"prod_{p.get('name')}")] for p in products]
        buttons.append([("↩️ Назад к категориям", "back_categories")])
        kb = build_kb(buttons)
        await query.edit_message_text(f"📦 {category} — выберите:", reply_markup=kb)
        return SELECT_PRODUCT

    product_id = query.data.replace("var_", "")

    catalog = sheets.get_catalog()
    variant = None
    for item in catalog:
        if str(item.get("product_id")) == str(product_id):
            variant = item
            break

    if not variant:
        await query.edit_message_text("Товар не найден. Попробуйте снова.")
        return SELECT_VARIANT

    context.user_data["current_variant"] = variant

    client = context.user_data.get("client", {})
    price_group = client.get("price_group", "retail")
    price = sheets.get_price(variant, price_group)
    context.user_data["current_price"] = price

    buttons = [
        [("1", "qty_1"), ("2", "qty_2"), ("3", "qty_3")],
        [("5", "qty_5"), ("10", "qty_10"), ("20", "qty_20")],
        [("↩️ Назад", "back_variants")],
    ]
    kb = build_kb(buttons)
    display = variant.get("display_name", "?")
    await query.edit_message_text(
        f"{display} — €{price:.2f}/шт ({price_group})\n\nКоличество:",
        reply_markup=kb,
    )
    return SELECT_QUANTITY


async def quantity_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "back_variants":
        category = context.user_data.get("current_category", "")
        product_name = context.user_data.get("current_product_name", "")
        variants = sheets.get_variants(category, product_name)
        client = context.user_data.get("client", {})
        price_group = client.get("price_group", "retail")
        buttons = []
        for v in variants:
            price = sheets.get_price(v, price_group)
            label = f"{v.get('variant', '?')} — €{price:.2f}"
            buttons.append([(label, f"var_{v.get('product_id')}")])
        buttons.append([("↩️ Назад", "back_products")])
        kb = build_kb(buttons)
        await query.edit_message_text(f"🐟 {product_name} — выберите размер:", reply_markup=kb)
        return SELECT_VARIANT

    qty = int(query.data.replace("qty_", ""))
    variant = context.user_data.get("current_variant", {})
    price = context.user_data.get("current_price", 0)

    cart_item = {
        "product_id": variant.get("product_id"),
        "display_name": variant.get("display_name", "?"),
        "shopify_variant_id": variant.get("shopify_variant_id", ""),
        "price": price,
        "quantity": qty,
    }
    context.user_data.setdefault("cart", []).append(cart_item)

    return await _show_cart(query, context)


# ─── Cart ─────────────────────────────────────────────────────


async def _show_cart(query, context) -> int:
    cart = context.user_data.get("cart", [])
    text = "🛒 Корзина:\n\n" + format_cart(cart)

    buttons = [
        [("➕ Добавить ещё", "add_more")],
        [("✅ Оформить заказ", "checkout")],
        [("🗑 Очистить корзину", "clear_cart")],
    ]
    kb = build_kb(buttons)
    await query.edit_message_text(text, reply_markup=kb)
    return CART


async def cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "add_more":
        return await _show_categories(query, context)

    if query.data == "clear_cart":
        context.user_data["cart"] = []
        await query.edit_message_text("🗑 Корзина очищена.")
        return await _show_categories(query, context)

    if query.data == "checkout":
        cart = context.user_data.get("cart", [])
        if not cart:
            await query.edit_message_text("Корзина пуста. Добавьте товары.")
            return await _show_categories(query, context)
        return await _show_address_selection(query, context)

    return CART


# ─── Address Selection ────────────────────────────────────────


async def _show_address_selection(query, context) -> int:
    client = context.user_data.get("client", {})
    buttons = []

    addr1 = client.get("address_1", "")
    label1 = client.get("address_label_1", "") or "Адрес 1"
    if addr1:
        short = addr1[:40] if len(addr1) > 40 else addr1
        buttons.append([(f"📍 {label1} — {short}", "addr_1")])

    addr2 = client.get("address_2", "")
    label2 = client.get("address_label_2", "") or "Адрес 2"
    if addr2:
        short = addr2[:40] if len(addr2) > 40 else addr2
        buttons.append([(f"📍 {label2} — {short}", "addr_2")])

    buttons.append([("✏️ Другой адрес", "addr_custom")])
    buttons.append([("↩️ Назад в корзину", "back_cart")])

    kb = build_kb(buttons)
    await query.edit_message_text("📍 Адрес доставки:", reply_markup=kb)
    return SELECT_ADDRESS


async def address_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "back_cart":
        return await _show_cart(query, context)

    client = context.user_data.get("client", {})

    if query.data == "addr_1":
        context.user_data["address"] = client.get("address_1", "")
        return await _show_confirmation(query, context)

    if query.data == "addr_2":
        context.user_data["address"] = client.get("address_2", "")
        return await _show_confirmation(query, context)

    if query.data == "addr_custom":
        await query.edit_message_text("Введите адрес доставки (улица, индекс, город):")
        return ENTER_ADDRESS

    return SELECT_ADDRESS


async def enter_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed a custom address."""
    address_text = update.message.text.strip()
    context.user_data["address"] = address_text

    cart = context.user_data.get("cart", [])
    client = context.user_data.get("client", {})
    address = context.user_data.get("address", "—")

    text = _build_confirmation_text(client, cart, address)
    kb = build_kb([
        [("✅ Создать заказ", "place_order")],
        [("✏️ Редактировать", "edit_order")],
        [("❌ Отменить", "cancel_order")],
    ])
    await update.message.reply_text(text, reply_markup=kb)
    return CONFIRM_ORDER


# ─── Confirmation ─────────────────────────────────────────────


def _build_confirmation_text(client: dict, cart: list[dict], address: str) -> str:
    name = client.get("name", "—")
    price_group = client.get("price_group", "—")

    lines = [f"📦 Заказ для {name} ({price_group}):\n"]
    total = 0.0
    for i, item in enumerate(cart, 1):
        subtotal = item["price"] * item["quantity"]
        total += subtotal
        lines.append(f"{i}. {item['display_name']} × {item['quantity']} — €{subtotal:.2f} (€{item['price']:.2f}/шт)")

    lines.append(f"\n📍 {address}")
    lines.append(f"💰 Итого: €{total:.2f}")
    return "\n".join(lines)


async def _show_confirmation(query, context) -> int:
    cart = context.user_data.get("cart", [])
    client = context.user_data.get("client", {})
    address = context.user_data.get("address", "—")

    text = _build_confirmation_text(client, cart, address)
    kb = build_kb([
        [("✅ Создать заказ", "place_order")],
        [("✏️ Редактировать", "edit_order")],
        [("❌ Отменить", "cancel_order")],
    ])
    await query.edit_message_text(text, reply_markup=kb)
    return CONFIRM_ORDER


async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "edit_order":
        return await _show_cart(query, context)

    if query.data == "cancel_order":
        context.user_data["cart"] = []
        await query.edit_message_text("❌ Заказ отменён.")
        return await _go_main_menu_msg(query, context)

    if query.data == "place_order":
        await query.edit_message_text("⏳ Создаю draft order в Shopify...")
        return await _create_order(query, context)

    return CONFIRM_ORDER


# ─── Create Order ─────────────────────────────────────────────


async def _create_order(query, context) -> int:
    cart = context.user_data.get("cart", [])
    client = context.user_data.get("client", {})
    address_str = context.user_data.get("address", "")
    rep = context.user_data.get("rep", {})

    price_group = client.get("price_group", "retail")
    line_items = []
    for item in cart:
        li = {
            "variant_id": item.get("shopify_variant_id", ""),
            "quantity": item["quantity"],
        }
        catalog = sheets.get_catalog()
        retail_price = None
        for p in catalog:
            if str(p.get("product_id")) == str(item.get("product_id")):
                retail_price = float(p.get("price_retail", 0))
                break
        if retail_price and retail_price > 0 and item["price"] < retail_price:
            discount_pct = round((1 - item["price"] / retail_price) * 100, 1)
            li["applied_discount"] = {
                "title": f"{price_group} price",
                "value": str(discount_pct),
                "valueType": "PERCENTAGE",
            }
        line_items.append(li)

    shipping_address = _parse_address(address_str, client)

    customer_id = client.get("shopify_customer_id", "")
    rep_name = rep.get("name", "?")
    note = f"Sales rep: {rep_name} | Telegram bot order"
    tags = ["telegram-bot", price_group]

    result = await shopify.create_draft_order(
        customer_id=customer_id or None,
        line_items=line_items,
        shipping_address=shipping_address,
        note=note,
        tags=tags,
    )

    if result.get("error"):
        order_id = sheets.get_next_order_id()
        order_data = {
            "order_id": order_id,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "client_id": client.get("client_id", ""),
            "client_name": client.get("name", ""),
            "items": order_items_summary(cart),
            "total": f"{cart_total(cart):.2f}",
            "price_group": price_group,
            "custom_prices": "Нет",
            "address": address_str,
            "sales_rep": rep_name,
            "shopify_draft_id": "",
            "shopify_invoice_url": "",
            "status": "error",
        }
        sheets.save_order(order_data)

        await query.edit_message_text(
            f"⚠️ Ошибка Shopify: {result['error']}\n\n"
            f"Заказ {order_id} сохранён в таблицу со статусом «error»."
        )
        kb = build_kb([
            [("🔄 Повторить", "place_order")],
            [("↩️ Главное меню", "back_main")],
        ])
        await query.message.reply_text("Что делаем?", reply_markup=kb)
        return CONFIRM_ORDER

    # Save to Google Sheets
    order_id = sheets.get_next_order_id()
    order_data = {
        "order_id": order_id,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "client_id": client.get("client_id", ""),
        "client_name": client.get("name", ""),
        "items": order_items_summary(cart),
        "total": f"{cart_total(cart):.2f}",
        "price_group": price_group,
        "custom_prices": "Нет",
        "address": address_str,
        "sales_rep": rep_name,
        "shopify_draft_id": result.get("id", ""),
        "shopify_invoice_url": result.get("invoiceUrl", ""),
        "status": "draft",
    }
    sheets.save_order(order_data)

    sheets.update_client_after_order(
        client_id=client.get("client_id", ""),
        order_summary=order_items_summary(cart),
    )

    invoice_url = result.get("invoiceUrl", "")
    shopify_id = result.get("name", "")
    text = (
        f"✅ Draft order создан!\n\n"
        f"📋 {shopify_id}\n"
        f"💰 Итого: €{cart_total(cart):.2f}\n"
        f"📦 {order_id}"
    )

    buttons = []
    if invoice_url:
        buttons.append([("🔗 Ссылка на оплату", "copy_invoice")])
    buttons.append([("🆕 Новый заказ", "new_order")])
    buttons.append([("↩️ Главное меню", "back_main")])

    kb = build_kb(buttons)
    context.user_data["last_invoice_url"] = invoice_url
    await query.edit_message_text(text, reply_markup=kb)
    return MAIN_MENU


def _parse_address(address_str: str, client: dict) -> dict:
    """Best-effort parse address string into Shopify format."""
    import re
    contact = client.get("contact_person", "")
    parts = contact.split(" ", 1) if contact else ["", ""]
    first = parts[0] if len(parts) > 0 else ""
    last = parts[1] if len(parts) > 1 else ""

    zip_match = re.search(r"\b(\d{5})\b", address_str)
    zip_code = zip_match.group(1) if zip_match else ""

    street = address_str
    city = "Berlin"
    if zip_code:
        after_zip = address_str.split(zip_code, 1)
        if len(after_zip) > 1 and after_zip[1].strip():
            city = after_zip[1].strip().strip(",").strip()
        street = address_str.split(zip_code)[0].strip().strip(",").strip()

    return {
        "firstName": first,
        "lastName": last,
        "company": client.get("name", ""),
        "address1": street,
        "city": city,
        "zip": zip_code,
        "countryCode": "DE",
    }


# ─── Navigation helpers ──────────────────────────────────────


async def _go_main_menu(query, context) -> int:
    rep = context.user_data.get("rep", {})
    name = rep.get("name", "менеджер")
    kb = build_kb([
        [("🆕 Новый заказ", "new_order")],
        [("📋 Мои заказы", "my_orders")],
    ])
    await query.edit_message_text(f"Привет, {name}! Что делаем?", reply_markup=kb)
    return MAIN_MENU


async def _go_main_menu_msg(query, context) -> int:
    """Same but sends new message (for after edit_message calls)."""
    rep = context.user_data.get("rep", {})
    name = rep.get("name", "менеджер")
    kb = build_kb([
        [("🆕 Новый заказ", "new_order")],
        [("📋 Мои заказы", "my_orders")],
    ])
    await query.message.reply_text(f"{name}, что дальше?", reply_markup=kb)
    return MAIN_MENU


# ─── Invoice URL handler ─────────────────────────────────────


async def invoice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "copy_invoice":
        url = context.user_data.get("last_invoice_url", "")
        if url:
            await query.message.reply_text(f"🔗 Ссылка на оплату:\n{url}")
        else:
            await query.message.reply_text("Ссылка недоступна.")
    return MAIN_MENU


# ─── Cancel ───────────────────────────────────────────────────


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Бот остановлен. Нажмите /start чтобы начать заново.")
    return ConversationHandler.END


# ─── Main ─────────────────────────────────────────────────────


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # back_main handler — added to every state
    back_main_handler = CallbackQueryHandler(global_back_main, pattern="^back_main$")

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_handler, pattern="^(new_order|my_orders)$"),
                CallbackQueryHandler(invoice_handler, pattern="^copy_invoice$"),
                back_main_handler,
            ],
            SEARCH_CLIENT: [
                CallbackQueryHandler(search_client_start, pattern="^search_client$"),
                CallbackQueryHandler(new_client_start, pattern="^new_client$"),
                back_main_handler,
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_client_input),
            ],
            SELECT_CLIENT: [
                CallbackQueryHandler(pick_client, pattern="^(pick_client_\\d+|search_client|new_client)$"),
                back_main_handler,
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_client_input),
            ],
            CLIENT_CARD: [
                CallbackQueryHandler(client_card_handler, pattern="^(start_order|search_client)$"),
                back_main_handler,
            ],
            SELECT_CATEGORY: [
                CallbackQueryHandler(category_handler, pattern="^(cat_|show_cart|back_to_client)"),
                back_main_handler,
            ],
            SELECT_PRODUCT: [
                CallbackQueryHandler(product_handler, pattern="^(prod_|back_categories)"),
                back_main_handler,
            ],
            SELECT_VARIANT: [
                CallbackQueryHandler(variant_handler, pattern="^(var_|back_products)"),
                back_main_handler,
            ],
            SELECT_QUANTITY: [
                CallbackQueryHandler(quantity_handler, pattern="^(qty_|back_variants)"),
                back_main_handler,
            ],
            CART: [
                CallbackQueryHandler(cart_handler, pattern="^(add_more|checkout|clear_cart)$"),
                back_main_handler,
            ],
            SELECT_ADDRESS: [
                CallbackQueryHandler(address_handler, pattern="^(addr_|back_cart)"),
                back_main_handler,
            ],
            ENTER_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_address),
            ],
            CONFIRM_ORDER: [
                CallbackQueryHandler(confirm_handler, pattern="^(place_order|edit_order|cancel_order)$"),
                CallbackQueryHandler(main_menu_handler, pattern="^new_order$"),
                back_main_handler,
            ],
            # New client states
            NEW_CLIENT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_client_name),
            ],
            NEW_CLIENT_CONTACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_client_contact),
            ],
            NEW_CLIENT_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_client_phone),
            ],
            NEW_CLIENT_EMAIL: [
                CallbackQueryHandler(new_client_email_skip, pattern="^skip_email$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_client_email_text),
            ],
            NEW_CLIENT_PRICE_GROUP: [
                CallbackQueryHandler(new_client_price_group, pattern="^pg_"),
            ],
            NEW_CLIENT_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_client_address),
            ],
            NEW_CLIENT_CONFIRM: [
                CallbackQueryHandler(new_client_confirm, pattern="^(save_client|redo_client|back_main)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(conv)

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
