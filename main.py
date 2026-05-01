import asyncio
import sqlite3
import uuid
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ==============================================
# ⚙️ НАСТРОЙКИ - ЗАМЕНИ ЭТИ 4 ПЕРЕМЕННЫЕ
# ==============================================
BOT_TOKEN = "8738511395:AAF2BtIXebNnttWN1cpyFM9sD0nfHa4DLqA"                    # Получить у @BotFather
STARS_TOKEN = "ТВОЙ_ТОКЕН_ДЛЯ_STARS"             # Telegram Stars (у @BotFather /newpayment)
YUKASSA_TOKEN = "ТВОЙ_ТОКЕН_ДЛЯ_ЮКАССЫ"          # ЮKassa токен (у @BotFather /newpayment)
ADMIN_IDS = [5975768284, 8319217707, 6403805365]  # Твой Telegram ID (можно узнать у @userinfobot)
# ==============================================

# ========== ИНИЦИАЛИЗАЦИЯ ==========
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ========== СОСТОЯНИЯ ДЛЯ ДОБАВЛЕНИЯ ФИЛЬМА ==========
class AddMovieStates(StatesGroup):
    waiting_for_video = State()
    waiting_for_title = State()
    waiting_for_year = State()
    waiting_for_country = State()
    waiting_for_genres = State()
    waiting_for_keywords = State()
    waiting_for_rating_kp = State()
    waiting_for_rating_imdb = State()
    waiting_for_description = State()

# ========== БД ==========
def init_db():
    conn = sqlite3.connect("cinema.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS movies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT, 
        year TEXT, 
        rating_kp REAL, 
        rating_imdb REAL,
        country TEXT, 
        genres TEXT, 
        keywords TEXT,
        description TEXT, 
        video_file_id TEXT,
        created_at TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
        user_id INTEGER PRIMARY KEY,
        plan_type TEXT,
        start_date TIMESTAMP,
        end_date TIMESTAMP,
        active BOOLEAN
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments (
        payment_id TEXT PRIMARY KEY,
        user_id INTEGER,
        amount INTEGER,
        currency TEXT,
        status TEXT,
        created_at TIMESTAMP
    )""")
    conn.commit()
    conn.close()

def has_active_subscription(user_id: int) -> bool:
    conn = sqlite3.connect("cinema.db")
    c = conn.cursor()
    c.execute("SELECT end_date, active FROM subscriptions WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    end_date_str, active = row
    end_date = datetime.fromisoformat(end_date_str)
    return active and end_date > datetime.now()

def activate_subscription(user_id: int, plan_type: str):
    now = datetime.now()
    if plan_type == "1m":
        end = now + timedelta(days=30)
    elif plan_type == "3m":
        end = now + timedelta(days=90)
    elif plan_type == "12m":
        end = now + timedelta(days=365)
    else:
        return
    
    conn = sqlite3.connect("cinema.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO subscriptions (user_id, plan_type, start_date, end_date, active)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(user_id) DO UPDATE SET
            plan_type = excluded.plan_type,
            start_date = excluded.start_date,
            end_date = excluded.end_date,
            active = 1
    """, (user_id, plan_type, now.isoformat(), end.isoformat()))
    conn.commit()
    conn.close()

def save_payment_record(payment_id: str, user_id: int, amount: int, currency: str, status: str):
    conn = sqlite3.connect("cinema.db")
    c = conn.cursor()
    c.execute("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)", 
              (payment_id, user_id, amount, currency, status, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def update_payment_status(payment_id: str, status: str):
    conn = sqlite3.connect("cinema.db")
    c = conn.cursor()
    c.execute("UPDATE payments SET status = ? WHERE payment_id = ?", (status, payment_id))
    conn.commit()
    conn.close()

def search_movies(query: str):
    """Поиск по названию ИЛИ ключевым словам"""
    conn = sqlite3.connect("cinema.db")
    c = conn.cursor()
    c.execute("""
        SELECT id, title, year, rating_kp, rating_imdb, country, genres 
        FROM movies 
        WHERE title LIKE ? OR keywords LIKE ?
        LIMIT 10
    """, (f"%{query}%", f"%{query}%"))
    rows = c.fetchall()
    conn.close()
    return rows

def get_movie_by_id(movie_id: int):
    conn = sqlite3.connect("cinema.db")
    c = conn.cursor()
    c.execute("SELECT * FROM movies WHERE id = ?", (movie_id,))
    row = c.fetchone()
    conn.close()
    return row

def get_all_movies():
    conn = sqlite3.connect("cinema.db")
    c = conn.cursor()
    c.execute("SELECT id, title, year FROM movies ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def delete_movie_by_id(movie_id: int):
    conn = sqlite3.connect("cinema.db")
    c = conn.cursor()
    c.execute("DELETE FROM movies WHERE id = ?", (movie_id,))
    conn.commit()
    conn.close()

def get_movies_count():
    conn = sqlite3.connect("cinema.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM movies")
    count = c.fetchone()[0]
    conn.close()
    return count

def get_active_subscriptions_count():
    conn = sqlite3.connect("cinema.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM subscriptions WHERE active = 1 AND end_date > ?", (datetime.now().isoformat(),))
    count = c.fetchone()[0]
    conn.close()
    return count

# ========== КЛАВИАТУРЫ ==========
def get_tariffs_keyboard(movie_id: int = None):
    movie_param = f"_{movie_id}" if movie_id else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 месяц — 99₽", callback_data=f"tariff_1m{movie_param}")],
        [InlineKeyboardButton(text="3 месяца — 199₽", callback_data=f"tariff_3m{movie_param}")],
        [InlineKeyboardButton(text="12 месяцев — 499₽", callback_data=f"tariff_12m{movie_param}")]
    ])

def get_payment_methods_keyboard(plan_type: str, movie_id: int = None):
    movie_param = f"_{movie_id}" if movie_id else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay_stars_{plan_type}{movie_param}")],
        [InlineKeyboardButton(text="💳 Банковская карта / СБП", callback_data=f"pay_yk_{plan_type}{movie_param}")],
        [InlineKeyboardButton(text="🔙 Назад к тарифам", callback_data="back_to_tariffs")]
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить фильм", callback_data="admin_add_movie")],
        [InlineKeyboardButton(text="📋 Список фильмов", callback_data="admin_list_movies")],
        [InlineKeyboardButton(text="🗑️ Удалить фильм", callback_data="admin_delete_movie")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]
    ])

# ========== INLINE ПОИСК ==========
@dp.inline_query()
async def inline_search(inline_query: types.InlineQuery):
    query = inline_query.query.strip()
    if len(query) < 2:
        return
    
    movies = search_movies(query)
    results = []
    for movie in movies:
        movie_id, title, year, kp, imdb, country, genres = movie
        results.append(InlineQueryResultArticle(
            id=str(movie_id),
            title=title,
            description=f"{year} | КП:{kp or '?'} | IMDB:{imdb or '?'} | {country}",
            input_message_content=InputTextMessageContent(
                message_text=f"🎬 *{title}* ({year})\n⭐ КП: {kp or '?'} | IMDB: {imdb or '?'}\n🌍 {country}\n🎭 {genres}",
                parse_mode=ParseMode.MARKDOWN
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎬 Смотреть (подписка)", callback_data=f"watch_{movie_id}")],
                [InlineKeyboardButton(text="💎 Купить подписку от 99₽", callback_data=f"buy_subscription_{movie_id}")]
            ])
        ))
    await inline_query.answer(results, cache_time=1)

# ========== ОБРАБОТКА ПРОСМОТРА ==========
@dp.callback_query(lambda c: c.data.startswith("watch_"))
async def watch_movie(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    movie_id = int(callback.data.split("_")[1])
    movie = get_movie_by_id(movie_id)
    
    if not movie:
        await callback.message.answer("❌ Фильм не найден")
        await callback.answer()
        return
    
    video_file_id = movie[8]  # video_file_id поле
    
    if has_active_subscription(user_id):
        try:
            await bot.send_video(chat_id=user_id, video=video_file_id, caption=f"🎬 *{movie[1]}*\n🍿 Приятного просмотра!", parse_mode=ParseMode.MARKDOWN)
            await callback.answer()
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка при отправке видео: {e}")
    else:
        await callback.message.answer(
            "🔒 *У вас нет активной подписки*\n\nОформите доступ к библиотеке фильмов:",
            reply_markup=get_tariffs_keyboard(movie_id),
            parse_mode=ParseMode.MARKDOWN
        )
        await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("buy_subscription_"))
async def buy_subscription(callback: types.CallbackQuery):
    movie_id = int(callback.data.split("_")[2])
    await callback.message.edit_text(
        "💳 *Магазин подписок*\n\nВыберите тариф на 1, 3 или 12 месяцев:",
        reply_markup=get_tariffs_keyboard(movie_id),
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

# ========== ВЫБОР ТАРИФА ==========
@dp.callback_query(lambda c: c.data.startswith("tariff_"))
async def handle_tariff_selection(callback: types.CallbackQuery):
    data = callback.data.split("_")
    plan_type = data[1]
    movie_id = int(data[2]) if len(data) > 2 else None
    
    prices = {"1m": "99₽", "3m": "199₽", "12m": "499₽"}
    
    await callback.message.edit_text(
        f"💰 *Тариф: {plan_type[:1] if plan_type=='1m' else plan_type[:1] if plan_type=='3m' else '12'} месяц(а)*\n\n"
        f"Сумма: {prices[plan_type]}\n\n"
        f"Выберите способ оплаты:",
        reply_markup=get_payment_methods_keyboard(plan_type, movie_id),
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "back_to_tariffs")
async def back_to_tariffs(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "💳 *Магазин подписок*\n\nВыберите тариф на 1, 3 или 12 месяцев:",
        reply_markup=get_tariffs_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

# ========== ОПЛАТА ==========
@dp.callback_query(lambda c: c.data.startswith("pay_stars_"))
async def pay_with_stars(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    plan_type = parts[2]
    movie_id = int(parts[3]) if len(parts) > 3 else None
    user_id = callback.from_user.id
    
    prices = {"1m": 99, "3m": 199, "12m": 499}
    months = {"1m": 1, "3m": 3, "12m": 12}
    
    payment_id = str(uuid.uuid4())
    save_payment_record(payment_id, user_id, prices[plan_type], "STARS", "pending")
    
    await bot.send_invoice(
        chat_id=user_id,
        title=f"Подписка на {months[plan_type]} месяц(а)",
        description=f"Доступ ко всем фильмам на {months[plan_type]} месяц(а)",
        payload=f"stars_{plan_type}_{user_id}_{payment_id}",
        provider_token=STARS_TOKEN,
        currency="XTR",
        prices=[LabeledPrice(label="Подписка", amount=prices[plan_type])],
        start_parameter="cinema_subscription"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("pay_yk_"))
async def pay_with_yukassa(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    plan_type = parts[2]
    movie_id = int(parts[3]) if len(parts) > 3 else None
    user_id = callback.from_user.id
    
    prices = {"1m": 99, "3m": 199, "12m": 499}
    months = {"1m": 1, "3m": 3, "12m": 12}
    
    payment_id = str(uuid.uuid4())
    save_payment_record(payment_id, user_id, prices[plan_type], "RUB", "pending")
    
    await bot.send_invoice(
        chat_id=user_id,
        title=f"Подписка на {months[plan_type]} месяц(а)",
        description=f"Доступ ко всем фильмам на {months[plan_type]} месяц(а)",
        payload=f"yukassa_{plan_type}_{user_id}_{payment_id}",
        provider_token=YUKASSA_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="Подписка", amount=prices[plan_type] * 100)],
        start_parameter="cinema_subscription"
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout: PreCheckoutQuery):
    await pre_checkout.answer(ok=True)

@dp.message(lambda msg: msg.successful_payment)
async def process_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    parts = payload.split("_")
    payment_type = parts[0]
    plan_type = parts[1]
    user_id = int(parts[2])
    payment_id = parts[3]
    
    activate_subscription(user_id, plan_type)
    update_payment_status(payment_id, "completed")
    
    plan_names = {"1m": "1 месяц", "3m": "3 месяца", "12m": "12 месяцев"}
    method = "Telegram Stars" if payment_type == "stars" else "ЮKassa (карта/СБП)"
    
    await message.answer(
        f"✅ *Оплата через {method} прошла успешно!*\n\n"
        f"Подписка *{plan_names[plan_type]}* активирована.\n\n"
        f"🍿 *Приятного просмотра!*\n\n"
        f"👇 *Нажми на кнопку ниже*, чтобы сразу начать поиск фильмов:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🔍 Найти фильм (просто нажми и введи название)",
                switch_inline_query_current_chat=""
            )],
            [InlineKeyboardButton(
                text="📽️ Искать фильм в любом чате",
                switch_inline_query=""
            )],
            [InlineKeyboardButton(text="📊 Проверить статус подписки", callback_data="check_subscription")]
        ])
    )

# ========== СТАТУС ПОДПИСКИ ==========
@dp.callback_query(lambda c: c.data == "check_subscription")
async def check_subscription_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if has_active_subscription(user_id):
        conn = sqlite3.connect("cinema.db")
        c = conn.cursor()
        c.execute("SELECT plan_type, end_date FROM subscriptions WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        plan_type, end_date_str = row
        end_date = datetime.fromisoformat(end_date_str)
        days_left = (end_date - datetime.now()).days
        
        plan_names = {"1m": "1 месяц", "3m": "3 месяца", "12m": "12 месяцев"}
        
        await callback.message.answer(
            f"✅ *Подписка активна*\n\n"
            f"📅 Тариф: {plan_names[plan_type]}\n"
            f"⏰ Осталось дней: {days_left}\n"
            f"📆 Действует до: {end_date.strftime('%d.%m.%Y')}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Продлить подписку", callback_data="extend_subscription")]
            ])
        )
    else:
        await callback.message.answer(
            "❌ *У вас нет активной подписки*\n\nОформите доступ ко всем фильмам:",
            reply_markup=get_tariffs_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "extend_subscription")
async def extend_subscription(callback: types.CallbackQuery):
    await callback.message.answer(
        "💳 *Продление подписки*\n\nВыберите тариф:",
        reply_markup=get_tariffs_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

# ========== АДМИН ПАНЕЛЬ ==========
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа к админ-панели")
        return
    
    await message.answer(
        "🔐 *Админ-панель*\n\nВыберите действие:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_admin_keyboard()
    )

@dp.callback_query(lambda c: c.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    movies_count = get_movies_count()
    subs_count = get_active_subscriptions_count()
    
    await callback.message.edit_text(
        f"📊 *Статистика*\n\n"
        f"🎬 Фильмов в базе: {movies_count}\n"
        f"👥 Активных подписок: {subs_count}\n"
        f"💰 Доход: требуется интеграция с платёжной системой",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="back_to_admin")]
        ])
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_list_movies")
async def admin_list_movies(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    movies = get_all_movies()
    if not movies:
        await callback.message.edit_text(
            "📭 В базе пока нет фильмов\n\nИспользуйте «➕ Добавить фильм» для добавления",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")]
            ])
        )
        await callback.answer()
        return
    
    text = "📋 *Список фильмов:*\n\n"
    for movie in movies[:20]:  # Показываем первые 20
        text += f"🎬 ID: `{movie[0]}` | {movie[1]} ({movie[2]})\n"
    
    if len(movies) > 20:
        text += f"\n...и ещё {len(movies) - 20} фильмов"
    
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Удалить фильм по ID", callback_data="admin_delete_movie")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")]
        ])
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_delete_movie")
async def admin_delete_movie_prompt(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🗑️ *Удаление фильма*\n\nВведите ID фильма, который нужно удалить.\n\n"
        "Список фильмов с ID можно посмотреть в «📋 Список фильмов»",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")]
        ])
    )
    await callback.answer()
    
    # Устанавливаем состояние для ожидания ID
    await dp.fsm.storage.set_state(callback.from_user.id, "waiting_for_delete_id")

@dp.message(lambda msg: msg.text and msg.text.isdigit())
async def process_delete_movie(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    state = await dp.fsm.storage.get_state(message.from_user.id)
    if state != "waiting_for_delete_id":
        return
    
    movie_id = int(message.text)
    movie = get_movie_by_id(movie_id)
    
    if not movie:
        await message.answer("❌ Фильм с таким ID не найден")
        await dp.fsm.storage.set_state(message.from_user.id, None)
        return
    
    delete_movie_by_id(movie_id)
    await message.answer(f"✅ Фильм *{movie[1]}* (ID: {movie_id}) удалён", parse_mode=ParseMode.MARKDOWN)
    await dp.fsm.storage.set_state(message.from_user.id, None)

@dp.callback_query(lambda c: c.data == "admin_add_movie")
async def admin_add_movie(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🎬 *Добавление нового фильма*\n\n"
        "📹 *Шаг 1/9:* Отправьте видео файлом.\n\n"
        "Видео будет сохранено в Telegram, я получу его file_id.",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_video)
    await callback.answer()

@dp.message(AddMovieStates.waiting_for_video)
async def add_movie_video(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Нет доступа")
        await state.clear()
        return
    
    if not message.video:
        await message.answer("❌ Пожалуйста, отправьте видео файлом")
        return
    
    file_id = message.video.file_id
    await state.update_data(video_file_id=file_id)
    
    await message.answer(
        f"✅ Видео получено!\n`{file_id}`\n\n"
        f"📝 *Шаг 2/9:* Введите название фильма:",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_title)

@dp.message(AddMovieStates.waiting_for_title)
async def add_movie_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer(
        f"📅 *Шаг 3/9:* Введите год выпуска (например: 2023):",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_year)

@dp.message(AddMovieStates.waiting_for_year)
async def add_movie_year(message: types.Message, state: FSMContext):
    year = message.text.strip()
    if not year.isdigit() or len(year) != 4:
        await message.answer("❌ Введите корректный год (4 цифры, например: 2023)")
        return
    await state.update_data(year=year)
    await message.answer(
        f"🌍 *Шаг 4/9:* Введите страну производства (например: США, Россия):",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_country)

@dp.message(AddMovieStates.waiting_for_country)
async def add_movie_country(message: types.Message, state: FSMContext):
    await state.update_data(country=message.text.strip())
    await message.answer(
        f"🎭 *Шаг 5/9:* Введите жанры через запятую (например: драма, комедия, боевик):",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_genres)

@dp.message(AddMovieStates.waiting_for_genres)
async def add_movie_genres(message: types.Message, state: FSMContext):
    await state.update_data(genres=message.text.strip())
    await message.answer(
        f"🔑 *Шаг 6/9:* Введите *ключевые слова* через запятую\n\n"
        f"Пример: `форсаж, вин дизель, гонки, тюнинг, машины`\n\n"
        f"⚠️ По этим словам пользователи смогут найти фильм!",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_keywords)

@dp.message(AddMovieStates.waiting_for_keywords)
async def add_movie_keywords(message: types.Message, state: FSMContext):
    keywords = message.text.strip().lower()
    await state.update_data(keywords=keywords)
    await message.answer(
        f"⭐ *Шаг 7/9:* Введите рейтинг Кинопоиска (например: 7.5 или 0 если нет):",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_rating_kp)

@dp.message(AddMovieStates.waiting_for_rating_kp)
async def add_movie_rating_kp(message: types.Message, state: FSMContext):
    try:
        val = float(message.text.strip().replace(',', '.'))
        await state.update_data(rating_kp=val if val > 0 else None)
    except:
        await state.update_data(rating_kp=None)
    await message.answer(
        f"🎬 *Шаг 8/9:* Введите рейтинг IMDb (например: 8.2 или 0 если нет):",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_rating_imdb)

@dp.message(AddMovieStates.waiting_for_rating_imdb)
async def add_movie_rating_imdb(message: types.Message, state: FSMContext):
    try:
        val = float(message.text.strip().replace(',', '.'))
        await state.update_data(rating_imdb=val if val > 0 else None)
    except:
        await state.update_data(rating_imdb=None)
    await message.answer(
        f"📝 *Шаг 9/9:* Введите описание фильма (несколько предложений):",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_description)

@dp.message(AddMovieStates.waiting_for_description)
async def add_movie_description(message: types.Message, state: FSMContext):
    data = await state.update_data(description=message.text.strip())
    
    conn = sqlite3.connect("cinema.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO movies (title, year, rating_kp, rating_imdb, country, genres, keywords, description, video_file_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data['title'], data['year'], data['rating_kp'], data['rating_imdb'],
        data['country'], data['genres'], data['keywords'], data['description'],
        data['video_file_id'], datetime.now().isoformat()
    ))
    conn.commit()
    movie_id = c.lastrowid
    conn.close()
    
    await message.answer(
        f"✅ *Фильм успешно добавлен!*\n\n"
        f"🎬 Название: {data['title']}\n"
        f"🆔 ID: {movie_id}\n"
        f"🔑 Ключевые слова: {data['keywords']}\n\n"
        f"Теперь пользователи смогут найти его через поиск!",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.clear()

@dp.callback_query(lambda c: c.data == "back_to_admin")
async def back_to_admin(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "🔐 *Админ-панель*\n\nВыберите действие:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_admin_keyboard()
    )
    await callback.answer()

# ========== КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🎬 *Добро пожаловать в кино-бот!*\n\n"
        "🔍 *Как пользоваться:*\n"
        "1️⃣ Напишите `@имя_бота название фильма` в любом чате\n"
        "2️⃣ Выберите фильм из результатов поиска\n"
        "3️⃣ Оформите подписку (1/3/12 месяцев)\n"
        "4️⃣ Смотрите фильмы без ограничений!\n\n"
        "💳 *Тарифы:*\n"
        "• 1 месяц — 99₽\n"
        "• 3 месяца — 199₽\n"
        "• 12 месяцев — 499₽\n\n"
        "💸 *Способы оплаты:* Telegram Stars или банковская карта/СБП\n\n"
        "Нажмите /status для проверки подписки.\n\n"
        "👑 Администратор: /admin",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    user_id = message.from_user.id
    if has_active_subscription(user_id):
        conn = sqlite3.connect("cinema.db")
        c = conn.cursor()
        c.execute("SELECT plan_type, end_date FROM subscriptions WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        plan_type, end_date_str = row
        end_date = datetime.fromisoformat(end_date_str)
        days_left = (end_date - datetime.now()).days
        
        plan_names = {"1m": "1 месяц", "3m": "3 месяца", "12m": "12 месяцев"}
        
        await message.answer(
            f"✅ *Подписка активна*\n\n"
            f"📅 Тариф: {plan_names[plan_type]}\n"
            f"⏰ Осталось дней: {days_left}\n"
            f"📆 Действует до: {end_date.strftime('%d.%m.%Y')}",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await message.answer(
            "❌ *У вас нет активной подписки*\n\nОформите доступ:",
            reply_markup=get_tariffs_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )

# ========== ЗАПУСК ==========
async def main():
    init_db()
    
    bot_info = await bot.get_me()
    print(f"🤖 Бот запущен: @{bot_info.username}")
    print(f"⭐ Stars токен: {'✅ установлен' if STARS_TOKEN != 'ТВОЙ_ТОКЕН_ДЛЯ_STARS' else '❌ НЕ УСТАНОВЛЕН'}")
    print(f"💳 ЮKassa токен: {'✅ установлен' if YUKASSA_TOKEN != 'ТВОЙ_ТОКЕН_ДЛЯ_ЮКАССЫ' else '❌ НЕ УСТАНОВЛЕН'}")
    print(f"👑 Администраторы: {ADMIN_IDS}")
    print("\n🎬 Кино-бот готов к работе!")
    print("📌 Не забудь включить inline-режим у @BotFather: /setinline")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
