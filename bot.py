"""
Telegram Order Bot — Sales Rep Flow v2

Changes from v1:
- ↩️ Back button on EVERY step (including text input steps)
- New client: structured address (street → PLZ → city → country)
- After shipping address → "Invoice address same?" → if no → enter billing address
- Billing address passed to Shopify separately
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
    # Shipping address
    SELECT_ADDRESS,
    SHIP_STREET,
    SHIP_ZIP,
    SHIP_CITY,
    SHIP_COUNTRY,
    # Invoice address
    INVOICE_SAME,
    BILL_STREET,
    BILL_ZIP,
    BILL_CITY,
    BILL_COUNTRY,
    # Confirm + create
    CONFIRM_ORDER,
    # New client
    NC_NAME,
    NC_CONTACT,
    NC_PHONE,
    NC_EMAIL,
    NC_ADDR_STREET,
    NC_ADDR_ZIP,
    NC_ADDR_CITY,
    NC_ADDR_COUNTRY,
    NC_CONFIRM,
) = range(28)


# ─── Helpers ──────────────────────────────────────────────────


def kb(buttons):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in buttons]
    )


def back_btn(cb="back_main"):
    """Single back button row."""
    return [("↩️ Назад", cb)]


def format_cart(cart):
    if not cart:
        return "Корзина пуста."
    lines = []
    total = 0.0
    for i, item in enumerate(cart, 1):
        sub = item["price"] * item["quantity"]
        total += sub
        lines.append(f"{i}. {item['display_name']} × {item['quantity']} — €{sub:.2f} (€{item['price']:.2f}/шт)")
    lines.append(f"\n💰 Итого: €{total:.2f}")
    return "\n".join(lines)


def cart_total(cart):
    return sum(i["price"] * i["quantity"] for i in cart)


def order_items_summary(cart):
    return ", ".join(f"{i['display_name']} x{i['quantity']} @€{i['price']:.2f}" for i in cart)


def fmt_addr(a):
    """Format address dict for display."""
    return f"{a.get('street','')}, {a.get('zip','')} {a.get('city','')}, {a.get('country','DE')}"


# ─── Universal back handler ──────────────────────────────────


async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Universal callback handler for all back_* buttons."""
    q = update.callback_query
    await q.answer()
    target = q.data

    if target == "back_main":
        return await _main_menu(q, context)
    if target == "back_search":
        k = kb([[("🔍 Поиск клиента", "search_client")], [("➕ Новый клиент", "new_client")], back_btn()])
        await q.edit_message_text("Для кого заказ?", reply_markup=k)
        return SEARCH_CLIENT
    if target == "back_client":
        return await _client_card(q, context)
    if target == "back_products":
        return await _products(q, context)
    if target == "back_cart":
        return await _cart(q, context)
    if target == "back_addr":
        return await _addr_select(q, context)
    if target == "back_invoice_q":
        return await _invoice_question(q, context)

    return await _main_menu(q, context)


# ─── /start ───────────────────────────────────────────────────


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tg_id = update.effective_user.id
    rep = sheets.get_sales_rep(tg_id)
    if not rep:
        await update.message.reply_text(
            f"⛔ Доступ запрещён.\nВаш ID: `{tg_id}`", parse_mode="Markdown")
        return ConversationHandler.END
    context.user_data.update({"rep": rep, "cart": [], "client": None})
    name = rep.get("name", "менеджер")
    await update.message.reply_text(f"Привет, {name}!", reply_markup=kb([[("🆕 Новый заказ", "new_order")]]))
    return MAIN_MENU


async def _main_menu(q, ctx):
    n = ctx.user_data.get("rep", {}).get("name", "менеджер")
    await q.edit_message_text(f"{n}, что делаем?", reply_markup=kb([[("🆕 Новый заказ", "new_order")]]))
    return MAIN_MENU


# ─── Main Menu ────────────────────────────────────────────────


async def main_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "new_order":
        context.user_data.update({"cart": [], "client": None, "shipping": None, "billing": None})
        await q.edit_message_text("Для кого заказ?", reply_markup=kb([
            [("🔍 Поиск клиента", "search_client")],
            [("➕ Новый клиент", "new_client")],
        ]))
        return SEARCH_CLIENT
    if q.data == "copy_invoice":
        url = context.user_data.get("last_invoice_url", "")
        await q.message.reply_text(f"🔗 {url}" if url else "Ссылка недоступна.")
        return MAIN_MENU
    return MAIN_MENU


# ═══════════════════════════════════════════════════════════════
#  CLIENT SEARCH
# ═══════════════════════════════════════════════════════════════


async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("🔍 Введите имя, телефон или компанию:\n", reply_markup=kb([back_btn()]))
    return SELECT_CLIENT


async def search_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    results = sheets.search_clients(txt)
    if not results:
        await update.message.reply_text("Ничего не найдено.", reply_markup=kb([
            [("🔍 Искать снова", "search_client")], [("➕ Новый клиент", "new_client")], back_btn()]))
        return SEARCH_CLIENT
    context.user_data["search_results"] = results
    btns = []
    for i, c in enumerate(results[:10]):
        lbl = c.get("name", "?")
        a = c.get("address_1", "")
        if a:
            lbl += f" — {a[:25]}"
        btns.append([(lbl, f"pick_{i}")])
    btns += [[("🔍 Ещё раз", "search_client"), ("➕ Новый", "new_client")], back_btn()]
    await update.message.reply_text(f"Найдено ({len(results)}):", reply_markup=kb(btns))
    return SELECT_CLIENT


async def pick_client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "search_client":
        await q.edit_message_text("🔍 Введите имя, телефон или компанию:", reply_markup=kb([back_btn()]))
        return SELECT_CLIENT
    if q.data == "new_client":
        context.user_data["nc"] = {}
        await q.edit_message_text("➕ Название компании / имя клиента:", reply_markup=kb([back_btn("back_search")]))
        return NC_NAME
    idx = int(q.data.replace("pick_", ""))
    res = context.user_data.get("search_results", [])
    if idx >= len(res):
        await q.edit_message_text("Ошибка.")
        return SEARCH_CLIENT
    context.user_data["client"] = res[idx]
    return await _client_card(q, context)


async def _client_card(q, ctx):
    c = ctx.user_data["client"]
    txt = (f"🏢 {c.get('name','—')}\n👤 {c.get('contact_person','—')}\n"
           f"📞 {c.get('phone','—')}\n📧 {c.get('email','—')}\n📍 {c.get('address_1','—')}")
    await q.edit_message_text(txt, reply_markup=kb([
        [("🆕 Собрать заказ", "start_order")],
        [("🔍 Другой клиент", "search_client")],
        back_btn()]))
    return CLIENT_CARD


async def client_card_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "start_order":
        context.user_data["cart"] = []
        return await _products(q, context)
    if q.data == "search_client":
        await q.edit_message_text("🔍 Введите имя, телефон или компанию:", reply_markup=kb([back_btn()]))
        return SELECT_CLIENT
    return CLIENT_CARD


# ═══════════════════════════════════════════════════════════════
#  NEW CLIENT (with structured address)
# ═══════════════════════════════════════════════════════════════


async def nc_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data["nc"] = {}
    await q.edit_message_text("➕ Название компании / имя клиента:", reply_markup=kb([back_btn("back_search")]))
    return NC_NAME


async def nc_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["nc"]["name"] = update.message.text.strip()
    await update.message.reply_text("👤 Контактное лицо:", reply_markup=kb([back_btn("back_search")]))
    return NC_CONTACT


async def nc_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["nc"]["contact_person"] = update.message.text.strip()
    await update.message.reply_text("📞 Телефон:", reply_markup=kb([back_btn("back_search")]))
    return NC_PHONE


async def nc_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["nc"]["phone"] = update.message.text.strip()
    await update.message.reply_text("📧 Email:", reply_markup=kb([[("⏩ Пропустить", "skip_email")], back_btn("back_search")]))
    return NC_EMAIL


async def nc_email_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["nc"]["email"] = update.message.text.strip()
    await update.message.reply_text("📍 Улица и номер дома:", reply_markup=kb([back_btn("back_search")]))
    return NC_ADDR_STREET


async def nc_email_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data["nc"]["email"] = ""
    await q.edit_message_text("📍 Улица и номер дома:", reply_markup=kb([back_btn("back_search")]))
    return NC_ADDR_STREET


async def nc_addr_street(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["nc"]["street"] = update.message.text.strip()
    await update.message.reply_text("📮 Почтовый индекс (PLZ):", reply_markup=kb([back_btn("back_search")]))
    return NC_ADDR_ZIP


async def nc_addr_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["nc"]["zip"] = update.message.text.strip()
    await update.message.reply_text("🏙 Город:", reply_markup=kb([
        [("Berlin", "nccity_Berlin")], [("✏️ Другой", "nccity_other")], back_btn("back_search")]))
    return NC_ADDR_CITY


async def nc_addr_city_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "nccity_other":
        await q.edit_message_text("🏙 Введите город:", reply_markup=kb([back_btn("back_search")]))
        return NC_ADDR_CITY
    context.user_data["nc"]["city"] = q.data.replace("nccity_", "")
    await q.edit_message_text("🌍 Страна:", reply_markup=kb([
        [("🇩🇪 DE", "nccountry_DE")], [("🇦🇹 AT", "nccountry_AT"), ("🇨🇭 CH", "nccountry_CH")],
        [("✏️ Другая", "nccountry_other")], back_btn("back_search")]))
    return NC_ADDR_COUNTRY


async def nc_addr_city_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["nc"]["city"] = update.message.text.strip()
    await update.message.reply_text("🌍 Страна:", reply_markup=kb([
        [("🇩🇪 DE", "nccountry_DE")], [("🇦🇹 AT", "nccountry_AT"), ("🇨🇭 CH", "nccountry_CH")],
        [("✏️ Другая", "nccountry_other")], back_btn("back_search")]))
    return NC_ADDR_COUNTRY


async def nc_addr_country_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "nccountry_other":
        await q.edit_message_text("🌍 Код страны (2 буквы: DE, AT, CH…):", reply_markup=kb([back_btn("back_search")]))
        return NC_ADDR_COUNTRY
    context.user_data["nc"]["country"] = q.data.replace("nccountry_", "")
    return await _nc_show_confirm(q, context, is_cb=True)


async def nc_addr_country_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["nc"]["country"] = update.message.text.strip().upper()[:2]
    return await _nc_show_confirm(update, context, is_cb=False)


async def _nc_show_confirm(src, ctx, is_cb):
    nc = ctx.user_data["nc"]
    addr = f"{nc.get('street','')}, {nc.get('zip','')} {nc.get('city','')}, {nc.get('country','DE')}"
    txt = (f"📋 Новый клиент:\n\n🏢 {nc.get('name')}\n👤 {nc.get('contact_person')}\n"
           f"📞 {nc.get('phone')}\n📧 {nc.get('email') or '—'}\n📍 {addr}\n\nВсё верно?")
    btns = kb([[("✅ Сохранить", "save_client")], [("✏️ Заново", "redo_client")], back_btn()])
    if is_cb:
        await src.edit_message_text(txt, reply_markup=btns)
    else:
        await src.message.reply_text(txt, reply_markup=btns)
    return NC_CONFIRM


async def nc_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "redo_client":
        context.user_data["nc"] = {}
        await q.edit_message_text("➕ Название компании:", reply_markup=kb([back_btn("back_search")]))
        return NC_NAME
    if q.data == "save_client":
        nc = context.user_data["nc"]
        cid = sheets.get_next_client_id()
        addr_str = f"{nc.get('street','')}, {nc.get('zip','')} {nc.get('city','')}, {nc.get('country','DE')}"
        data = {
            "client_id": cid, "name": nc.get("name",""), "contact_person": nc.get("contact_person",""),
            "phone": nc.get("phone",""), "email": nc.get("email",""), "telegram_id": "",
            "address_1": addr_str, "address_2": "", "address_label_1": "", "address_label_2": "",
            "notes": "", "shopify_customer_id": "", "usual_order": "", "last_order_date": "",
        }
        result = sheets.create_client(data)
        if result:
            # Also store structured address for later use
            data["_addr_structured"] = {
                "street": nc.get("street",""), "zip": nc.get("zip",""),
                "city": nc.get("city",""), "country": nc.get("country","DE"),
            }
            context.user_data["client"] = data
            await q.edit_message_text(f"✅ Клиент «{nc.get('name')}» создан!", reply_markup=kb([
                [("🆕 Собрать заказ", "start_order")], back_btn()]))
            return CLIENT_CARD
        await q.edit_message_text("❌ Ошибка сохранения.")
        return MAIN_MENU
    return NC_CONFIRM


# ═══════════════════════════════════════════════════════════════
#  CATALOG → QUANTITY → PRICE → CART
# ═══════════════════════════════════════════════════════════════


async def _products(q, ctx):
    cat = sheets.get_catalog()
    if not cat:
        await q.edit_message_text("Каталог пуст.")
        return MAIN_MENU
    btns = [[(it.get("display_name", it.get("name","?")), f"prod_{it.get('product_id')}")] for it in cat]
    cart = ctx.user_data.get("cart", [])
    if cart:
        btns.append([("🛒 Корзина ({})".format(len(cart)), "show_cart")])
    btns.append(back_btn("back_client"))
    await q.edit_message_text("Выберите товар:", reply_markup=kb(btns))
    return SELECT_PRODUCT


async def product_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "show_cart":
        return await _cart(q, context)
    pid = q.data.replace("prod_", "")
    cat = sheets.get_catalog()
    prod = next((x for x in cat if str(x.get("product_id")) == pid), None)
    if not prod:
        await q.edit_message_text("Не найден.")
        return SELECT_PRODUCT
    context.user_data["cur_prod"] = prod
    name = prod.get("display_name", prod.get("name", "?"))
    await q.edit_message_text(f"📦 {name}\n\nСколько штук? (введите число)",
                              reply_markup=kb([back_btn("back_products")]))
    return ENTER_QUANTITY


async def enter_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    try:
        qty = int(txt)
        assert qty > 0
    except (ValueError, AssertionError):
        await update.message.reply_text("❌ Введите число > 0:", reply_markup=kb([back_btn("back_products")]))
        return ENTER_QUANTITY
    context.user_data["cur_qty"] = qty
    p = context.user_data["cur_prod"]
    await update.message.reply_text(
        f"📦 {p.get('display_name','?')} × {qty}\n\n💶 Цена за штуку (€)?",
        reply_markup=kb([back_btn("back_products")]))
    return ENTER_PRICE


async def enter_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip().replace(",", ".")
    try:
        price = float(txt)
        assert price > 0
    except (ValueError, AssertionError):
        await update.message.reply_text("❌ Введите цену:", reply_markup=kb([back_btn("back_products")]))
        return ENTER_PRICE
    p = context.user_data["cur_prod"]
    context.user_data.setdefault("cart", []).append({
        "product_id": p.get("product_id"),
        "display_name": p.get("display_name", p.get("name", "?")),
        "shopify_variant_id": p.get("shopify_variant_id", ""),
        "price": price, "quantity": context.user_data["cur_qty"],
    })
    # Show cart as new message (after text input)
    cart = context.user_data["cart"]
    await update.message.reply_text("🛒 Корзина:\n\n" + format_cart(cart), reply_markup=kb([
        [("➕ Добавить ещё", "add_more")],
        [("🗑 Удалить последний", "remove_last")],
        [("✅ Оформить", "checkout")],
        [("❌ Очистить", "clear_cart")],
    ]))
    return CART


# ─── Cart ─────────────────────────────────────────────────────


async def _cart(q, ctx):
    cart = ctx.user_data.get("cart", [])
    btns = [[("➕ Добавить ещё", "add_more")]]
    if cart:
        btns.append([("🗑 Удалить последний", "remove_last")])
    btns += [[("✅ Оформить", "checkout")], [("❌ Очистить", "clear_cart")], back_btn("back_products")]
    await q.edit_message_text("🛒 Корзина:\n\n" + format_cart(cart), reply_markup=kb(btns))
    return CART


async def cart_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "add_more":
        return await _products(q, context)
    if q.data == "remove_last":
        cart = context.user_data.get("cart", [])
        if cart:
            cart.pop()
        return await _cart(q, context)
    if q.data == "clear_cart":
        context.user_data["cart"] = []
        return await _cart(q, context)
    if q.data == "checkout":
        if not context.user_data.get("cart"):
            return await _products(q, context)
        return await _addr_select(q, context)
    return CART


# ═══════════════════════════════════════════════════════════════
#  SHIPPING ADDRESS (step-by-step)
# ═══════════════════════════════════════════════════════════════


async def _addr_select(q, ctx):
    c = ctx.user_data.get("client", {})
    btns = []
    a1 = c.get("address_1", "")
    if a1:
        btns.append([(f"📍 {a1[:40]}", "addr_saved_1")])
    a2 = c.get("address_2", "")
    if a2:
        btns.append([(f"📍 {a2[:40]}", "addr_saved_2")])
    btns += [[("✏️ Новый адрес", "addr_new")], back_btn("back_cart")]
    await q.edit_message_text("📍 Адрес доставки:", reply_markup=kb(btns))
    return SELECT_ADDRESS


async def addr_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    c = context.user_data.get("client", {})
    if q.data == "addr_saved_1":
        context.user_data["shipping"] = {"street": c.get("address_1",""), "zip": "", "city": "Berlin", "country": "DE"}
        return await _invoice_question(q, context)
    if q.data == "addr_saved_2":
        context.user_data["shipping"] = {"street": c.get("address_2",""), "zip": "", "city": "Berlin", "country": "DE"}
        return await _invoice_question(q, context)
    if q.data == "addr_new":
        context.user_data["_addr_target"] = "shipping"
        await q.edit_message_text("📍 Улица и номер дома:", reply_markup=kb([back_btn("back_addr")]))
        return SHIP_STREET
    return SELECT_ADDRESS


async def ship_street(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("_ship_tmp", {})["street"] = update.message.text.strip()
    await update.message.reply_text("📮 PLZ:", reply_markup=kb([back_btn("back_addr")]))
    return SHIP_ZIP


async def ship_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["_ship_tmp"]["zip"] = update.message.text.strip()
    await update.message.reply_text("🏙 Город:", reply_markup=kb([
        [("Berlin", "shipcity_Berlin")], [("✏️ Другой", "shipcity_other")], back_btn("back_addr")]))
    return SHIP_CITY


async def ship_city_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "shipcity_other":
        await q.edit_message_text("🏙 Город:", reply_markup=kb([back_btn("back_addr")]))
        return SHIP_CITY
    context.user_data["_ship_tmp"]["city"] = q.data.replace("shipcity_", "")
    await q.edit_message_text("🌍 Страна:", reply_markup=kb([
        [("🇩🇪 DE", "shipcountry_DE")], [("🇦🇹 AT", "shipcountry_AT"), ("🇨🇭 CH", "shipcountry_CH")],
        [("✏️ Другая", "shipcountry_other")], back_btn("back_addr")]))
    return SHIP_COUNTRY


async def ship_city_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["_ship_tmp"]["city"] = update.message.text.strip()
    await update.message.reply_text("🌍 Страна:", reply_markup=kb([
        [("🇩🇪 DE", "shipcountry_DE")], [("🇦🇹 AT", "shipcountry_AT"), ("🇨🇭 CH", "shipcountry_CH")],
        [("✏️ Другая", "shipcountry_other")], back_btn("back_addr")]))
    return SHIP_COUNTRY


async def ship_country_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "shipcountry_other":
        await q.edit_message_text("🌍 Код страны (DE, AT, CH…):", reply_markup=kb([back_btn("back_addr")]))
        return SHIP_COUNTRY
    context.user_data["_ship_tmp"]["country"] = q.data.replace("shipcountry_", "")
    context.user_data["shipping"] = dict(context.user_data["_ship_tmp"])
    return await _invoice_question(q, context)


async def ship_country_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["_ship_tmp"]["country"] = update.message.text.strip().upper()[:2]
    context.user_data["shipping"] = dict(context.user_data["_ship_tmp"])
    # Need to show invoice question as new msg
    ship = context.user_data["shipping"]
    await update.message.reply_text(
        f"📍 Доставка: {fmt_addr(ship)}\n\n🧾 Адрес для счёта (Rechnungsadresse) такой же?",
        reply_markup=kb([[("✅ Да, такой же", "invoice_same")], [("✏️ Нет, другой", "invoice_diff")], back_btn("back_addr")]))
    return INVOICE_SAME


# ═══════════════════════════════════════════════════════════════
#  INVOICE ADDRESS QUESTION
# ═══════════════════════════════════════════════════════════════


async def _invoice_question(q, ctx):
    ship = ctx.user_data.get("shipping", {})
    await q.edit_message_text(
        f"📍 Доставка: {fmt_addr(ship)}\n\n🧾 Адрес для счёта (Rechnungsadresse) такой же?",
        reply_markup=kb([[("✅ Да, такой же", "invoice_same")], [("✏️ Нет, другой", "invoice_diff")], back_btn("back_addr")]))
    return INVOICE_SAME


async def invoice_same_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "invoice_same":
        context.user_data["billing"] = dict(context.user_data.get("shipping", {}))
        return await _confirm(q, context)
    if q.data == "invoice_diff":
        context.user_data["_bill_tmp"] = {}
        await q.edit_message_text("🧾 Улица и номер дома (для счёта):", reply_markup=kb([back_btn("back_invoice_q")]))
        return BILL_STREET
    return INVOICE_SAME


# ─── Billing address steps ────────────────────────────────────


async def bill_street(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("_bill_tmp", {})["street"] = update.message.text.strip()
    await update.message.reply_text("📮 PLZ (счёт):", reply_markup=kb([back_btn("back_invoice_q")]))
    return BILL_ZIP


async def bill_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["_bill_tmp"]["zip"] = update.message.text.strip()
    await update.message.reply_text("🏙 Город (счёт):", reply_markup=kb([
        [("Berlin", "billcity_Berlin")], [("✏️ Другой", "billcity_other")], back_btn("back_invoice_q")]))
    return BILL_CITY


async def bill_city_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "billcity_other":
        await q.edit_message_text("🏙 Город (счёт):", reply_markup=kb([back_btn("back_invoice_q")]))
        return BILL_CITY
    context.user_data["_bill_tmp"]["city"] = q.data.replace("billcity_", "")
    await q.edit_message_text("🌍 Страна (счёт):", reply_markup=kb([
        [("🇩🇪 DE", "billcountry_DE")], [("🇦🇹 AT", "billcountry_AT"), ("🇨🇭 CH", "billcountry_CH")],
        [("✏️ Другая", "billcountry_other")], back_btn("back_invoice_q")]))
    return BILL_COUNTRY


async def bill_city_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["_bill_tmp"]["city"] = update.message.text.strip()
    await update.message.reply_text("🌍 Страна (счёт):", reply_markup=kb([
        [("🇩🇪 DE", "billcountry_DE")], [("🇦🇹 AT", "billcountry_AT"), ("🇨🇭 CH", "billcountry_CH")],
        [("✏️ Другая", "billcountry_other")], back_btn("back_invoice_q")]))
    return BILL_COUNTRY


async def bill_country_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "billcountry_other":
        await q.edit_message_text("🌍 Код страны (счёт):", reply_markup=kb([back_btn("back_invoice_q")]))
        return BILL_COUNTRY
    context.user_data["_bill_tmp"]["country"] = q.data.replace("billcountry_", "")
    context.user_data["billing"] = dict(context.user_data["_bill_tmp"])
    return await _confirm(q, context)


async def bill_country_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["_bill_tmp"]["country"] = update.message.text.strip().upper()[:2]
    context.user_data["billing"] = dict(context.user_data["_bill_tmp"])
    # Show confirmation as new message
    cart = context.user_data.get("cart", [])
    client = context.user_data.get("client", {})
    ship = context.user_data.get("shipping", {})
    bill = context.user_data.get("billing", {})
    txt = _confirm_text(client, cart, ship, bill)
    await update.message.reply_text(txt, reply_markup=kb([
        [("✅ Создать заказ", "place_order")],
        [("✏️ Редактировать", "edit_order")],
        [("❌ Отменить", "cancel_order")],
    ]))
    return CONFIRM_ORDER


# ═══════════════════════════════════════════════════════════════
#  CONFIRMATION
# ═══════════════════════════════════════════════════════════════


def _confirm_text(client, cart, ship, bill):
    name = client.get("name", "—")
    lines = [f"📦 Заказ для {name}:\n"]
    total = 0.0
    for i, item in enumerate(cart, 1):
        sub = item["price"] * item["quantity"]
        total += sub
        lines.append(f"{i}. {item['display_name']} × {item['quantity']} — €{sub:.2f}")
    lines.append(f"\n📍 Доставка: {fmt_addr(ship)}")
    if ship != bill:
        lines.append(f"🧾 Счёт: {fmt_addr(bill)}")
    lines.append(f"💰 Итого: €{total:.2f}")
    return "\n".join(lines)


async def _confirm(q, ctx):
    cart = ctx.user_data.get("cart", [])
    client = ctx.user_data.get("client", {})
    ship = ctx.user_data.get("shipping", {})
    bill = ctx.user_data.get("billing", {})
    txt = _confirm_text(client, cart, ship, bill)
    await q.edit_message_text(txt, reply_markup=kb([
        [("✅ Создать заказ", "place_order")],
        [("✏️ Редактировать", "edit_order")],
        [("❌ Отменить", "cancel_order")],
    ]))
    return CONFIRM_ORDER


async def confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "edit_order":
        return await _cart(q, context)
    if q.data == "cancel_order":
        context.user_data["cart"] = []
        await q.edit_message_text("❌ Отменён.")
        return await _main_menu(q, context)
    if q.data == "place_order":
        await q.edit_message_text("⏳ Создаю заказ...")
        return await _create_order(q, context)
    return CONFIRM_ORDER


# ═══════════════════════════════════════════════════════════════
#  CREATE ORDER → SHOPIFY + SHEETS
# ═══════════════════════════════════════════════════════════════


def _to_shopify_addr(addr_dict, client):
    contact = client.get("contact_person", "")
    parts = contact.split(" ", 1) if contact else ["", ""]
    return {
        "firstName": parts[0] if parts else "",
        "lastName": parts[1] if len(parts) > 1 else "",
        "company": client.get("name", ""),
        "address1": addr_dict.get("street", ""),
        "city": addr_dict.get("city", "Berlin"),
        "zip": addr_dict.get("zip", ""),
        "countryCode": addr_dict.get("country", "DE"),
        "phone": str(client.get("phone", "")),
    }


async def _create_order(q, ctx):
    cart = ctx.user_data.get("cart", [])
    client = ctx.user_data.get("client", {})
    ship = ctx.user_data.get("shipping", {})
    bill = ctx.user_data.get("billing", {})
    rep = ctx.user_data.get("rep", {})
    rep_name = rep.get("name", "?")

    line_items = [{"title": i["display_name"], "quantity": i["quantity"], "custom_price": i["price"]} for i in cart]

    shipping_address = _to_shopify_addr(ship, client)
    billing_address = _to_shopify_addr(bill, client)

    result = await shopify.create_draft_order(
        customer_id=client.get("shopify_customer_id") or None,
        line_items=line_items,
        shipping_address=shipping_address,
        billing_address=billing_address,
        note=f"Sales rep: {rep_name} | {client.get('name','')}",
        tags=["telegram-bot"],
        email=str(client.get("email", "")),
    )

    ok = not result.get("error")
    oid = sheets.get_next_order_id()
    sheets.save_order({
        "order_id": oid,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "client_id": client.get("client_id", ""),
        "client_name": client.get("name", ""),
        "items": order_items_summary(cart),
        "total": f"{cart_total(cart):.2f}",
        "address": fmt_addr(ship),
        "sales_rep": rep_name,
        "shopify_draft_id": result.get("id", "") if ok else "",
        "shopify_invoice_url": result.get("invoiceUrl", "") if ok else "",
        "status": "draft" if ok else "saved",
    })
    sheets.update_client_after_order(client.get("client_id", ""), order_items_summary(cart))

    if ok:
        ctx.user_data["last_invoice_url"] = result.get("invoiceUrl", "")
        btns = []
        if result.get("invoiceUrl"):
            btns.append([("🔗 Ссылка на оплату", "copy_invoice")])
        btns += [[("🆕 Новый заказ", "new_order")], back_btn()]
        await q.edit_message_text(
            f"✅ Заказ создан!\n📋 {result.get('name','')}\n📦 {oid}\n💰 €{cart_total(cart):.2f}",
            reply_markup=kb(btns))
    else:
        await q.edit_message_text(
            f"✅ Сохранён в таблицу\n📦 {oid}\n💰 €{cart_total(cart):.2f}\n⚠️ Shopify: {result.get('error','')}",
            reply_markup=kb([[("🆕 Новый заказ", "new_order")], back_btn()]))
    return MAIN_MENU


# ─── Cancel ───────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("/start чтобы начать заново.")
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
#  CONVERSATION HANDLER
# ═══════════════════════════════════════════════════════════════


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    back_h = CallbackQueryHandler(go_back, pattern="^back_")

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_cb, pattern="^(new_order|copy_invoice)$"),
                back_h,
            ],
            SEARCH_CLIENT: [
                CallbackQueryHandler(search_start, pattern="^search_client$"),
                CallbackQueryHandler(nc_start, pattern="^new_client$"),
                back_h,
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_input),
            ],
            SELECT_CLIENT: [
                CallbackQueryHandler(pick_client, pattern="^(pick_\\d+|search_client|new_client)$"),
                back_h,
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_input),
            ],
            CLIENT_CARD: [
                CallbackQueryHandler(client_card_cb, pattern="^(start_order|search_client)$"),
                back_h,
            ],
            SELECT_PRODUCT: [
                CallbackQueryHandler(product_cb, pattern="^(prod_|show_cart)"),
                back_h,
            ],
            ENTER_QUANTITY: [
                back_h,
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_qty),
            ],
            ENTER_PRICE: [
                back_h,
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_price),
            ],
            CART: [
                CallbackQueryHandler(cart_cb, pattern="^(add_more|remove_last|checkout|clear_cart)$"),
                back_h,
            ],
            SELECT_ADDRESS: [
                CallbackQueryHandler(addr_cb, pattern="^addr_"),
                back_h,
            ],
            SHIP_STREET: [back_h, MessageHandler(filters.TEXT & ~filters.COMMAND, ship_street)],
            SHIP_ZIP: [back_h, MessageHandler(filters.TEXT & ~filters.COMMAND, ship_zip)],
            SHIP_CITY: [
                CallbackQueryHandler(ship_city_btn, pattern="^shipcity_"),
                back_h,
                MessageHandler(filters.TEXT & ~filters.COMMAND, ship_city_text),
            ],
            SHIP_COUNTRY: [
                CallbackQueryHandler(ship_country_btn, pattern="^shipcountry_"),
                back_h,
                MessageHandler(filters.TEXT & ~filters.COMMAND, ship_country_text),
            ],
            INVOICE_SAME: [
                CallbackQueryHandler(invoice_same_cb, pattern="^invoice_"),
                back_h,
            ],
            BILL_STREET: [back_h, MessageHandler(filters.TEXT & ~filters.COMMAND, bill_street)],
            BILL_ZIP: [back_h, MessageHandler(filters.TEXT & ~filters.COMMAND, bill_zip)],
            BILL_CITY: [
                CallbackQueryHandler(bill_city_btn, pattern="^billcity_"),
                back_h,
                MessageHandler(filters.TEXT & ~filters.COMMAND, bill_city_text),
            ],
            BILL_COUNTRY: [
                CallbackQueryHandler(bill_country_btn, pattern="^billcountry_"),
                back_h,
                MessageHandler(filters.TEXT & ~filters.COMMAND, bill_country_text),
            ],
            CONFIRM_ORDER: [
                CallbackQueryHandler(confirm_cb, pattern="^(place_order|edit_order|cancel_order)$"),
                back_h,
            ],
            NC_NAME: [back_h, MessageHandler(filters.TEXT & ~filters.COMMAND, nc_name)],
            NC_CONTACT: [back_h, MessageHandler(filters.TEXT & ~filters.COMMAND, nc_contact)],
            NC_PHONE: [back_h, MessageHandler(filters.TEXT & ~filters.COMMAND, nc_phone)],
            NC_EMAIL: [
                CallbackQueryHandler(nc_email_skip, pattern="^skip_email$"),
                back_h,
                MessageHandler(filters.TEXT & ~filters.COMMAND, nc_email_text),
            ],
            NC_ADDR_STREET: [back_h, MessageHandler(filters.TEXT & ~filters.COMMAND, nc_addr_street)],
            NC_ADDR_ZIP: [back_h, MessageHandler(filters.TEXT & ~filters.COMMAND, nc_addr_zip)],
            NC_ADDR_CITY: [
                CallbackQueryHandler(nc_addr_city_btn, pattern="^nccity_"),
                back_h,
                MessageHandler(filters.TEXT & ~filters.COMMAND, nc_addr_city_text),
            ],
            NC_ADDR_COUNTRY: [
                CallbackQueryHandler(nc_addr_country_btn, pattern="^nccountry_"),
                back_h,
                MessageHandler(filters.TEXT & ~filters.COMMAND, nc_addr_country_text),
            ],
            NC_CONFIRM: [
                CallbackQueryHandler(nc_confirm, pattern="^(save_client|redo_client)$"),
                back_h,
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
       
