"""
Telegram Order Bot — Sales Rep Flow (Simplified)

Flow: search/create client → pick product (buttons) → type quantity → type price → cart
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

# ─── States ───────────────────────────────────────────────────

(
    MAIN_MENU,
    SEARCH_CLIENT,
    SELECT_CLIENT,
    CLIENT_CARD,
    SELECT_PRODUCT,
    ENTER_QUANTITY,
    ENTER_PRICE,
    CART,
    SELECT_ADDRESS,
    ENTER_ADDR_STREET,
    ENTER_ADDR_ZIP,
    ENTER_ADDR_CITY,
    ENTER_ADDR_COUNTRY,
    CONFIRM_ORDER,
    NEW_CLIENT_NAME,
    NEW_CLIENT_CONTACT,
    NEW_CLIENT_PHONE,
    NEW_CLIENT_EMAIL,
    NEW_CLIENT_ADDRESS,
    NEW_CLIENT_CONFIRM,
) = range(20)


# ─── Helpers ──────────────────────────────────────────────────


def build_kb(buttons):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in buttons]
    )


def format_cart(cart):
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


def cart_total(cart):
    return sum(item["price"] * item["quantity"] for item in cart)


def order_items_summary(cart):
    return ", ".join(f"{item['display_name']} x{item['quantity']} @€{item['price']:.2f}" for item in cart)


# ─── Global back_main ────────────────────────────────────────


async def global_back_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    return await _go_main_menu(query, context)


# ─── /start ───────────────────────────────────────────────────


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tg_id = update.effective_user.id
    rep = sheets.get_sales_rep(tg_id)

    if not rep:
        await update.message.reply_text(
            f"⛔ Доступ запрещён. Ваш Telegram ID не найден.\nВаш ID: `{tg_id}`",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["rep"] = rep
    context.user_data["cart"] = []
    context.user_data["client"] = None

    name = rep.get("name", "менеджер")
    kb = build_kb([[("🆕 Новый заказ", "new_order")]])
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

    return MAIN_MENU


# ─── Client Search ────────────────────────────────────────────


async def search_client_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔍 Введите имя, телефон или компанию:")
    return SELECT_CLIENT


async def search_client_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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

    context.user_data["search_results"] = results
    buttons = []
    for i, c in enumerate(results[:10]):
        label = c.get("name", "?")
        addr = c.get("address_1", "")
        if addr:
            label += f" — {addr[:30]}"
        buttons.append([(label, f"pick_client_{i}")])
    buttons.append([("🔍 Ещё раз", "search_client"), ("➕ Новый", "new_client")])
    buttons.append([("↩️ Меню", "back_main")])

    await update.message.reply_text(f"Найдено ({len(results)}):", reply_markup=build_kb(buttons))
    return SELECT_CLIENT


async def pick_client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "search_client":
        await query.edit_message_text("🔍 Введите имя, телефон или компанию:")
        return SELECT_CLIENT
    if query.data == "new_client":
        context.user_data["new_client"] = {}
        await query.edit_message_text("➕ Название компании / имя клиента:")
        return NEW_CLIENT_NAME

    idx = int(query.data.replace("pick_client_", ""))
    results = context.user_data.get("search_results", [])
    if idx >= len(results):
        await query.edit_message_text("Ошибка. Попробуйте снова.")
        return SEARCH_CLIENT

    context.user_data["client"] = results[idx]
    return await _show_client_card(query, context)


async def _show_client_card(query, context) -> int:
    c = context.user_data["client"]
    text = (
        f"🏢 {c.get('name', '—')}\n"
        f"👤 {c.get('contact_person', '—')}\n"
        f"📞 {c.get('phone', '—')}\n"
        f"📍 {c.get('address_1', '—')}"
    )
    kb = build_kb([
        [("🆕 Собрать заказ", "start_order")],
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
        return await _show_products(query, context)
    if query.data == "search_client":
        await query.edit_message_text("🔍 Введите имя, телефон или компанию:")
        return SELECT_CLIENT

    return CLIENT_CARD


# ─── New Client ───────────────────────────────────────────────


async def new_client_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["new_client"] = {}
    await query.edit_message_text("➕ Название компании / имя клиента:")
    return NEW_CLIENT_NAME


async def nc_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_client"]["name"] = update.message.text.strip()
    await update.message.reply_text("👤 Контактное лицо (имя фамилия):")
    return NEW_CLIENT_CONTACT


async def nc_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_client"]["contact_person"] = update.message.text.strip()
    await update.message.reply_text("📞 Телефон:")
    return NEW_CLIENT_PHONE


async def nc_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_client"]["phone"] = update.message.text.strip()
    kb = build_kb([[("⏩ Пропустить", "skip_email")]])
    await update.message.reply_text("📧 Email:", reply_markup=kb)
    return NEW_CLIENT_EMAIL


async def nc_email_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_client"]["email"] = update.message.text.strip()
    await update.message.reply_text("📍 Адрес доставки:")
    return NEW_CLIENT_ADDRESS


async def nc_email_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["new_client"]["email"] = ""
    await query.edit_message_text("📍 Адрес доставки:")
    return NEW_CLIENT_ADDRESS


async def nc_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_client"]["address_1"] = update.message.text.strip()
    nc = context.user_data["new_client"]
    text = (
        f"📋 Новый клиент:\n\n"
        f"🏢 {nc.get('name')}\n"
        f"👤 {nc.get('contact_person')}\n"
        f"📞 {nc.get('phone')}\n"
        f"📧 {nc.get('email') or '—'}\n"
        f"📍 {nc.get('address_1')}\n\n"
        f"Всё верно?"
    )
    kb = build_kb([
        [("✅ Сохранить", "save_client")],
        [("✏️ Заново", "redo_client")],
        [("❌ Отмена", "back_main")],
    ])
    await update.message.reply_text(text, reply_markup=kb)
    return NEW_CLIENT_CONFIRM


async def nc_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "back_main":
        return await _go_main_menu(query, context)
    if query.data == "redo_client":
        context.user_data["new_client"] = {}
        await query.edit_message_text("➕ Название компании / имя клиента:")
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
            text = f"✅ Клиент «{nc.get('name')}» создан!\n\n🏢 {nc.get('name')}\n📍 {nc.get('address_1')}"
            kb = build_kb([
                [("🆕 Собрать заказ", "start_order")],
                [("↩️ Главное меню", "back_main")],
            ])
            await query.edit_message_text(text, reply_markup=kb)
            return CLIENT_CARD
        else:
            await query.edit_message_text("❌ Ошибка сохранения.")
            return await _go_main_menu_msg(query, context)

    return NEW_CLIENT_CONFIRM


# ─── Product Selection (from catalog) ────────────────────────


async def _show_products(query, context) -> int:
    """Show all products from catalog as buttons."""
    catalog = sheets.get_catalog()
    if not catalog:
        await query.edit_message_text("Каталог пуст. Заполните лист «Каталог» в Google Sheets.")
        return MAIN_MENU

    buttons = []
    for item in catalog:
        display = item.get("display_name", item.get("name", "?"))
        buttons.append([(display, f"prod_{item.get('product_id')}")])

    # Show cart if not empty
    cart = context.user_data.get("cart", [])
    if cart:
        buttons.append([("🛒 Корзина ({})".format(len(cart)), "show_cart")])
    buttons.append([("↩️ Назад", "back_to_client")])

    await query.edit_message_text("Выберите товар:", reply_markup=build_kb(buttons))
    return SELECT_PRODUCT


async def product_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "show_cart":
        return await _show_cart(query, context)
    if query.data == "back_to_client":
        client = context.user_data.get("client")
        if client:
            return await _show_client_card(query, context)
        return await _go_main_menu(query, context)

    product_id = query.data.replace("prod_", "")
    catalog = sheets.get_catalog()
    product = None
    for item in catalog:
        if str(item.get("product_id")) == str(product_id):
            product = item
            break

    if not product:
        await query.edit_message_text("Товар не найден.")
        return SELECT_PRODUCT

    context.user_data["current_product"] = product
    display = product.get("display_name", product.get("name", "?"))
    await query.edit_message_text(f"📦 {display}\n\nСколько штук/банок? (введите число)")
    return ENTER_QUANTITY


# ─── Quantity (free text) ─────────────────────────────────────


async def enter_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        qty = int(text)
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введите целое число больше 0:")
        return ENTER_QUANTITY

    context.user_data["current_qty"] = qty
    product = context.user_data.get("current_product", {})
    display = product.get("display_name", "?")
    await update.message.reply_text(f"📦 {display} × {qty}\n\n💶 Цена за штуку в евро? (например: 95.50)")
    return ENTER_PRICE


# ─── Price (free text) ───────────────────────────────────────


async def enter_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(",", ".")
    try:
        price = float(text)
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введите цену (число, например 95.50):")
        return ENTER_PRICE

    product = context.user_data.get("current_product", {})
    qty = context.user_data.get("current_qty", 1)

    cart_item = {
        "product_id": product.get("product_id"),
        "display_name": product.get("display_name", product.get("name", "?")),
        "shopify_variant_id": product.get("shopify_variant_id", ""),
        "price": price,
        "quantity": qty,
    }
    context.user_data.setdefault("cart", []).append(cart_item)

    return await _show_cart_msg(update, context)


async def _show_cart_msg(update, context) -> int:
    """Show cart as a new message (after text input)."""
    cart = context.user_data.get("cart", [])
    text = "🛒 Корзина:\n\n" + format_cart(cart)
    kb = build_kb([
        [("➕ Добавить ещё", "add_more")],
        [("🗑 Удалить последний", "remove_last")],
        [("✅ Оформить заказ", "checkout")],
        [("❌ Очистить всё", "clear_cart")],
    ])
    await update.message.reply_text(text, reply_markup=kb)
    return CART


# ─── Cart ─────────────────────────────────────────────────────


async def _show_cart(query, context) -> int:
    """Show cart by editing message (after button press)."""
    cart = context.user_data.get("cart", [])
    text = "🛒 Корзина:\n\n" + format_cart(cart)

    buttons = [
        [("➕ Добавить ещё", "add_more")],
    ]
    if cart:
        buttons.append([("🗑 Удалить последний", "remove_last")])
    buttons.append([("✅ Оформить заказ", "checkout")])
    buttons.append([("❌ Очистить всё", "clear_cart")])

    await query.edit_message_text(text, reply_markup=build_kb(buttons))
    return CART


async def cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "add_more":
        return await _show_products(query, context)

    if query.data == "remove_last":
        cart = context.user_data.get("cart", [])
        if cart:
            removed = cart.pop()
            context.user_data["cart"] = cart
        return await _show_cart(query, context)

    if query.data == "clear_cart":
        context.user_data["cart"] = []
        return await _show_cart(query, context)

    if query.data == "checkout":
        cart = context.user_data.get("cart", [])
        if not cart:
            await query.edit_message_text("Корзина пуста.")
            return await _show_products(query, context)
        return await _show_address_selection(query, context)

    return CART


# ─── Address Selection ────────────────────────────────────────


async def _show_address_selection(query, context) -> int:
    client = context.user_data.get("client", {})
    buttons = []

    addr1 = client.get("address_1", "")
    if addr1:
        short = addr1[:45] if len(addr1) > 45 else addr1
        buttons.append([(f"📍 {short}", "addr_1")])

    addr2 = client.get("address_2", "")
    if addr2:
        short = addr2[:45] if len(addr2) > 45 else addr2
        buttons.append([(f"📍 {short}", "addr_2")])

    buttons.append([("✏️ Ввести новый адрес", "addr_custom")])
    buttons.append([("↩️ Назад в корзину", "back_cart")])

    await query.edit_message_text("📍 Адрес доставки:", reply_markup=build_kb(buttons))
    return SELECT_ADDRESS


async def address_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "back_cart":
        return await _show_cart(query, context)

    client = context.user_data.get("client", {})

    if query.data == "addr_1":
        # Use saved address — store as structured dict
        context.user_data["shipping_address"] = {
            "address1": client.get("address_1", ""),
            "zip": "",
            "city": "Berlin",
            "countryCode": "DE",
        }
        context.user_data["address_display"] = client.get("address_1", "")
        return await _show_confirmation(query, context)

    if query.data == "addr_2":
        context.user_data["shipping_address"] = {
            "address1": client.get("address_2", ""),
            "zip": "",
            "city": "Berlin",
            "countryCode": "DE",
        }
        context.user_data["address_display"] = client.get("address_2", "")
        return await _show_confirmation(query, context)

    if query.data == "addr_custom":
        context.user_data["new_address"] = {}
        await query.edit_message_text("📍 Улица и номер дома:")
        return ENTER_ADDR_STREET

    return SELECT_ADDRESS


async def enter_addr_street(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_address"]["street"] = update.message.text.strip()
    await update.message.reply_text("📮 Почтовый индекс (PLZ):")
    return ENTER_ADDR_ZIP


async def enter_addr_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_address"]["zip"] = update.message.text.strip()
    kb = build_kb([
        [("Berlin", "city_Berlin")],
        [("✏️ Другой город", "city_other")],
    ])
    await update.message.reply_text("🏙 Город:", reply_markup=kb)
    return ENTER_ADDR_CITY


async def enter_addr_city_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "city_other":
        await query.edit_message_text("🏙 Введите название города:")
        return ENTER_ADDR_CITY  # Will be caught by text handler

    city = query.data.replace("city_", "")
    context.user_data["new_address"]["city"] = city

    kb = build_kb([
        [("🇩🇪 Германия", "country_DE")],
        [("🇦🇹 Австрия", "country_AT"), ("🇨🇭 Швейцария", "country_CH")],
        [("✏️ Другая", "country_other")],
    ])
    await query.edit_message_text("🌍 Страна:", reply_markup=kb)
    return ENTER_ADDR_COUNTRY


async def enter_addr_city_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_address"]["city"] = update.message.text.strip()
    kb = build_kb([
        [("🇩🇪 Германия", "country_DE")],
        [("🇦🇹 Австрия", "country_AT"), ("🇨🇭 Швейцария", "country_CH")],
        [("✏️ Другая", "country_other")],
    ])
    await update.message.reply_text("🌍 Страна:", reply_markup=kb)
    return ENTER_ADDR_COUNTRY


async def enter_addr_country(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "country_other":
        await query.edit_message_text("🌍 Введите код страны (2 буквы, например: DE, AT, CH, PL):")
        return ENTER_ADDR_COUNTRY  # Will be caught by text handler

    country = query.data.replace("country_", "")
    return await _finalize_address(query, context, country, is_callback=True)


async def enter_addr_country_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    country = update.message.text.strip().upper()[:2]
    return await _finalize_address(update, context, country, is_callback=False)


async def _finalize_address(update_or_query, context, country_code, is_callback=True):
    addr = context.user_data["new_address"]
    addr["country"] = country_code

    context.user_data["shipping_address"] = {
        "address1": addr["street"],
        "zip": addr["zip"],
        "city": addr["city"],
        "countryCode": country_code,
    }
    display = f"{addr['street']}, {addr['zip']} {addr['city']}, {country_code}"
    context.user_data["address_display"] = display

    cart = context.user_data.get("cart", [])
    client = context.user_data.get("client", {})
    text = _build_confirmation_text(client, cart, display)
    kb = build_kb([
        [("✅ Создать заказ", "place_order")],
        [("✏️ Редактировать", "edit_order")],
        [("❌ Отменить", "cancel_order")],
    ])

    if is_callback:
        await update_or_query.edit_message_text(text, reply_markup=kb)
    else:
        await update_or_query.message.reply_text(text, reply_markup=kb)
    return CONFIRM_ORDER


# ─── Confirmation ─────────────────────────────────────────────


def _build_confirmation_text(client, cart, address_display):
    name = client.get("name", "—")
    lines = [f"📦 Заказ для {name}:\n"]
    total = 0.0
    for i, item in enumerate(cart, 1):
        subtotal = item["price"] * item["quantity"]
        total += subtotal
        lines.append(f"{i}. {item['display_name']} × {item['quantity']} — €{subtotal:.2f} (€{item['price']:.2f}/шт)")
    lines.append(f"\n📍 {address_display}")
    lines.append(f"💰 Итого: €{total:.2f}")
    return "\n".join(lines)


async def _show_confirmation(query, context) -> int:
    cart = context.user_data.get("cart", [])
    client = context.user_data.get("client", {})
    address_display = context.user_data.get("address_display", "—")

    text = _build_confirmation_text(client, cart, address_display)
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
        await query.edit_message_text("⏳ Создаю заказ...")
        return await _create_order(query, context)

    return CONFIRM_ORDER


# ─── Create Order ─────────────────────────────────────────────


async def _create_order(query, context) -> int:
    cart = context.user_data.get("cart", [])
    client = context.user_data.get("client", {})
    address_display = context.user_data.get("address_display", "")
    rep = context.user_data.get("rep", {})
    rep_name = rep.get("name", "?")

    # Build Shopify line items
    line_items = []
    for item in cart:
        variant_id = item.get("shopify_variant_id", "")
        if variant_id and variant_id.startswith("gid://"):
            li = {
                "variant_id": variant_id,
                "quantity": item["quantity"],
                "applied_discount": {
                    "title": "Custom price",
                    "value": str(item["price"]),
                    "valueType": "FIXED_AMOUNT",
                    "description": f"Set by {rep_name}",
                },
                "custom_price": item["price"],
            }
        else:
            li = {
                "title": item.get("display_name", "Product"),
                "quantity": item["quantity"],
                "custom_price": item["price"],
            }
        line_items.append(li)

    # Build shipping address — use structured data directly
    addr_data = context.user_data.get("shipping_address", {})
    contact = client.get("contact_person", "")
    parts = contact.split(" ", 1) if contact else ["", ""]
    shipping_address = {
        "firstName": parts[0] if parts else "",
        "lastName": parts[1] if len(parts) > 1 else "",
        "company": client.get("name", ""),
        "address1": addr_data.get("address1", address_display),
        "city": addr_data.get("city", "Berlin"),
        "zip": addr_data.get("zip", ""),
        "countryCode": addr_data.get("countryCode", "DE"),
    }

    customer_id = client.get("shopify_customer_id", "")
    note = f"Sales rep: {rep_name} | Telegram bot order"

    result = await shopify.create_draft_order(
        customer_id=customer_id or None,
        line_items=line_items,
        shipping_address=shipping_address,
        note=note,
        tags=["telegram-bot"],
    )

    shopify_ok = not result.get("error")

    # Save to Sheets regardless
    order_id = sheets.get_next_order_id()
    order_data = {
        "order_id": order_id,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "client_id": client.get("client_id", ""),
        "client_name": client.get("name", ""),
        "items": order_items_summary(cart),
        "total": f"{cart_total(cart):.2f}",
        "address": address_display,
        "sales_rep": rep_name,
        "shopify_draft_id": result.get("id", "") if shopify_ok else "",
        "shopify_invoice_url": result.get("invoiceUrl", "") if shopify_ok else "",
        "status": "draft" if shopify_ok else "saved",
    }
    sheets.save_order(order_data)

    sheets.update_client_after_order(
        client_id=client.get("client_id", ""),
        order_summary=order_items_summary(cart),
    )

    if shopify_ok:
        shopify_id = result.get("name", "")
        invoice_url = result.get("invoiceUrl", "")
        text = (
            f"✅ Заказ создан!\n\n"
            f"📋 Shopify: {shopify_id}\n"
            f"📦 {order_id}\n"
            f"💰 €{cart_total(cart):.2f}"
        )
        context.user_data["last_invoice_url"] = invoice_url
        buttons = []
        if invoice_url:
            buttons.append([("🔗 Ссылка на оплату", "copy_invoice")])
        buttons.append([("🆕 Новый заказ", "new_order")])
        buttons.append([("↩️ Главное меню", "back_main")])
    else:
        text = (
            f"✅ Заказ сохранён в таблицу!\n\n"
            f"📦 {order_id}\n"
            f"💰 €{cart_total(cart):.2f}\n\n"
            f"⚠️ Shopify: {result.get('error', 'не настроен')}"
        )
        buttons = [
            [("🆕 Новый заказ", "new_order")],
            [("↩️ Главное меню", "back_main")],
        ]

    await query.edit_message_text(text, reply_markup=build_kb(buttons))
    return MAIN_MENU



# ─── Navigation ──────────────────────────────────────────────


async def _go_main_menu(query, context) -> int:
    name = context.user_data.get("rep", {}).get("name", "менеджер")
    kb = build_kb([[("🆕 Новый заказ", "new_order")]])
    await query.edit_message_text(f"{name}, что делаем?", reply_markup=kb)
    return MAIN_MENU


async def _go_main_menu_msg(query, context) -> int:
    name = context.user_data.get("rep", {}).get("name", "менеджер")
    kb = build_kb([[("🆕 Новый заказ", "new_order")]])
    await query.message.reply_text(f"{name}, что дальше?", reply_markup=kb)
    return MAIN_MENU


async def invoice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    url = context.user_data.get("last_invoice_url", "")
    if url:
        await query.message.reply_text(f"🔗 {url}")
    else:
        await query.message.reply_text("Ссылка недоступна.")
    return MAIN_MENU


# ─── Cancel ───────────────────────────────────────────────────


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Нажмите /start чтобы начать заново.")
    return ConversationHandler.END


# ─── Main ─────────────────────────────────────────────────────


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    back = CallbackQueryHandler(global_back_main, pattern="^back_main$")

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_handler, pattern="^new_order$"),
                CallbackQueryHandler(invoice_handler, pattern="^copy_invoice$"),
                back,
            ],
            SEARCH_CLIENT: [
                CallbackQueryHandler(search_client_start, pattern="^search_client$"),
                CallbackQueryHandler(new_client_start, pattern="^new_client$"),
                back,
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_client_input),
            ],
            SELECT_CLIENT: [
                CallbackQueryHandler(pick_client, pattern="^(pick_client_\\d+|search_client|new_client)$"),
                back,
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_client_input),
            ],
            CLIENT_CARD: [
                CallbackQueryHandler(client_card_handler, pattern="^(start_order|search_client)$"),
                back,
            ],
            SELECT_PRODUCT: [
                CallbackQueryHandler(product_handler, pattern="^(prod_|show_cart|back_to_client)"),
                back,
            ],
            ENTER_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_quantity),
            ],
            ENTER_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_price),
            ],
            CART: [
                CallbackQueryHandler(cart_handler, pattern="^(add_more|remove_last|checkout|clear_cart)$"),
                back,
            ],
            SELECT_ADDRESS: [
                CallbackQueryHandler(address_handler, pattern="^(addr_|back_cart)"),
                back,
            ],
            ENTER_ADDR_STREET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_addr_street),
            ],
            ENTER_ADDR_ZIP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_addr_zip),
            ],
            ENTER_ADDR_CITY: [
                CallbackQueryHandler(enter_addr_city_btn, pattern="^city_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_addr_city_text),
            ],
            ENTER_ADDR_COUNTRY: [
                CallbackQueryHandler(enter_addr_country, pattern="^country_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_addr_country_text),
            ],
            CONFIRM_ORDER: [
                CallbackQueryHandler(confirm_handler, pattern="^(place_order|edit_order|cancel_order)$"),
                back,
            ],
            NEW_CLIENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_name)],
            NEW_CLIENT_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_contact)],
            NEW_CLIENT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_phone)],
            NEW_CLIENT_EMAIL: [
                CallbackQueryHandler(nc_email_skip, pattern="^skip_email$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, nc_email_text),
            ],
            NEW_CLIENT_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_address)],
            NEW_CLIENT_CONFIRM: [
                CallbackQueryHandler(nc_confirm, pattern="^(save_client|redo_client|back_main)$"),
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
       
