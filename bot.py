"""
Telegram Order Bot — Sales Rep v3

Hardcoded product catalog tree. No Google Sheets catalog needed.
Structured addresses for shipping & billing.
Back button on every step.
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters,
)

from config import TELEGRAM_BOT_TOKEN
import sheets_service as sheets
import shopify_service as shopify

logging.basicConfig(format="%(asctime)s — %(name)s — %(levelname)s — %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── States ───────────────────────────────────────────────────

(
    MAIN_MENU, SEARCH_CLIENT, SELECT_CLIENT, CLIENT_CARD,
    # Catalog navigation
    CAT_L1, CAT_L2, CAT_L3, CAT_SIZE,
    ENTER_QTY, ENTER_PRICE, CART,
    # Shipping
    SELECT_ADDRESS, SHIP_STREET, SHIP_ZIP, SHIP_CITY, SHIP_COUNTRY,
    # Invoice
    INVOICE_SAME, BILL_STREET, BILL_ZIP, BILL_CITY, BILL_COUNTRY,
    CONFIRM_ORDER,
    # New client
    NC_NAME, NC_CONTACT, NC_PHONE, NC_EMAIL, NC_TAXID,
    NC_STREET, NC_ZIP, NC_CITY, NC_COUNTRY, NC_CONFIRM,
) = range(32)


# ═══════════════════════════════════════════════════════════════
#  PRODUCT CATALOG (hardcoded tree)
# ═══════════════════════════════════════════════════════════════

CAVIAR_SIZES = ["30g", "50g", "125g", "250g", "500g", "1kg"]
SMALL_SIZES = ["57g"]
RED_SIZES = ["100g", "190g"]

# Level 1 → Level 2 → Level 3 → sizes
CATALOG = {
    "Siberian Oscietra": {
        "Classic Siberian Osc": CAVIAR_SIZES,
        "Royal Siberian Osc": CAVIAR_SIZES,
        "_other": {
            "Dry": SMALL_SIZES,
            "Pasteurisiert": SMALL_SIZES,
        },
    },
    "Russian Oscietra": {
        "Classic Russian Osc": CAVIAR_SIZES,
        "Royal Russian Osc": CAVIAR_SIZES,
        "_other": {
            "Dry": SMALL_SIZES,
            "Pasteurisiert": SMALL_SIZES,
        },
    },
    "Beluga": {
        "Classic Beluga": CAVIAR_SIZES,
        "Royal Beluga": CAVIAR_SIZES,
        "_other": {
            "Dry": SMALL_SIZES,
            "Pasteurisiert": SMALL_SIZES,
        },
    },
    "Другое": {
        "Другая икра": {
            "Чёрная": {
                "Amur": CAVIAR_SIZES,
                "Kaluga": CAVIAR_SIZES,
            },
            "Красная": {
                "Лосось": RED_SIZES,
                "Кета": RED_SIZES,
                "Форель": RED_SIZES,
            },
        },
        "Рыба": {
            "Лосось": "_no_size",
            "Боттарга": {
                "Обычная": "_no_size",
                "В воске": "_no_size",
                "В пчелином воске": "_no_size",
            },
            "Осетровое филе": "_no_size",
        },
        "Аксессуары": {
            "Открывашка": "_no_size",
            "Ложка большая": "_no_size",
            "Ложка рыба": "_no_size",
            "Обычная ложка": "_no_size",
        },
    },
}


# ─── Helpers ──────────────────────────────────────────────────

def kb(buttons):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in buttons])

def back_btn(cb="back_main"):
    return [("↩️ Назад", cb)]

def format_cart(cart):
    if not cart:
        return "Корзина пуста."
    lines = []
    total = 0.0
    for i, item in enumerate(cart, 1):
        sub = item["price"] * item["quantity"]
        total += sub
        name = item["name"]
        if item.get("size"):
            name += f" {item['size']}"
        lines.append(f"{i}. {name} × {item['quantity']} — €{sub:.2f} (€{item['price']:.2f}/шт)")
    lines.append(f"\n💰 Итого: €{total:.2f}")
    return "\n".join(lines)

def cart_total(cart):
    return sum(i["price"] * i["quantity"] for i in cart)

def order_summary(cart):
    parts = []
    for i in cart:
        n = i["name"]
        if i.get("size"):
            n += f" {i['size']}"
        parts.append(f"{n} x{i['quantity']} @€{i['price']:.2f}")
    return ", ".join(parts)

def fmt_addr(a):
    if not a:
        return "—"
    return f"{a.get('street','')}, {a.get('zip','')} {a.get('city','')}, {a.get('country','DE')}"


# ─── Universal back handler ──────────────────────────────────

async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    t = q.data
    if t == "back_main": return await _main_menu(q, context)
    if t == "back_search":
        await q.edit_message_text("Для кого заказ?", reply_markup=kb([
            [("🔍 Поиск", "search_client")], [("➕ Новый клиент", "new_client")], back_btn()]))
        return SEARCH_CLIENT
    if t == "back_client": return await _client_card(q, context)
    if t == "back_cat_l1": return await _show_l1(q, context)
    if t == "back_cat_l2":
        return await _show_l2(q, context, context.user_data.get("cat_l1", ""))
    if t == "back_cat_l3":
        return await _show_l3(q, context, context.user_data.get("cat_l1",""), context.user_data.get("cat_l2",""))
    if t == "back_products": return await _show_l1(q, context)
    if t == "back_cart": return await _cart(q, context)
    if t == "back_addr": return await _addr_select(q, context)
    if t == "back_invoice_q": return await _invoice_q(q, context)
    return await _main_menu(q, context)


# ─── /start & main menu ──────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    rep = sheets.get_sales_rep(update.effective_user.id)
    if not rep:
        await update.message.reply_text(f"⛔ Доступ запрещён.\nID: `{update.effective_user.id}`", parse_mode="Markdown")
        return ConversationHandler.END
    context.user_data.update({"rep": rep, "cart": [], "client": None})
    await update.message.reply_text(f"Привет, {rep.get('name','менеджер')}!",
                                     reply_markup=kb([[("🆕 Новый заказ", "new_order")]]))
    return MAIN_MENU

async def _main_menu(q, ctx):
    await q.edit_message_text(f"{ctx.user_data.get('rep',{}).get('name','')}, что делаем?",
                              reply_markup=kb([[("🆕 Новый заказ", "new_order")]]))
    return MAIN_MENU

async def main_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "new_order":
        context.user_data.update({"cart": [], "client": None, "shipping": None, "billing": None})
        await q.edit_message_text("Для кого заказ?", reply_markup=kb([
            [("🔍 Поиск", "search_client")], [("➕ Новый клиент", "new_client")]]))
        return SEARCH_CLIENT
    if q.data == "copy_invoice":
        url = context.user_data.get("last_invoice_url", "")
        await q.message.reply_text(f"🔗 {url}" if url else "Нет ссылки.")
    return MAIN_MENU


# ═══════════════════════════════════════════════════════════════
#  CLIENT SEARCH
# ═══════════════════════════════════════════════════════════════

async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    await q.edit_message_text("🔍 Введите имя, телефон или компанию:", reply_markup=kb([back_btn()]))
    return SELECT_CLIENT

async def search_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    results = sheets.search_clients(update.message.text.strip())
    if not results:
        await update.message.reply_text("Не найдено.", reply_markup=kb([
            [("🔍 Снова", "search_client")], [("➕ Новый", "new_client")], back_btn()]))
        return SEARCH_CLIENT
    context.user_data["search_results"] = results
    btns = [[(c.get("name","?") + (f" — {c.get('address_1','')[:20]}" if c.get("address_1") else ""),
              f"pick_{i}")] for i, c in enumerate(results[:10])]
    btns += [[("🔍 Снова", "search_client"), ("➕ Новый", "new_client")], back_btn()]
    await update.message.reply_text(f"Найдено ({len(results)}):", reply_markup=kb(btns))
    return SELECT_CLIENT

async def pick_client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "search_client":
        await q.edit_message_text("🔍 Введите имя:", reply_markup=kb([back_btn()]))
        return SELECT_CLIENT
    if q.data == "new_client":
        context.user_data["nc"] = {}
        await q.edit_message_text("➕ Компания:", reply_markup=kb([back_btn("back_search")]))
        return NC_NAME
    idx = int(q.data.replace("pick_", ""))
    context.user_data["client"] = context.user_data["search_results"][idx]
    return await _client_card(q, context)

async def _client_card(q, ctx):
    c = ctx.user_data["client"]
    txt = f"🏢 {c.get('name','—')}\n👤 {c.get('contact_person','—')}\n📞 {c.get('phone','—')}\n📧 {c.get('email','—')}\n📍 {c.get('address_1','—')}"
    await q.edit_message_text(txt, reply_markup=kb([
        [("🆕 Собрать заказ", "start_order")], [("🔍 Другой", "search_client")], back_btn()]))
    return CLIENT_CARD

async def client_card_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "start_order":
        context.user_data["cart"] = []
        return await _show_l1(q, context)
    if q.data == "search_client":
        await q.edit_message_text("🔍 Введите имя:", reply_markup=kb([back_btn()]))
        return SELECT_CLIENT
    return CLIENT_CARD


# ═══════════════════════════════════════════════════════════════
#  NEW CLIENT
# ═══════════════════════════════════════════════════════════════

async def nc_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    context.user_data["nc"] = {}
    await q.edit_message_text("➕ Компания / имя клиента:", reply_markup=kb([back_btn("back_search")]))
    return NC_NAME

async def nc_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["nc"]["name"] = update.message.text.strip()
    await update.message.reply_text("👤 Контактное лицо:", reply_markup=kb([back_btn("back_search")]))
    return NC_CONTACT

async def nc_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["nc"]["contact_person"] = update.message.text.strip()
    await update.message.reply_text("📞 Телефон:", reply_markup=kb([back_btn("back_search")]))
    return NC_PHONE

async def nc_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["nc"]["phone"] = update.message.text.strip()
    await update.message.reply_text("📧 Email:", reply_markup=kb([[("⏩ Пропустить","skip_email")], back_btn("back_search")]))
    return NC_EMAIL

async def nc_email_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["nc"]["email"] = update.message.text.strip()
    await update.message.reply_text("🏛 USt-IdNr (Tax ID):", reply_markup=kb([[("⏩ Пропустить","skip_taxid")], back_btn("back_search")]))
    return NC_TAXID

async def nc_email_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    ctx.user_data["nc"]["email"] = ""
    await q.edit_message_text("🏛 USt-IdNr (Tax ID):", reply_markup=kb([[("⏩ Пропустить","skip_taxid")], back_btn("back_search")]))
    return NC_TAXID

async def nc_taxid_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["nc"]["tax_id"] = update.message.text.strip()
    await update.message.reply_text("📍 Улица и номер дома:", reply_markup=kb([back_btn("back_search")]))
    return NC_STREET

async def nc_taxid_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    ctx.user_data["nc"]["tax_id"] = ""
    await q.edit_message_text("📍 Улица и номер дома:", reply_markup=kb([back_btn("back_search")]))
    return NC_STREET

async def nc_street(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["nc"]["street"] = update.message.text.strip()
    await update.message.reply_text("📮 PLZ:", reply_markup=kb([back_btn("back_search")]))
    return NC_ZIP

async def nc_zip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["nc"]["zip"] = update.message.text.strip()
    await update.message.reply_text("🏙 Город:", reply_markup=kb([
        [("Berlin","nccity_Berlin")],[("✏️ Другой","nccity_other")], back_btn("back_search")]))
    return NC_CITY

async def nc_city_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "nccity_other":
        await q.edit_message_text("🏙 Город:", reply_markup=kb([back_btn("back_search")]))
        return NC_CITY
    ctx.user_data["nc"]["city"] = q.data.replace("nccity_","")
    await q.edit_message_text("🌍 Страна:", reply_markup=kb([
        [("🇩🇪 DE","ncc_DE")],[("🇦🇹 AT","ncc_AT"),("🇨🇭 CH","ncc_CH")],[("✏️ Другая","ncc_other")], back_btn("back_search")]))
    return NC_COUNTRY

async def nc_city_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["nc"]["city"] = update.message.text.strip()
    await update.message.reply_text("🌍 Страна:", reply_markup=kb([
        [("🇩🇪 DE","ncc_DE")],[("🇦🇹 AT","ncc_AT"),("🇨🇭 CH","ncc_CH")],[("✏️ Другая","ncc_other")], back_btn("back_search")]))
    return NC_COUNTRY

async def nc_country_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "ncc_other":
        await q.edit_message_text("🌍 Код страны (DE,AT,CH…):", reply_markup=kb([back_btn("back_search")]))
        return NC_COUNTRY
    ctx.user_data["nc"]["country"] = q.data.replace("ncc_","")
    return await _nc_confirm_show(q, ctx, True)

async def nc_country_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["nc"]["country"] = update.message.text.strip().upper()[:2]
    return await _nc_confirm_show(update, ctx, False)

async def _nc_confirm_show(src, ctx, is_cb):
    nc = ctx.user_data["nc"]
    a = f"{nc.get('street','')}, {nc.get('zip','')} {nc.get('city','')}, {nc.get('country','DE')}"
    tax = nc.get('tax_id','')
    txt = (f"📋 Новый клиент:\n🏢 {nc.get('name')}\n👤 {nc.get('contact_person')}\n"
           f"📞 {nc.get('phone')}\n📧 {nc.get('email') or '—'}\n🏛 USt-IdNr: {tax or '—'}\n📍 {a}\n\nВерно?")
    btns = kb([[("✅ Сохранить","save_client")],[("✏️ Заново","redo_client")],back_btn()])
    if is_cb: await src.edit_message_text(txt, reply_markup=btns)
    else: await src.message.reply_text(txt, reply_markup=btns)
    return NC_CONFIRM

async def nc_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "redo_client":
        ctx.user_data["nc"] = {}
        await q.edit_message_text("➕ Компания:", reply_markup=kb([back_btn("back_search")]))
        return NC_NAME
    if q.data == "save_client":
        nc = ctx.user_data["nc"]
        cid = sheets.get_next_client_id()
        addr = f"{nc.get('street','')}, {nc.get('zip','')} {nc.get('city','')}, {nc.get('country','DE')}"
        data = {"client_id": cid, "name": nc.get("name",""), "contact_person": nc.get("contact_person",""),
                "phone": nc.get("phone",""), "email": nc.get("email",""), "telegram_id": "",
                "tax_id": nc.get("tax_id",""),
                "address_1": addr, "address_2":"","address_label_1":"","address_label_2":"",
                "notes":"","shopify_customer_id":"","usual_order":"","last_order_date":""}
        if sheets.create_client(data):
            ctx.user_data["client"] = data
            await q.edit_message_text(f"✅ Клиент «{nc.get('name')}» создан!", reply_markup=kb([
                [("🆕 Собрать заказ","start_order")], back_btn()]))
            return CLIENT_CARD
        await q.edit_message_text("❌ Ошибка.")
    return NC_CONFIRM


# ═══════════════════════════════════════════════════════════════
#  CATALOG NAVIGATION
# ═══════════════════════════════════════════════════════════════

async def _show_l1(q, ctx):
    """Level 1: main categories."""
    cart = ctx.user_data.get("cart", [])
    btns = [[(name, f"l1_{name}")] for name in CATALOG.keys()]
    if cart:
        btns.append([("🛒 Корзина ({})".format(len(cart)), "show_cart")])
    btns.append(back_btn("back_client"))
    await q.edit_message_text("Выберите:", reply_markup=kb(btns))
    return CAT_L1

async def cat_l1_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "show_cart": return await _cart(q, ctx)
    name = q.data.replace("l1_", "")
    ctx.user_data["cat_l1"] = name
    return await _show_l2(q, ctx, name)

async def _show_l2(q, ctx, l1):
    """Level 2: sub-categories."""
    node = CATALOG.get(l1, {})
    btns = []
    for key in node:
        if key == "_other":
            btns.append([("Другое", "l2__other")])
        else:
            btns.append([(key, f"l2_{key}")])
    btns.append(back_btn("back_cat_l1"))
    await q.edit_message_text(f"📦 {l1}:", reply_markup=kb(btns))
    return CAT_L2

async def cat_l2_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    name = q.data.replace("l2_", "")
    ctx.user_data["cat_l2"] = name
    l1 = ctx.user_data["cat_l1"]
    node = CATALOG[l1]

    if name == "_other":
        sub = node.get("_other", {})
    else:
        sub = node.get(name)

    if sub is None:
        return CAT_L2

    # If sub is a list of sizes → go to size selection
    if isinstance(sub, list):
        ctx.user_data["cat_path"] = f"{l1} → {name}"
        return await _show_sizes(q, ctx, sub)

    # If sub is "_no_size" → skip size, go to qty
    if sub == "_no_size":
        ctx.user_data["cat_path"] = f"{l1} → {name}"
        ctx.user_data["cur_size"] = ""
        await q.edit_message_text(f"📦 {name}\n\nКоличество?", reply_markup=kb([back_btn("back_cat_l2")]))
        return ENTER_QTY

    # Otherwise it's a dict → go deeper (L3)
    return await _show_l3(q, ctx, l1, name)

async def _show_l3(q, ctx, l1, l2):
    """Level 3: deeper sub-categories."""
    node = CATALOG[l1]
    if l2 == "_other":
        sub = node.get("_other", {})
    else:
        sub = node.get(l2, {})

    btns = []
    for key in sub:
        btns.append([(key, f"l3_{key}")])
    btns.append(back_btn("back_cat_l2"))
    label = "Другое" if l2 == "_other" else l2
    await q.edit_message_text(f"📦 {l1} → {label}:", reply_markup=kb(btns))
    return CAT_L3

async def cat_l3_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    name = q.data.replace("l3_", "")
    ctx.user_data["cat_l3"] = name
    l1 = ctx.user_data["cat_l1"]
    l2 = ctx.user_data["cat_l2"]

    node = CATALOG[l1]
    if l2 == "_other":
        sub = node.get("_other", {}).get(name)
    else:
        sub = node.get(l2, {}).get(name)

    if sub is None:
        return CAT_L3

    if isinstance(sub, list):
        ctx.user_data["cat_path"] = f"{l1} → {l2} → {name}" if l2 != "_other" else f"{l1} → {name}"
        return await _show_sizes(q, ctx, sub)

    if sub == "_no_size":
        ctx.user_data["cat_path"] = f"{l1} → {l2} → {name}" if l2 != "_other" else f"{l1} → {name}"
        ctx.user_data["cur_size"] = ""
        await q.edit_message_text(f"📦 {name}\n\nКоличество?", reply_markup=kb([back_btn("back_cat_l3")]))
        return ENTER_QTY

    # Even deeper (e.g. Другая икра → Чёрная → Amur)
    # Store and show next level
    ctx.user_data["cat_l3_sub"] = name
    btns = []
    for key in sub:
        btns.append([(key, f"l4_{key}")])
    btns.append(back_btn("back_cat_l3"))
    await q.edit_message_text(f"📦 {name}:", reply_markup=kb(btns))
    return CAT_SIZE  # reuse state for L4 selections

async def cat_l4_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle level 4+ selections (deepest items)."""
    q = update.callback_query; await q.answer()
    name = q.data.replace("l4_", "")
    l1 = ctx.user_data["cat_l1"]
    l2 = ctx.user_data["cat_l2"]
    l3 = ctx.user_data["cat_l3"]
    l3_sub = ctx.user_data.get("cat_l3_sub", "")

    # Navigate to the value
    node = CATALOG[l1]
    if l2 == "_other":
        branch = node.get("_other", {}).get(l3, {})
    else:
        branch = node.get(l2, {}).get(l3, {})

    if l3_sub and isinstance(branch, dict):
        branch = branch.get(l3_sub, {})

    val = branch.get(name) if isinstance(branch, dict) else branch

    path = f"{l3_sub} → {name}" if l3_sub else f"{l3} → {name}"
    ctx.user_data["cat_path"] = path

    if isinstance(val, list):
        return await _show_sizes(q, ctx, val)
    if val == "_no_size":
        ctx.user_data["cur_size"] = ""
        await q.edit_message_text(f"📦 {name}\n\nКоличество?", reply_markup=kb([back_btn("back_cat_l3")]))
        return ENTER_QTY

    # It's another dict → show its keys as sizes or items
    if isinstance(val, dict):
        btns = [[(k, f"l4_{k}")] for k in val]
        btns.append(back_btn("back_cat_l3"))
        await q.edit_message_text(f"📦 {name}:", reply_markup=kb(btns))
        return CAT_SIZE

    return CAT_SIZE


async def _show_sizes(q, ctx, sizes):
    path = ctx.user_data.get("cat_path", "")
    btns = [[(s, f"size_{s}")] for s in sizes]
    btns.append(back_btn("back_cat_l2"))
    await q.edit_message_text(f"📦 {path}\n\nРазмер:", reply_markup=kb(btns))
    return CAT_SIZE

async def size_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    size = q.data.replace("size_", "")
    ctx.user_data["cur_size"] = size
    path = ctx.user_data.get("cat_path", "")
    await q.edit_message_text(f"📦 {path} {size}\n\nКоличество?", reply_markup=kb([back_btn("back_cat_l2")]))
    return ENTER_QTY


# ─── Quantity & Price ─────────────────────────────────────────

async def enter_qty(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    try:
        qty = int(txt); assert qty > 0
    except:
        await update.message.reply_text("❌ Число > 0:", reply_markup=kb([back_btn("back_cat_l1")]))
        return ENTER_QTY
    ctx.user_data["cur_qty"] = qty
    path = ctx.user_data.get("cat_path", "")
    size = ctx.user_data.get("cur_size", "")
    label = f"{path} {size}".strip()
    await update.message.reply_text(f"📦 {label} × {qty}\n\n💶 Цена за штуку (€)?", reply_markup=kb([back_btn("back_cat_l1")]))
    return ENTER_PRICE

async def enter_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip().replace(",",".")
    try:
        price = float(txt); assert price > 0
    except:
        await update.message.reply_text("❌ Цена:", reply_markup=kb([back_btn("back_cat_l1")]))
        return ENTER_PRICE
    path = ctx.user_data.get("cat_path", "")
    size = ctx.user_data.get("cur_size", "")
    ctx.user_data.setdefault("cart", []).append({
        "name": path, "size": size, "display_name": f"{path} {size}".strip(),
        "price": price, "quantity": ctx.user_data["cur_qty"],
    })
    cart = ctx.user_data["cart"]
    btns = [[("➕ Добавить ещё","add_more")]]
    if cart: btns.append([("🗑 Удалить последний","remove_last")])
    btns += [[("✅ Оформить","checkout")],[("❌ Очистить","clear_cart")]]
    await update.message.reply_text("🛒 Корзина:\n\n" + format_cart(cart), reply_markup=kb(btns))
    return CART


# ─── Cart ─────────────────────────────────────────────────────

async def _cart(q, ctx):
    cart = ctx.user_data.get("cart", [])
    btns = [[("➕ Добавить","add_more")]]
    if cart: btns.append([("🗑 Удалить последний","remove_last")])
    btns += [[("✅ Оформить","checkout")],[("❌ Очистить","clear_cart")],back_btn("back_cat_l1")]
    await q.edit_message_text("🛒 Корзина:\n\n" + format_cart(cart), reply_markup=kb(btns))
    return CART

async def cart_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "add_more": return await _show_l1(q, ctx)
    if q.data == "remove_last":
        cart = ctx.user_data.get("cart",[])
        if cart: cart.pop()
        return await _cart(q, ctx)
    if q.data == "clear_cart":
        ctx.user_data["cart"] = []
        return await _cart(q, ctx)
    if q.data == "checkout":
        if not ctx.user_data.get("cart"): return await _show_l1(q, ctx)
        return await _addr_select(q, ctx)
    return CART


# ═══════════════════════════════════════════════════════════════
#  SHIPPING ADDRESS
# ═══════════════════════════════════════════════════════════════

async def _addr_select(q, ctx):
    c = ctx.user_data.get("client",{})
    btns = []
    if c.get("address_1"): btns.append([(f"📍 {c['address_1'][:40]}","addr_s1")])
    if c.get("address_2"): btns.append([(f"📍 {c['address_2'][:40]}","addr_s2")])
    btns += [[("✏️ Новый адрес","addr_new")], back_btn("back_cart")]
    await q.edit_message_text("📍 Адрес доставки:", reply_markup=kb(btns))
    return SELECT_ADDRESS

async def addr_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    c = ctx.user_data.get("client",{})
    if q.data == "addr_s1":
        ctx.user_data["shipping"] = {"street": c.get("address_1",""), "zip":"", "city":"Berlin", "country":"DE"}
        return await _invoice_q(q, ctx)
    if q.data == "addr_s2":
        ctx.user_data["shipping"] = {"street": c.get("address_2",""), "zip":"", "city":"Berlin", "country":"DE"}
        return await _invoice_q(q, ctx)
    if q.data == "addr_new":
        ctx.user_data["_sh"] = {}
        await q.edit_message_text("📍 Улица:", reply_markup=kb([back_btn("back_addr")]))
        return SHIP_STREET
    return SELECT_ADDRESS

async def sh_street(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.setdefault("_sh",{})["street"] = update.message.text.strip()
    await update.message.reply_text("📮 PLZ:", reply_markup=kb([back_btn("back_addr")]))
    return SHIP_ZIP

async def sh_zip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["_sh"]["zip"] = update.message.text.strip()
    await update.message.reply_text("🏙 Город:", reply_markup=kb([
        [("Berlin","shcity_Berlin")],[("✏️ Другой","shcity_other")],back_btn("back_addr")]))
    return SHIP_CITY

async def sh_city_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "shcity_other":
        await q.edit_message_text("🏙 Город:", reply_markup=kb([back_btn("back_addr")]))
        return SHIP_CITY
    ctx.user_data["_sh"]["city"] = q.data.replace("shcity_","")
    await q.edit_message_text("🌍 Страна:", reply_markup=kb([
        [("🇩🇪 DE","shc_DE")],[("🇦🇹 AT","shc_AT"),("🇨🇭 CH","shc_CH")],[("✏️","shc_other")],back_btn("back_addr")]))
    return SHIP_COUNTRY

async def sh_city_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["_sh"]["city"] = update.message.text.strip()
    await update.message.reply_text("🌍 Страна:", reply_markup=kb([
        [("🇩🇪 DE","shc_DE")],[("🇦🇹 AT","shc_AT"),("🇨🇭 CH","shc_CH")],[("✏️","shc_other")],back_btn("back_addr")]))
    return SHIP_COUNTRY

async def sh_country_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "shc_other":
        await q.edit_message_text("🌍 Код (DE,AT…):", reply_markup=kb([back_btn("back_addr")]))
        return SHIP_COUNTRY
    ctx.user_data["_sh"]["country"] = q.data.replace("shc_","")
    ctx.user_data["shipping"] = dict(ctx.user_data["_sh"])
    return await _invoice_q(q, ctx)

async def sh_country_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["_sh"]["country"] = update.message.text.strip().upper()[:2]
    ctx.user_data["shipping"] = dict(ctx.user_data["_sh"])
    ship = ctx.user_data["shipping"]
    await update.message.reply_text(
        f"📍 Доставка: {fmt_addr(ship)}\n\n🧾 Rechnungsadresse такой же?",
        reply_markup=kb([[("✅ Да","invoice_same")],[("✏️ Нет","invoice_diff")],back_btn("back_addr")]))
    return INVOICE_SAME


# ═══════════════════════════════════════════════════════════════
#  INVOICE ADDRESS
# ═══════════════════════════════════════════════════════════════

async def _invoice_q(q, ctx):
    ship = ctx.user_data.get("shipping",{})
    await q.edit_message_text(
        f"📍 Доставка: {fmt_addr(ship)}\n\n🧾 Rechnungsadresse такой же?",
        reply_markup=kb([[("✅ Да","invoice_same")],[("✏️ Нет","invoice_diff")],back_btn("back_addr")]))
    return INVOICE_SAME

async def invoice_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "invoice_same":
        ctx.user_data["billing"] = dict(ctx.user_data.get("shipping",{}))
        return await _confirm(q, ctx)
    if q.data == "invoice_diff":
        ctx.user_data["_bl"] = {}
        await q.edit_message_text("🧾 Улица (счёт):", reply_markup=kb([back_btn("back_invoice_q")]))
        return BILL_STREET
    return INVOICE_SAME

async def bl_street(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.setdefault("_bl",{})["street"] = update.message.text.strip()
    await update.message.reply_text("📮 PLZ (счёт):", reply_markup=kb([back_btn("back_invoice_q")]))
    return BILL_ZIP

async def bl_zip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["_bl"]["zip"] = update.message.text.strip()
    await update.message.reply_text("🏙 Город (счёт):", reply_markup=kb([
        [("Berlin","blcity_Berlin")],[("✏️","blcity_other")],back_btn("back_invoice_q")]))
    return BILL_CITY

async def bl_city_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "blcity_other":
        await q.edit_message_text("🏙 Город (счёт):", reply_markup=kb([back_btn("back_invoice_q")]))
        return BILL_CITY
    ctx.user_data["_bl"]["city"] = q.data.replace("blcity_","")
    await q.edit_message_text("🌍 Страна (счёт):", reply_markup=kb([
        [("🇩🇪 DE","blc_DE")],[("🇦🇹 AT","blc_AT"),("🇨🇭 CH","blc_CH")],[("✏️","blc_other")],back_btn("back_invoice_q")]))
    return BILL_COUNTRY

async def bl_city_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["_bl"]["city"] = update.message.text.strip()
    await update.message.reply_text("🌍 Страна (счёт):", reply_markup=kb([
        [("🇩🇪 DE","blc_DE")],[("🇦🇹 AT","blc_AT"),("🇨🇭 CH","blc_CH")],[("✏️","blc_other")],back_btn("back_invoice_q")]))
    return BILL_COUNTRY

async def bl_country_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "blc_other":
        await q.edit_message_text("🌍 Код:", reply_markup=kb([back_btn("back_invoice_q")]))
        return BILL_COUNTRY
    ctx.user_data["_bl"]["country"] = q.data.replace("blc_","")
    ctx.user_data["billing"] = dict(ctx.user_data["_bl"])
    return await _confirm(q, ctx)

async def bl_country_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["_bl"]["country"] = update.message.text.strip().upper()[:2]
    ctx.user_data["billing"] = dict(ctx.user_data["_bl"])
    cart = ctx.user_data.get("cart",[])
    client = ctx.user_data.get("client",{})
    ship = ctx.user_data.get("shipping",{})
    bill = ctx.user_data["billing"]
    await update.message.reply_text(_confirm_text(client,cart,ship,bill), reply_markup=kb([
        [("✅ Создать","place_order")],[("✏️ Редактировать","edit_order")],[("❌ Отмена","cancel_order")]]))
    return CONFIRM_ORDER


# ═══════════════════════════════════════════════════════════════
#  CONFIRMATION
# ═══════════════════════════════════════════════════════════════

def _confirm_text(client, cart, ship, bill):
    lines = [f"📦 Заказ для {client.get('name','—')}:\n"]
    total = 0.0
    for i, item in enumerate(cart, 1):
        sub = item["price"] * item["quantity"]
        total += sub
        n = item["name"]
        if item.get("size"): n += f" {item['size']}"
        lines.append(f"{i}. {n} × {item['quantity']} — €{sub:.2f}")
    lines.append(f"\n📍 Доставка: {fmt_addr(ship)}")
    if ship != bill:
        lines.append(f"🧾 Счёт: {fmt_addr(bill)}")
    lines.append(f"💰 Итого: €{total:.2f}")
    return "\n".join(lines)

async def _confirm(q, ctx):
    cart = ctx.user_data.get("cart",[]); client = ctx.user_data.get("client",{})
    ship = ctx.user_data.get("shipping",{}); bill = ctx.user_data.get("billing",{})
    await q.edit_message_text(_confirm_text(client,cart,ship,bill), reply_markup=kb([
        [("✅ Создать","place_order")],[("✏️ Редактировать","edit_order")],[("❌ Отмена","cancel_order")]]))
    return CONFIRM_ORDER

async def confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if q.data == "edit_order": return await _cart(q, ctx)
    if q.data == "cancel_order":
        ctx.user_data["cart"] = []
        await q.edit_message_text("❌ Отменён.")
        return await _main_menu(q, ctx)
    if q.data == "place_order":
        await q.edit_message_text("⏳ Создаю…")
        return await _create_order(q, ctx)
    return CONFIRM_ORDER


# ═══════════════════════════════════════════════════════════════
#  CREATE ORDER
# ═══════════════════════════════════════════════════════════════

def _shopify_addr(a, client):
    contact = client.get("contact_person","")
    parts = contact.split(" ",1) if contact else ["",""]
    return {
        "firstName": parts[0], "lastName": parts[1] if len(parts)>1 else "",
        "company": client.get("name",""),
        "address1": a.get("street",""), "city": a.get("city","Berlin"),
        "zip": a.get("zip",""), "countryCode": a.get("country","DE"),
        "phone": str(client.get("phone","")),
    }

async def _create_order(q, ctx):
    cart = ctx.user_data.get("cart",[]); client = ctx.user_data.get("client",{})
    ship = ctx.user_data.get("shipping",{}); bill = ctx.user_data.get("billing",{})
    rep = ctx.user_data.get("rep",{}); rep_name = rep.get("name","?")

    items = [{"title": f"{i['name']} {i.get('size','')}".strip(), "quantity": i["quantity"], "custom_price": i["price"]} for i in cart]

    tax_id = str(client.get("tax_id", ""))
    note_parts = [f"Sales rep: {rep_name}", client.get("name","")]
    if tax_id:
        note_parts.append(f"USt-IdNr: {tax_id}")

    custom_attributes = []
    if tax_id:
        custom_attributes.append({"key": "USt-IdNr", "value": tax_id})

    result = await shopify.create_draft_order(
        customer_id=client.get("shopify_customer_id") or None,
        line_items=items,
        shipping_address=_shopify_addr(ship, client),
        billing_address=_shopify_addr(bill, client),
        note=" | ".join(note_parts),
        tags=["telegram-bot"],
        email=str(client.get("email","")),
        custom_attributes=custom_attributes,
    )

    ok = not result.get("error")
    oid = sheets.get_next_order_id()
    sheets.save_order({
        "order_id": oid, "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "client_id": client.get("client_id",""), "client_name": client.get("name",""),
        "items": order_summary(cart), "total": f"{cart_total(cart):.2f}",
        "address": fmt_addr(ship), "sales_rep": rep_name,
        "shopify_draft_id": result.get("id","") if ok else "",
        "shopify_invoice_url": result.get("invoiceUrl","") if ok else "",
        "status": "draft" if ok else "saved",
    })
    sheets.update_client_after_order(client.get("client_id",""), order_summary(cart))

    if ok:
        ctx.user_data["last_invoice_url"] = result.get("invoiceUrl","")
        btns = []
        if result.get("invoiceUrl"): btns.append([("🔗 Invoice link","copy_invoice")])
        btns += [[("🆕 Новый заказ","new_order")],back_btn()]
        await q.edit_message_text(f"✅ Заказ создан!\n📋 {result.get('name','')}\n📦 {oid}\n💰 €{cart_total(cart):.2f}",
                                  reply_markup=kb(btns))
    else:
        await q.edit_message_text(f"✅ Сохранён\n📦 {oid}\n💰 €{cart_total(cart):.2f}\n⚠️ {result.get('error','')}",
                                  reply_markup=kb([[("🆕 Новый","new_order")],back_btn()]))
    return MAIN_MENU


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("/start")
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
#  WIRING
# ═══════════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    bh = CallbackQueryHandler(go_back, pattern="^back_")

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [CallbackQueryHandler(main_menu_cb, pattern="^(new_order|copy_invoice)$"), bh],
            SEARCH_CLIENT: [CallbackQueryHandler(search_start, pattern="^search_client$"),
                            CallbackQueryHandler(nc_start, pattern="^new_client$"), bh,
                            MessageHandler(filters.TEXT & ~filters.COMMAND, search_input)],
            SELECT_CLIENT: [CallbackQueryHandler(pick_client, pattern="^(pick_|search_client|new_client)"),
                            bh, MessageHandler(filters.TEXT & ~filters.COMMAND, search_input)],
            CLIENT_CARD: [CallbackQueryHandler(client_card_cb, pattern="^(start_order|search_client)$"), bh],
            CAT_L1: [CallbackQueryHandler(cat_l1_cb, pattern="^(l1_|show_cart)"), bh],
            CAT_L2: [CallbackQueryHandler(cat_l2_cb, pattern="^l2_"), bh],
            CAT_L3: [CallbackQueryHandler(cat_l3_cb, pattern="^l3_"), bh],
            CAT_SIZE: [CallbackQueryHandler(size_cb, pattern="^size_"),
                       CallbackQueryHandler(cat_l4_cb, pattern="^l4_"), bh],
            ENTER_QTY: [bh, MessageHandler(filters.TEXT & ~filters.COMMAND, enter_qty)],
            ENTER_PRICE: [bh, MessageHandler(filters.TEXT & ~filters.COMMAND, enter_price)],
            CART: [CallbackQueryHandler(cart_cb, pattern="^(add_more|remove_last|checkout|clear_cart)$"), bh],
            SELECT_ADDRESS: [CallbackQueryHandler(addr_cb, pattern="^addr_"), bh],
            SHIP_STREET: [bh, MessageHandler(filters.TEXT & ~filters.COMMAND, sh_street)],
            SHIP_ZIP: [bh, MessageHandler(filters.TEXT & ~filters.COMMAND, sh_zip)],
            SHIP_CITY: [CallbackQueryHandler(sh_city_btn, pattern="^shcity_"), bh,
                        MessageHandler(filters.TEXT & ~filters.COMMAND, sh_city_text)],
            SHIP_COUNTRY: [CallbackQueryHandler(sh_country_btn, pattern="^shc_"), bh,
                           MessageHandler(filters.TEXT & ~filters.COMMAND, sh_country_text)],
            INVOICE_SAME: [CallbackQueryHandler(invoice_cb, pattern="^invoice_"), bh],
            BILL_STREET: [bh, MessageHandler(filters.TEXT & ~filters.COMMAND, bl_street)],
            BILL_ZIP: [bh, MessageHandler(filters.TEXT & ~filters.COMMAND, bl_zip)],
            BILL_CITY: [CallbackQueryHandler(bl_city_btn, pattern="^blcity_"), bh,
                        MessageHandler(filters.TEXT & ~filters.COMMAND, bl_city_text)],
            BILL_COUNTRY: [CallbackQueryHandler(bl_country_btn, pattern="^blc_"), bh,
                           MessageHandler(filters.TEXT & ~filters.COMMAND, bl_country_text)],
            CONFIRM_ORDER: [CallbackQueryHandler(confirm_cb, pattern="^(place_order|edit_order|cancel_order)$"), bh],
            NC_NAME: [bh, MessageHandler(filters.TEXT & ~filters.COMMAND, nc_name)],
            NC_CONTACT: [bh, MessageHandler(filters.TEXT & ~filters.COMMAND, nc_contact)],
            NC_PHONE: [bh, MessageHandler(filters.TEXT & ~filters.COMMAND, nc_phone)],
            NC_EMAIL: [CallbackQueryHandler(nc_email_skip, pattern="^skip_email$"), bh,
                       MessageHandler(filters.TEXT & ~filters.COMMAND, nc_email_text)],
            NC_TAXID: [CallbackQueryHandler(nc_taxid_skip, pattern="^skip_taxid$"), bh,
                       MessageHandler(filters.TEXT & ~filters.COMMAND, nc_taxid_text)],
            NC_STREET: [bh, MessageHandler(filters.TEXT & ~filters.COMMAND, nc_street)],
            NC_ZIP: [bh, MessageHandler(filters.TEXT & ~filters.COMMAND, nc_zip)],
            NC_CITY: [CallbackQueryHandler(nc_city_btn, pattern="^nccity_"), bh,
                      MessageHandler(filters.TEXT & ~filters.COMMAND, nc_city_text)],
            NC_COUNTRY: [CallbackQueryHandler(nc_country_btn, pattern="^ncc_"), bh,
                         MessageHandler(filters.TEXT & ~filters.COMMAND, nc_country_text)],
            NC_CONFIRM: [CallbackQueryHandler(nc_confirm, pattern="^(save_client|redo_client)$"), bh],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )
    app.add_handler(conv)
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
       
