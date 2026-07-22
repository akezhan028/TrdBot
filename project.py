#!/usr/bin/env python3

"""
Threads Bot - CS50P Final Project

Автоматический ИИ-помощник для соцсети Threads: находит посты по темам,
фильтрует их по ключевым словам и через OpenAI, генерирует релевантные
комментарии и публикует их через Playwright. Управление - через GUI (tkinter).

Структура под требования CS50P:
  * main() и несколько функций верхнего уровня с чистой логикой;
  * эти функции покрыты тестами в test_project.py (pytest);
  * тяжёлые/GUI-зависимости импортируются мягко, чтобы модуль можно было
    импортировать и тестировать в любом окружении.
"""

import threading
import time
import random
import json
import os
import sys
import configparser
import signal
from datetime import datetime
import hashlib

if sys.platform.startswith('win'):
    os.environ['PYTHONIOENCODING'] = 'utf-8'

# GUI (tkinter). Импорт мягкий: без него можно импортировать модуль и
# запускать тесты чистых функций, но нельзя запустить графический интерфейс.
try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, filedialog, messagebox
    GUI_AVAILABLE = True
except ImportError as e:
    GUI_AVAILABLE = False
    GUI_IMPORT_ERROR = str(e)

try:
    import pandas as pd
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    from openai import OpenAI
    DEPENDENCIES_OK = True
except ImportError as e:
    DEPENDENCIES_OK = False
    MISSING_DEPS = str(e)

LIKE_LABELS = ["Нравится", "Like"]
REPLY_LABELS = ["Ответ", "Reply"]

# Карта замен эмодзи на текстовые метки (для консольных логов Windows).
EMOJI_REPLACEMENTS = {
    '🤖': '[BOT]', '🚀': '[START]', '✅': '[OK]', '❌': '[ERROR]',
    '⚠️': '[WARN]', '📱': '[PHONE]', '💬': '[MSG]', '👍': '[LIKE]',
    '🎯': '[TARGET]', '📊': '[STATS]', '🔄': '[RELOAD]', '⏳': '[WAIT]',
    '🛑': '[STOP]', '📋': '[LIST]', '🎉': '[SUCCESS]', '⚪': '[SKIP]',
    '🔍': '[SEARCH]', '📄': '[FILE]', '💾': '[SAVE]', '📏': '[MEASURE]',
    '🖱️': '[MOUSE]', '📜': '[SCROLL]', '🔑': '[KEY]', '🚨': '[ALERT]',
    '🧠': '[AI]', '🔬': '[ANALYZE]'
}

# Инструкции по тону для генерации комментариев.
TONE_INSTRUCTIONS = {
    "friendly": "Пиши дружелюбно и тепло",
    "professional": "Пиши профессионально и сдержанно",
    "enthusiastic": "Пиши с энтузиазмом и воодушевлением",
    "supportive": "Пиши поддерживающе и ободряюще",
    "neutral": "Пиши нейтрально и вежливо",
}


def svg_selector(labels):
    """Собирает CSS-селектор svg по списку возможных aria-label"""
    return ", ".join(f"svg[aria-label='{label}']" for label in labels)


# ---------------------------------------------------------------------------
# Функции верхнего уровня с чистой логикой (тестируются в test_project.py).
# ---------------------------------------------------------------------------

def clean_message(message):
    """Заменяет эмодзи в строке на текстовые метки вида [OK], [ERROR] и т.д."""
    for emoji, text in EMOJI_REPLACEMENTS.items():
        message = message.replace(emoji, text)
    return message


def parse_keywords(keywords_str):
    """Превращает строку "a, b , c" в список ключевых слов в нижнем регистре
    без пустых элементов: ['a', 'b', 'c']."""
    return [kw.strip().lower() for kw in keywords_str.split(',') if kw.strip()]


def matches_topic_filter(post_text, keywords, min_matches=1):
    """Проверяет, содержит ли текст поста хотя бы min_matches ключевых слов.
    Пустой список ключевых слов означает "фильтр выключен" -> True."""
    if not keywords:
        return True
    post_text_lower = post_text.lower()
    matches = [keyword for keyword in keywords if keyword in post_text_lower]
    return len(matches) >= min_matches


def make_post_id(author, text):
    """Возвращает стабильный SHA-256 идентификатор поста по автору и тексту.
    Нужен, чтобы не комментировать один и тот же пост дважды."""
    unique_str = f"{author}:{text}"
    return hashlib.sha256(unique_str.encode('utf-8')).hexdigest()


def is_valid_api_key(api_key):
    """Проверяет, похож ли ключ на валидный ключ OpenAI (начинается с 'sk-')."""
    return bool(api_key) and api_key.startswith('sk-')


def select_tone_instruction(tone):
    """Возвращает текстовую инструкцию для заданного тона; для неизвестного
    тона используется нейтральная инструкция."""
    return TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["neutral"])


class ThreadsBotCore:
    """Основной класс бота"""
    def __init__(self, log_callback=None, update_progress_callback=None,
                 dialog_callback=None, finished_callback=None):
        self.log_callback = log_callback or print
        self.update_progress_callback = update_progress_callback
        self.dialog_callback = dialog_callback
        self.finished_callback = finished_callback
        self.running = True

        self.processed_posts = {}

        self.attempted_posts = set()
        self.processed_file = 'processed_posts.json'
        self.load_processed_posts()
        self.logs = []

        self.comments_made = 0
        self.posts_checked = 0
        self.posts_filtered = 0
        self.ai_filtered = 0

        self.api_key = ""
        self.max_comments = 5
        self.typing_speed = 0.12
        self.min_delay = 30
        self.max_delay = 60
        self.min_post_length = 40
        self.ai_prompt = ""
        self.chrome_profile = ""
        self.max_tokens_comments = 300
        self.topic_filter_enabled = True
        self.keywords = []
        self.match_type = 'any'
        self.min_keyword_matches = 1
        self.use_ai_filter = True
        self.scroll_attempts = 5
        self.scroll_pause = 3000
        self.client = None

        self.browser_keep_open = False

    def update_progress(self, current_action="", progress_percent=0):
        """Обновление прогресса"""
        if self.update_progress_callback:
            self.update_progress_callback(current_action, progress_percent, self.comments_made, self.posts_checked, self.posts_filtered)

    def log(self, message):
        """Логирование с очисткой от эмодзи"""
        clean_message = self.clean_message(message)
        if self.log_callback:
            self.log_callback(clean_message)

    def clean_message(self, message):
        """Убираем эмодзи и заменяем на текст (см. clean_message верхнего уровня)."""
        return clean_message(message)

    def load_processed_posts(self):
        """Загрузка обработанных постов из файла"""
        if os.path.exists(self.processed_file):
            try:
                with open(self.processed_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.processed_posts = dict.fromkeys(data)
                self.log(f"[LOAD] Загружено {len(self.processed_posts)} обработанных постов")
            except Exception as e:
                self.log(f"[ERROR] Ошибка загрузки processed_posts: {str(e)}")
                self.processed_posts = {}
        else:
            self.processed_posts = {}

    def save_processed_posts(self):
        """Сохранение обработанных постов в файл"""
        try:
            if len(self.processed_posts) > 1000:

                recent_keys = list(self.processed_posts)[-1000:]
                self.processed_posts = dict.fromkeys(recent_keys)

            with open(self.processed_file, 'w', encoding='utf-8') as f:
                json.dump(list(self.processed_posts), f, ensure_ascii=False)
            self.log(f"[SAVE] Сохранено {len(self.processed_posts)} обработанных постов")
        except Exception as e:
            self.log(f"[ERROR] Ошибка сохранения processed_posts: {str(e)}")

    def load_config_from_gui(self, config):
        """Загрузка конфигурации из GUI"""
        self.api_key = config.get('api_key', '')
        self.max_comments = config.get('max_comments', 5)
        self.min_delay = config.get('min_delay', 30)
        self.max_delay = config.get('max_delay', 60)
        self.typing_speed = config.get('typing_speed', 0.12)
        self.chrome_profile = config.get('chrome_path', '')
        self.ai_prompt = config.get('ai_prompt', '')
        self.max_tokens_comments = config.get('max_tokens', 300)
        self.topic_filter_enabled = config.get('filter_enabled', True)
        keywords_str = config.get('keywords', '')
        self.keywords = parse_keywords(keywords_str)
        self.use_ai_filter = config.get('use_ai_filter', True)

        if self.api_key:
            try:
                self.client = OpenAI(api_key=self.api_key)
                self.log("[OK] OpenAI клиент инициализирован")
            except Exception as e:
                self.log(f"[ERROR] Ошибка OpenAI: {str(e)}")

    def matches_topic_filter(self, post_text):
        """Проверка соответствия темам (см. matches_topic_filter верхнего уровня)."""
        if not self.topic_filter_enabled:
            return True
        return matches_topic_filter(post_text, self.keywords, self.min_keyword_matches)

    def analyze_post_with_ai(self, post_text, post_author):
        """ИИ анализ поста с улучшенным парсингом JSON"""
        if not self.client or not self.use_ai_filter:
            return True, "neutral"

        try:
            self.log("[AI] Анализирую пост...")
            self.update_progress("Анализ поста через ИИ...", int((self.comments_made / self.max_comments) * 100))

            analysis_prompt = f"""
            Проанализируй пост и верни ТОЛЬКО JSON без дополнительного текста:

            Пост: "{post_text}"
            Автор: {post_author}
            Ключевые слова: {', '.join(self.keywords) if self.keywords else 'любые'}

            Верни строго в формате:
            {{"should_comment": true, "reason": "краткая причина", "tone": "friendly", "relevance_score": 85}}

            Возможные тона: friendly, professional, enthusiastic, supportive, neutral
            """

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Отвечай ТОЛЬКО валидным JSON без markdown блоков, без пояснений, без дополнительного текста."},
                    {"role": "user", "content": analysis_prompt}
                ],
                max_tokens=150,
                temperature=0.3
            )

            content = response.choices[0].message.content
            analysis_text = (content or "").strip()

            if analysis_text.startswith('```'):
                lines = analysis_text.split('\n')
                if len(lines) > 1:
                    analysis_text = '\n'.join(lines[1:])
                else:
                    analysis_text = analysis_text[3:]

            if analysis_text.endswith('```'):
                lines = analysis_text.split('\n')
                if len(lines) > 1:
                    analysis_text = '\n'.join(lines[:-1])
                else:
                    analysis_text = analysis_text[:-3]

            analysis_text = analysis_text.strip()

            try:
                analysis = json.loads(analysis_text)
                should_comment = analysis.get('should_comment', False)
                reason = analysis.get('reason', 'Нет причины')
                tone = analysis.get('tone', 'neutral')
                relevance_score = analysis.get('relevance_score', 0)

                self.log(f"[AI] Релевантность: {relevance_score}%, Тон: {tone}")
                self.log(f"[AI] Решение: {'Комментировать' if should_comment else 'Пропустить'} - {reason}")

                return should_comment, tone

            except json.JSONDecodeError:
                self.log(f"[WARN] Ошибка JSON: {analysis_text[:100]}...")
                return self.simple_fallback_analysis(post_text)

        except Exception as e:
            self.log(f"[ERROR] Ошибка анализа поста: {str(e)}")
            return True, "neutral"

    def simple_fallback_analysis(self, post_text):
        """Простой анализ если ИИ не работает"""
        post_lower = post_text.lower()

        negative_words = ['спам', 'реклама', 'продаю', 'покупаю', 'скидка', 'акция']
        if any(word in post_lower for word in negative_words):
            self.log("[FALLBACK] Пропуск: обнаружены негативные слова")
            return False, "neutral"

        if self.keywords:
            matches = sum(1 for keyword in self.keywords if keyword.lower() in post_lower)
            if matches >= self.min_keyword_matches:
                self.log(f"[FALLBACK] Принят: найдено {matches} ключевых слов")
                return True, "friendly"

        should_comment = random.random() < 0.3
        self.log(f"[FALLBACK] Случайный выбор: {'принят' if should_comment else 'пропущен'}")
        return should_comment, "neutral"

    def generate_comment(self, post_text, tone="neutral"):
        """Генерация комментария через OpenAI с учетом тона"""
        if not self.client:
            return "Интересный пост!"

        try:
            self.log("[AI] Генерирую комментарий...")
            self.update_progress("Генерация комментария...", int((self.comments_made / self.max_comments) * 100))

            tone_instruction = select_tone_instruction(tone)

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": f"{self.ai_prompt} {tone_instruction}. ОБЯЗАТЕЛЬНО заканчивай предложения полностью."},
                    {"role": "user", "content": f"Комментарий к посту: {post_text}"}
                ],
                max_tokens=self.max_tokens_comments,
                temperature=0.7
            )
            content = response.choices[0].message.content
            comment = (content or "").strip()
            if not comment:
                return "Интересный пост!"
            self.log(f"[AI] Комментарий ({tone}): {comment}")
            return comment
        except Exception as e:
            self.log(f"[ERROR] Ошибка генерации: {str(e)}")
            return "Интересный пост!"

    def setup_browser(self):
        """Инициализация браузера"""
        if not DEPENDENCIES_OK:
            self.log(f"[ERROR] Отсутствуют зависимости: {MISSING_DEPS}")
            self.log("[INFO] Установите: pip install playwright openai pandas openpyxl")
            self.log("[INFO] Затем выполните: playwright install chromium")
            self.show_dependencies_error()
            return None, None, None

        try:
            self.log("[BROWSER] Запускаю браузер...")
            self.update_progress("Запуск браузера...", 5)
            playwright = sync_playwright().start()

            if not self.chrome_profile or not os.path.exists(self.chrome_profile):
                self.log("[WARN] Неверный путь к профилю Chrome, использую профиль по умолчанию")
                self.chrome_profile = ""

            if self.chrome_profile:
                context = playwright.chromium.launch_persistent_context(
                    self.chrome_profile,
                    channel="chrome",
                    headless=False,
                    args=["--start-maximized"]
                )
            else:
                browser = playwright.chromium.launch(
                    channel="chrome",
                    headless=False,
                    args=["--start-maximized"]
                )
                context = browser.new_context()

            page = context.new_page()
            self.update_progress("Браузер запущен", 10)
            return playwright, context, page
        except Exception as e:
            self.log(f"[ERROR] Ошибка браузера: {str(e)}")
            self.show_browser_error(str(e))
            return None, None, None

    def _dialog(self, kind, title, message):
        """Показ диалога через главный поток GUI (tkinter не потокобезопасен)"""
        if self.dialog_callback:
            return self.dialog_callback(kind, title, message)
        return None

    def show_dependencies_error(self):
        """Показать ошибку отсутствия зависимостей"""
        self._dialog(
            "error",
            "Отсутствуют зависимости",
            f"Для работы бота необходимо установить зависимости:\n\n"
            f"Ошибка: {MISSING_DEPS}\n\n"
            f"Выполните в командной строке:\n"
            f"pip install playwright openai pandas openpyxl\n"
            f"playwright install chromium\n\n"
            f"После установки перезапустите приложение."
        )

    def show_browser_error(self, error_msg):
        """Показать ошибку браузера"""
        self._dialog(
            "error",
            "Ошибка запуска браузера",
            f"Не удалось запустить браузер:\n\n"
            f"{error_msg}\n\n"
            f"Возможные решения:\n"
            f"-  Проверьте путь к профилю Chrome\n"
            f"-  Закройте все окна Chrome\n"
            f"-  Установите Chromium: playwright install chromium\n"
            f"-  Попробуйте запустить без профиля (оставьте поле пустым)"
        )

    def setup_threads(self, page):
        """Открытие и проверка авторизации в Threads"""
        try:
            self.log("[THREADS] Открываю Threads...")
            self.update_progress("Открываю Threads...", 15)
            page.goto("https://www.threads.net", timeout=120_000)
            time.sleep(5)
            if not self.running:
                return False
            page.wait_for_selector(svg_selector(LIKE_LABELS), timeout=15_000)
            self.log("[OK] Авторизация успешна!")
            self.update_progress("Авторизация успешна", 20)
            return True
        except PlaywrightTimeoutError:
            self.log("[AUTH] Требуется ручная авторизация")
            self.log("[WAIT] Войдите в свой аккаунт в открывшемся браузере...")
            return self.wait_for_manual_auth(page)

    def wait_for_manual_auth(self, page):
        """Ожидание ручной авторизации"""
        result = self._dialog(
            "okcancel",
            "Требуется авторизация",
            "Войдите в свой аккаунт Threads в открывшемся браузере.\n\n"
            "После успешного входа нажмите 'OK' для продолжения работы бота.\n\n"
            "Или нажмите 'Отмена' для остановки."
        )

        if result == 'ok':
            self.log("[OK] Пользователь подтвердил авторизацию")
            self.update_progress("Авторизация подтверждена", 20)
            self.show_start_notification()
            return True
        else:
            self.log("[STOP] Пользователь отменил авторизацию")
            self.running = False
            return False

    def show_start_notification(self):
        """Показать уведомление о начале работы бота"""
        self._dialog(
            "info",
            "Бот начинает работу!",
            "Отлично! Авторизация успешна.\n\n"
            "Бот сейчас начнет автоматически:\n"
            "-  Анализировать посты через ИИ\n"
            "-  Искать подходящие посты по темам\n"
            "-  Генерировать релевантные комментарии\n"
            "-  Ставить лайки и отправлять комментарии\n\n"
            "Следите за прогрессом в интерфейсе.\n"
            "Для остановки нажмите кнопку 'ОСТАНОВИТЬ'."
        )

    def show_finish_notification(self):
        """Показать уведомление о завершении работы"""
        self._dialog(
            "info",
            "Работа завершена!",
            f"Бот завершил работу.\n\n"
            f"Статистика:\n"
            f"-  Комментариев сделано: {self.comments_made}\n"
            f"-  Постов проверено: {self.posts_checked}\n"
            f"-  Постов отфильтровано: {self.posts_filtered}\n"
            f"-  ИИ отфильтровано: {self.ai_filtered}\n\n"
            f"Данные об обработанных постах сохранены."
        )

    def get_posts_on_screen(self, page):
        """Получение постов с экрана"""
        try:
            posts = page.query_selector_all("div[data-pressable-container='true']")
            visible_posts = [post for post in posts if post.is_visible()]
            self.log(f"[POSTS] Найдено постов: {len(visible_posts)}")
            return visible_posts
        except Exception as e:
            self.log(f"[ERROR] Ошибка получения постов: {str(e)}")
            return []

    def like_post(self, post_element):
        """Лайк поста"""
        try:
            like_btn = post_element.query_selector(svg_selector(LIKE_LABELS))
            if like_btn:
                like_btn.click()
                self.log("[LIKE] Лайк поставлен")
                time.sleep(random.uniform(1, 3))
                return True
        except:
            pass
        return False

    def post_comment(self, page, post_element, comment_text):
        """Отправка комментария"""
        try:
            self.update_progress("Отправка комментария...", int((self.comments_made / self.max_comments) * 100))
            reply_btn = post_element.query_selector(svg_selector(REPLY_LABELS))
            if not reply_btn:
                return False

            reply_btn.click()
            page.wait_for_selector("div[contenteditable='true']", timeout=10_000)
            comment_field = page.query_selector("div[contenteditable='true']")

            comment_field.click()
            comment_field.press("Control+A")
            comment_field.press("Backspace")
            time.sleep(0.5)

            for char in comment_text:
                if not self.running:
                    break
                comment_field.type(char, delay=self.typing_speed * 1000)
                if char == " ":
                    time.sleep(0.25)

            if not self.running:
                return False

            time.sleep(2)
            comment_field.press("Control+Enter")
            self.log("[SUCCESS] Комментарий отправлен!")
            time.sleep(2)
            return True
        except Exception as e:
            self.log(f"[ERROR] Ошибка отправки: {str(e)}")
            return False

    def scroll_and_load_content(self, page):
        """Скролл для загрузки новых постов"""
        try:
            self.log("[SCROLL] Загружаю новые посты...")
            self.update_progress("Загрузка новых постов...", int((self.comments_made / self.max_comments) * 100))
            previous_posts_count = len(page.query_selector_all("div[data-pressable-container='true']"))

            for i in range(3):
                page.mouse.wheel(0, 800)
                time.sleep(0.5)

            page.wait_for_timeout(self.scroll_pause)
            new_posts_count = len(page.query_selector_all("div[data-pressable-container='true']"))
            return new_posts_count > previous_posts_count
        except Exception as e:
            self.log(f"[ERROR] Ошибка скролла: {str(e)}")
            return False

    def find_and_process_posts(self, page):
        """Основная логика поиска и обработки постов"""
        self.log(f"[TARGET] Ищу {self.max_comments} постов по темам")
        self.update_progress("Поиск постов...", 25)
        if self.topic_filter_enabled:
            self.log(f"[FILTER] Ключевые слова: {', '.join(self.keywords)}")
        if self.use_ai_filter:
            self.log(f"[AI] ИИ анализ включен")

        while self.comments_made < self.max_comments and self.running:
            try:
                current_progress = int((self.comments_made / self.max_comments) * 75) + 25
                self.update_progress(f"Поиск постов ({self.comments_made}/{self.max_comments})", current_progress)

                posts_on_screen = self.get_posts_on_screen(page)
                if not posts_on_screen:
                    self.log("[WARN] Посты не найдены, перезагружаю...")
                    page.reload()
                    page.wait_for_timeout(5000)
                    continue

                found_matching_posts = False
                for post in posts_on_screen:
                    if self.comments_made >= self.max_comments or not self.running:
                        break

                    if not post.is_visible():
                        continue

                    post_text_elements = post.query_selector_all("span[dir='auto']")
                    post_text_parts = [t for elem in post_text_elements if (t := elem.inner_text().strip())]
                    post_text = " ".join(post_text_parts)

                    if len(post_text) < self.min_post_length:
                        continue

                    self.posts_checked += 1

                    author_elem = post.query_selector("a[role='link'] span")
                    post_author = author_elem.inner_text().strip() if author_elem else "unknown_user"

                    post_id = make_post_id(post_author, post_text)

                    if post_id in self.processed_posts or post_id in self.attempted_posts:
                        self.log(f"[SKIP] Пост {self.posts_checked}: уже обработан (ID: {post_id[:10]}...)")
                        continue

                    if not self.matches_topic_filter(post_text):
                        self.posts_filtered += 1
                        self.log(f"[SKIP] Пост {self.posts_checked}: не соответствует ключевым словам")
                        continue

                    should_comment, tone = self.analyze_post_with_ai(post_text, post_author)
                    if not should_comment:
                        self.ai_filtered += 1
                        self.log(f"[AI-SKIP] Пост {self.posts_checked}: отфильтрован ИИ")
                        continue

                    found_matching_posts = True

                    self.attempted_posts.add(post_id)

                    self.log(f"[OK] Пост {self.posts_checked} от {post_author} (тон: {tone})")
                    self.log(f"[TEXT] {post_text}")

                    if not self.running:
                        break

                    self.like_post(post)

                    if not self.running:
                        break

                    comment = self.generate_comment(post_text, tone)
                    if comment and self.post_comment(page, post, comment):

                        self.processed_posts[post_id] = None
                        self.save_processed_posts()
                        self.comments_made += 1
                        progress = int((self.comments_made / self.max_comments) * 75) + 25
                        self.update_progress(f"Комментарий {self.comments_made}/{self.max_comments} готов", progress)
                        self.log(f"[PROGRESS] Готово! ({self.comments_made}/{self.max_comments})")
                    else:
                        self.log(f"[WARN] Пост {self.posts_checked}: комментарий не отправлен, будет пропущен в этой сессии")

                    if self.comments_made < self.max_comments and self.running:
                        delay = random.uniform(self.min_delay, self.max_delay)
                        self.log(f"[WAIT] Пауза {int(delay)}с...")
                        self.update_progress(f"Пауза {int(delay)}с...", progress)
                        for _ in range(int(delay)):
                            if not self.running:
                                break
                            time.sleep(1)

                if not found_matching_posts and self.running:
                    if not self.scroll_and_load_content(page):
                        self.log("[WARN] Не удалось загрузить новые посты")
                    page.wait_for_timeout(3000)

            except Exception as e:
                if self.running:
                    self.log(f"[ERROR] Общая ошибка: {str(e)}")
                page.wait_for_timeout(2000)

    def run_bot(self, config):
        """Главная функция запуска бота"""
        try:
            self.load_config_from_gui(config)

            if not is_valid_api_key(self.api_key):
                self.log("[ERROR] Неверный OpenAI API ключ!")
                return

            playwright, context, page = self.setup_browser()
            if not page:
                return

            try:
                if self.setup_threads(page):
                    self.find_and_process_posts(page)
            finally:
                self.save_processed_posts()
                self.update_progress("Завершение работы", 100)
                if not self.browser_keep_open:
                    try:
                        context.close()
                        playwright.stop()
                    except:
                        pass
                self.show_finish_notification()
                self.log(f"[FINISH] Работа завершена! Комментариев: {self.comments_made}")
        finally:
            if self.finished_callback:
                self.finished_callback()


class ThreadsBotGUI:
    """GUI для бота Threads"""
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Threads Bot v2.0")
        self.root.geometry("850x950")
        self.root.configure(bg='#f0f0f0')

        self.bot = None
        self.bot_thread = None
        self.config_file = "threads_bot_config.ini"

        self.create_widgets()
        self.load_config()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_widgets(self):
        """Создание виджетов GUI"""

        header_frame = tk.Frame(self.root, bg='#2c3e50', height=60)
        header_frame.pack(fill='x', padx=0, pady=0)
        header_frame.pack_propagate(False)

        header_label = tk.Label(header_frame, text="🤖 Threads Bot v2.0 + ИИ",
                               font=('Arial', 16, 'bold'), fg='white', bg='#2c3e50')
        header_label.pack(expand=True)

        status_frame = tk.Frame(self.root, bg='#ecf0f1', height=120)
        status_frame.pack(fill='x', padx=10, pady=5)
        status_frame.pack_propagate(False)

        self.status_label = tk.Label(status_frame, text="Готов к работе",
                                    font=('Arial', 12, 'bold'), bg='#ecf0f1', fg='#2c3e50')
        self.status_label.pack(pady=5)

        self.progress_bar = ttk.Progressbar(status_frame, length=400, mode='determinate')
        self.progress_bar.pack(pady=5)

        stats_frame = tk.Frame(status_frame, bg='#ecf0f1')
        stats_frame.pack(fill='x', pady=5)

        self.comments_label = tk.Label(stats_frame, text="Комментариев: 0",
                                      font=('Arial', 10), bg='#ecf0f1', fg='#27ae60')
        self.comments_label.pack(side='left', padx=10)

        self.checked_label = tk.Label(stats_frame, text="Проверено: 0",
                                     font=('Arial', 10), bg='#ecf0f1', fg='#3498db')
        self.checked_label.pack(side='left', padx=10)

        self.filtered_label = tk.Label(stats_frame, text="Отфильтровано: 0",
                                      font=('Arial', 10), bg='#ecf0f1', fg='#e74c3c')
        self.filtered_label.pack(side='left', padx=10)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)

        self.settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.settings_frame, text="⚙️ Настройки")

        self.logs_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.logs_frame, text="📋 Логи")

        self.create_settings_tab()
        self.create_logs_tab()

    def create_settings_tab(self):
        """Создание вкладки настроек"""
        canvas = tk.Canvas(self.settings_frame)
        scrollbar = ttk.Scrollbar(self.settings_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        ai_frame = ttk.LabelFrame(scrollable_frame, text="🔑 OpenAI настройки", padding=10)
        ai_frame.pack(fill='x', padx=5, pady=5)

        ttk.Label(ai_frame, text="API ключ OpenAI:").pack(anchor='w')
        self.api_key_entry = ttk.Entry(ai_frame, width=60, show="*")
        self.api_key_entry.pack(fill='x', pady=2)

        ttk.Label(ai_frame, text="AI промпт для комментариев:").pack(anchor='w', pady=(10,0))
        self.ai_prompt_text = tk.Text(ai_frame, height=4, wrap=tk.WORD)
        self.ai_prompt_text.pack(fill='x', pady=2)
        self.ai_prompt_text.insert('1.0', "Ты пишешь комментарии к постам в Threads. Пиши естественно, как живой человек, избегай спам. Комментарий должен быть релевантным к посту.")

        ai_filter_frame = ttk.LabelFrame(scrollable_frame, text="🧠 ИИ анализ постов", padding=10)
        ai_filter_frame.pack(fill='x', padx=5, pady=5)

        self.use_ai_filter_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(ai_filter_frame, text="Включить ИИ анализ постов перед комментированием",
                       variable=self.use_ai_filter_var).pack(anchor='w')

        ttk.Label(ai_filter_frame, text="ИИ будет анализировать релевантность, тон и подходящность поста для комментирования",
                 font=('Arial', 9), foreground='gray').pack(anchor='w', pady=(5,0))

        comment_frame = ttk.LabelFrame(scrollable_frame, text="💬 Настройки комментариев", padding=10)
        comment_frame.pack(fill='x', padx=5, pady=5)

        ttk.Label(comment_frame, text="Максимум комментариев:").pack(anchor='w')
        self.max_comments_var = tk.StringVar(value="5")
        ttk.Entry(comment_frame, textvariable=self.max_comments_var, width=10).pack(anchor='w', pady=2)

        ttk.Label(comment_frame, text="Максимум токенов в комментарии:").pack(anchor='w', pady=(10,0))
        self.max_tokens_var = tk.StringVar(value="300")
        ttk.Entry(comment_frame, textvariable=self.max_tokens_var, width=10).pack(anchor='w', pady=2)

        timing_frame = ttk.LabelFrame(scrollable_frame, text="⏰ Тайминги", padding=10)
        timing_frame.pack(fill='x', padx=5, pady=5)

        ttk.Label(timing_frame, text="Мин. задержка между комментариями (сек):").pack(anchor='w')
        self.min_delay_var = tk.StringVar(value="30")
        ttk.Entry(timing_frame, textvariable=self.min_delay_var, width=10).pack(anchor='w', pady=2)

        ttk.Label(timing_frame, text="Макс. задержка между комментариями (сек):").pack(anchor='w', pady=(10,0))
        self.max_delay_var = tk.StringVar(value="60")
        ttk.Entry(timing_frame, textvariable=self.max_delay_var, width=10).pack(anchor='w', pady=2)

        ttk.Label(timing_frame, text="Скорость печати (сек на символ):").pack(anchor='w', pady=(10,0))
        self.typing_speed_var = tk.StringVar(value="0.12")
        ttk.Entry(timing_frame, textvariable=self.typing_speed_var, width=10).pack(anchor='w', pady=2)

        filter_frame = ttk.LabelFrame(scrollable_frame, text="🎯 Фильтр тем", padding=10)
        filter_frame.pack(fill='x', padx=5, pady=5)

        self.filter_enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(filter_frame, text="Включить фильтр по ключевым словам",
                       variable=self.filter_enabled_var).pack(anchor='w')

        ttk.Label(filter_frame, text="Ключевые слова (через запятую):").pack(anchor='w', pady=(10,0))
        self.keywords_entry = ttk.Entry(filter_frame, width=60)
        self.keywords_entry.pack(fill='x', pady=2)

        browser_frame = ttk.LabelFrame(scrollable_frame, text="🌐 Настройки браузера", padding=10)
        browser_frame.pack(fill='x', padx=5, pady=5)

        ttk.Label(browser_frame, text="Путь к профилю Chrome (опционально):").pack(anchor='w')
        chrome_path_frame = ttk.Frame(browser_frame)
        chrome_path_frame.pack(fill='x', pady=2)

        self.chrome_path_entry = ttk.Entry(chrome_path_frame, width=50)
        self.chrome_path_entry.pack(side='left', fill='x', expand=True)

        ttk.Button(chrome_path_frame, text="Обзор",
                  command=self.browse_chrome_profile).pack(side='right', padx=(5,0))

        control_frame = ttk.Frame(scrollable_frame)
        control_frame.pack(fill='x', padx=5, pady=20)

        self.start_button = ttk.Button(control_frame, text="🚀 ЗАПУСТИТЬ БОТА",
                                      command=self.start_bot)
        self.start_button.pack(side='left', padx=5)

        self.stop_button = ttk.Button(control_frame, text="🛑 ОСТАНОВИТЬ",
                                     command=self.stop_bot, state='disabled')
        self.stop_button.pack(side='left', padx=5)

        ttk.Button(control_frame, text="💾 Сохранить настройки",
                  command=self.save_config).pack(side='right', padx=5)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def create_logs_tab(self):
        """Создание вкладки логов"""
        self.logs_text = scrolledtext.ScrolledText(self.logs_frame, wrap=tk.WORD,
                                                  font=('Consolas', 9), bg='#1e1e1e', fg='#f0f0f0')
        self.logs_text.pack(fill='both', expand=True, padx=10, pady=10)

        logs_control_frame = ttk.Frame(self.logs_frame)
        logs_control_frame.pack(fill='x', padx=10, pady=5)

        ttk.Button(logs_control_frame, text="📄 Сохранить логи",
                  command=self.save_logs).pack(side='left', padx=5)

        ttk.Button(logs_control_frame, text="🗑️ Очистить логи",
                  command=self.clear_logs).pack(side='left', padx=5)

    def browse_chrome_profile(self):
        """Выбор профиля Chrome"""
        folder = filedialog.askdirectory(title="Выберите папку профиля Chrome")
        if folder:
            self.chrome_path_entry.delete(0, tk.END)
            self.chrome_path_entry.insert(0, folder)

    def update_progress(self, current_action, progress_percent, comments_made, posts_checked, posts_filtered):
        """Обновление прогресса"""
        self.root.after(0, self._update_progress_gui, current_action, progress_percent, comments_made, posts_checked, posts_filtered)

    def _update_progress_gui(self, current_action, progress_percent, comments_made, posts_checked, posts_filtered):
        """Обновление GUI прогресса"""
        self.status_label.config(text=current_action)
        self.progress_bar['value'] = progress_percent
        self.comments_label.config(text=f"Комментариев: {comments_made}")
        self.checked_label.config(text=f"Проверено: {posts_checked}")
        self.filtered_label.config(text=f"Отфильтровано: {posts_filtered}")
        self.root.update_idletasks()

    def log_message(self, message):
        """Добавление сообщения в логи"""
        self.root.after(0, self._log_message_gui, message)

    def _log_message_gui(self, message):
        """Обновление GUI логов"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"

        self.logs_text.insert(tk.END, log_entry)
        self.logs_text.see(tk.END)

    def get_config(self):
        """Получение текущей конфигурации"""
        try:
            return {
                'api_key': self.api_key_entry.get().strip(),
                'ai_prompt': self.ai_prompt_text.get('1.0', tk.END).strip(),
                'max_comments': int(self.max_comments_var.get()),
                'max_tokens': int(self.max_tokens_var.get()),
                'min_delay': int(self.min_delay_var.get()),
                'max_delay': int(self.max_delay_var.get()),
                'typing_speed': float(self.typing_speed_var.get()),
                'chrome_path': self.chrome_path_entry.get().strip(),
                'filter_enabled': self.filter_enabled_var.get(),
                'keywords': self.keywords_entry.get().strip(),
                'use_ai_filter': self.use_ai_filter_var.get()
            }
        except ValueError as e:
            messagebox.showerror("Ошибка", f"Неверные настройки: {str(e)}")
            return None

    def start_bot(self):
        """Запуск бота"""
        config = self.get_config()
        if not config:
            return

        if not config['api_key']:
            messagebox.showerror("Ошибка", "Введите API ключ OpenAI")
            return

        self.start_button.config(state='disabled')
        self.stop_button.config(state='normal')
        self.progress_bar['value'] = 0

        self.bot = ThreadsBotCore(
            log_callback=self.log_message,
            update_progress_callback=self.update_progress,
            dialog_callback=self.show_dialog,
            finished_callback=self.bot_finished
        )
        self.bot_thread = threading.Thread(target=self.bot.run_bot, args=(config,))
        self.bot_thread.daemon = True
        self.bot_thread.start()

    def show_dialog(self, kind, title, message):
        """Показ диалога в главном потоке GUI, вызывается из потока бота"""
        result = {}
        done = threading.Event()

        def _run():
            try:
                if kind == 'error':
                    messagebox.showerror(title, message)
                    result['value'] = 'ok'
                elif kind == 'okcancel':
                    result['value'] = 'ok' if messagebox.askokcancel(title, message) else 'cancel'
                else:
                    messagebox.showinfo(title, message)
                    result['value'] = 'ok'
            finally:
                done.set()

        self.root.after(0, _run)
        done.wait()
        return result.get('value')

    def bot_finished(self):
        """Сброс состояния GUI после завершения работы бота"""
        self.root.after(0, self._bot_finished_gui)

    def _bot_finished_gui(self):
        self.start_button.config(state='normal')
        self.stop_button.config(state='disabled')

    def stop_bot(self):
        """Остановка бота"""
        if self.bot:
            self.bot.running = False
            self.log_message("[STOP] Остановка бота...")

        self.start_button.config(state='normal')
        self.stop_button.config(state='disabled')
        self.status_label.config(text="🛑 Остановлен")

    def save_config(self):
        """Сохранение конфигурации"""
        config = configparser.ConfigParser()
        current_config = self.get_config()

        if current_config:
            config['Settings'] = {
                'api_key': current_config['api_key'],
                'ai_prompt': current_config['ai_prompt'],
                'max_comments': str(current_config['max_comments']),
                'max_tokens': str(current_config['max_tokens']),
                'min_delay': str(current_config['min_delay']),
                'max_delay': str(current_config['max_delay']),
                'typing_speed': str(current_config['typing_speed']),
                'chrome_path': current_config['chrome_path'],
                'filter_enabled': str(current_config['filter_enabled']),
                'keywords': current_config['keywords'],
                'use_ai_filter': str(current_config['use_ai_filter'])
            }

            with open(self.config_file, 'w', encoding='utf-8') as f:
                config.write(f)

            messagebox.showinfo("Успех", "Настройки сохранены!")

    def load_config(self):
        """Загрузка конфигурации"""
        if os.path.exists(self.config_file):
            config = configparser.ConfigParser()
            config.read(self.config_file, encoding='utf-8')

            try:
                settings = config['Settings']
                self.api_key_entry.insert(0, settings.get('api_key', ''))
                self.ai_prompt_text.delete('1.0', tk.END)
                self.ai_prompt_text.insert('1.0', settings.get('ai_prompt', ''))
                self.max_comments_var.set(settings.get('max_comments', '5'))
                self.max_tokens_var.set(settings.get('max_tokens', '300'))
                self.min_delay_var.set(settings.get('min_delay', '30'))
                self.max_delay_var.set(settings.get('max_delay', '60'))
                self.typing_speed_var.set(settings.get('typing_speed', '0.12'))
                self.chrome_path_entry.insert(0, settings.get('chrome_path', ''))
                self.filter_enabled_var.set(settings.getboolean('filter_enabled', True))
                self.keywords_entry.insert(0, settings.get('keywords', ''))
                self.use_ai_filter_var.set(settings.getboolean('use_ai_filter', True))
            except:
                pass

    def save_logs(self):
        """Сохранение логов в файл"""
        logs = self.logs_text.get('1.0', tk.END)
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if filename:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(logs)
            messagebox.showinfo("Успех", "Логи сохранены!")

    def clear_logs(self):
        """Очистка логов"""
        self.logs_text.delete('1.0', tk.END)

    def on_closing(self):
        """Обработка закрытия приложения"""
        if self.bot and self.bot.running:
            if messagebox.askokcancel("Выход", "Бот еще работает. Остановить и выйти?"):
                self.stop_bot()
                time.sleep(1)
                self.root.destroy()
        else:
            self.root.destroy()

    def run(self):
        """Запуск GUI"""
        self.root.mainloop()

def main():
    """Точка входа: запускает графический интерфейс бота Threads."""
    if not GUI_AVAILABLE:
        print("[ERROR] GUI недоступен: не установлен tkinter "
              f"({GUI_IMPORT_ERROR}).")
        print("[INFO] Установите tkinter (например, 'sudo apt install "
              "python3-tk') и запустите снова.")
        return

    def signal_handler(signum, frame):
        print("\nПрерывание программы...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    app = ThreadsBotGUI()
    app.run()


if __name__ == "__main__":
    main()
