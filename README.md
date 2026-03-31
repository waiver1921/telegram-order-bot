# 🐟 Telegram Order Bot — Sales Rep (Phase 1)

Telegram-бот для менеджеров: поиск клиентов, сборка заказов из каталога, создание draft orders в Shopify.

## Архитектура

```
Telegram Bot  ←→  bot.py (python-telegram-bot)
                     ├── sheets_service.py  →  Google Sheets (клиенты, каталог, заказы)
                     └── shopify_service.py →  Shopify GraphQL Admin API (draft orders)
```

## Что умеет (Фаза 1)

- ✅ Авторизация по `telegram_id` (лист «Sales Reps»)
- ✅ Поиск клиента по имени/телефону/компании
- ✅ Каталог с кнопками: категория → товар → размер → количество
- ✅ Цены по группе клиента (retail / vip / b2b_standard / b2b_gold)
- ✅ Корзина с возможностью добавить ещё
- ✅ Выбор адреса из сохранённых или ввод нового
- ✅ Создание draft order в Shopify
- ✅ Запись заказа в лист «Заказы»
- ✅ Обновление `last_order_date` и `usual_order` клиента

## Быстрый старт

### 1. Клонируйте / скопируйте проект

```bash
cd telegram-order-bot
```

### 2. Установите зависимости

```bash
pip install -r requirements.txt
```

### 3. Настройте credentials

#### Telegram Bot
1. Напишите `@BotFather` → `/newbot` → сохраните токен

#### Google Sheets
1. [Google Cloud Console](https://console.cloud.google.com/) → создайте проект
2. Включите **Google Sheets API**
3. Создайте **Service Account** → скачайте JSON-ключ → сохраните как `credentials.json`
4. Скопируйте email сервис-аккаунта (типа `bot@project.iam.gserviceaccount.com`)
5. Откройте Google Sheet → Share → добавьте этот email с правом Edit

#### Shopify
1. Shopify Admin → Settings → Apps → Develop apps → Create app
2. Scopes: `write_draft_orders`, `read_products`, `read_customers`, `write_customers`
3. Install → сохраните Admin API access token

### 4. Создайте `.env`

```bash
cp .env.example .env
# Заполните все значения
```

### 5. Подготовьте Google Sheet

Создайте 4 листа с **точными именами** и заголовками:

**Лист «Клиенты»:**
```
client_id | name | contact_person | phone | email | telegram_id | price_group | address_1 | address_2 | address_label_1 | address_label_2 | notes | shopify_customer_id | usual_order | last_order_date
```

**Лист «Каталог»:**
```
product_id | category | name | variant | display_name | price_retail | price_vip | price_b2b_standard | price_b2b_gold | shopify_variant_id | in_stock | sort_order
```

**Лист «Заказы»:**
```
order_id | date | client_id | client_name | items | total | price_group | custom_prices | address | sales_rep | shopify_draft_id | shopify_invoice_url | status
```

**Лист «Sales Reps»:**
```
telegram_id | name | role | can_set_custom_price | max_discount_pct
```

> ⚠️ Добавьте свой `telegram_id` в лист «Sales Reps» для тестирования. Узнать ID: напишите `@userinfobot` в Telegram.

### 6. Запустите

```bash
python bot.py
```

## Структура файлов

```
telegram-order-bot/
├── bot.py              # Главный файл — все хэндлеры бота
├── config.py           # Загрузка переменных окружения
├── sheets_service.py   # Работа с Google Sheets
├── shopify_service.py  # Shopify GraphQL API
├── requirements.txt    # Python-зависимости
├── .env.example        # Шаблон переменных окружения
└── README.md           # Этот файл
```

## Flow бота

```
/start
  → Проверка telegram_id в Sales Reps
  → Главное меню: [Новый заказ] [Мои заказы]
      → Поиск клиента (текстовый ввод)
          → Выбор клиента → Карточка
              → Категория → Товар → Размер → Количество
                  → Корзина: [Добавить ещё] [Оформить]
                      → Выбор адреса
                          → Подтверждение
                              → Shopify draft order + лог в Sheets
```

## Что будет в Фазе 2

- Кастомные цены для sales rep (с лимитом скидки)
- Повтор последнего заказа
- Сохранение новых адресов
- Отправка invoice через Shopify
- Раздел «Мои заказы»
