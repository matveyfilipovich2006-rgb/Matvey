"""
«Наследие Петербурга» — утренний дайджест новостей об архитектуре и наследии Санкт-Петербурга.

Что делает:
  1. Ищет свежие новости в Google Новостях по запросам для каждого раздела.
  2. Оставляет только проверенные издания и только новости про Петербург.
  3. Убирает дубли и то, что уже присылал за последнюю неделю.
  4. Для каждой новости открывает статью и берёт краткое описание (лид).
  5. Присылает в Telegram подборку, разбитую на разделы.

Работает на стандартной библиотеке Python — ничего ставить не нужно.

Переменные окружения:
  TELEGRAM_BOT_TOKEN  — токен бота от @BotFather
  TELEGRAM_CHAT_ID    — кому слать; можно несколько через запятую
  HOURS_BACK          — за сколько часов брать новости (по умолчанию 48)
  SENT_FILE           — файл памяти об отправленных новостях (по умолчанию sent.json)

Проверка без отправки:  python bot.py --dry-run
"""

import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# ===========================================================================
# НАСТРОЙКИ — всё, что можно менять под интересы папы, находится здесь
# ===========================================================================

# Разделы дайджеста. Порядок разделов = порядок в сообщении.
#   queries — что искать в Google Новостях (слово «Петербург» лучше оставлять)
#   words   — по каким словам новость относится к разделу (проверяются по порядку разделов)
SECTIONS = [
    {
        "key": "demolition",
        "title": "⚠️ Снос и споры",
        "queries": [
            "Петербург снос исторического здания",
            "Петербург градозащитники",
            "Петербург снос дома суд памятник",
            "Петербург историческая застройка снос",
        ],
        "words": ["снос", "снесут", "снесл", "демонтаж", "градозащит", "утрат", "разрушен"],
    },
    {
        "key": "expertise",
        "title": "📑 Экспертизы и обсуждения",
        "queries": [
            "Петербург историко-культурная экспертиза",
            "акт ГИКЭ Петербург",
            "КГИОП экспертиза",
            "КГИОП общественное обсуждение",
            "Петербург экспертный совет КГИОП",
        ],
        "words": ["историко-культурн", "гикэ", "экспертиз", "экспертный совет",
                  "общественное обсуждение", "общественного обсуждения", "на обсуждение",
                  "для обсуждения", "публичные слушания", "публичных слушаний"],
    },
    {
        "key": "heritage",
        "title": "🏛 Охрана наследия",
        "queries": [
            "КГИОП",
            'Петербург "объект культурного наследия"',
            "Петербург охранный статус здания",
            "Петербург памятник архитектуры",
            "Петербург выявленный объект наследия",
            "Петербург зоны охраны",
            "Петербург историческое здание",
        ],
        "words": ["кгиоп", "наслед", "реестр", "памятник", "статус памятник", "статус объект",
                  "охранный статус", "охранного статуса", "охранное обязательство", "охранного обязательства",
                  "охранной зон", "охранная зон", "охранные зон", "зон охраны", "зоны охраны", "предмет охраны", "охраны памятник",
                  "охране памятник", "охрана наследия", "охраны наследия", "охране наследия"],
    },
    {
        "key": "restoration",
        "title": "🔨 Реставрация",
        "queries": [
            "Петербург реставрация",
            "Петербург реставрация фасада",
            "Петербург приспособление памятника",
            "Петербург реставрация усадьбы дворца",
            "Петербург доходный дом реставрация",
            "Петербург реставрация памятника",
        ],
        "words": ["реставр", "фасад", "приспособ", "реконструк", "капремонт", "воссозда", "восстанов"],
    },
    {
        "key": "projects",
        "title": "🏗 Новые проекты и градостроительство",
        "queries": [
            "Петербург градсовет",
            "Петербург архитектурный конкурс",
            "КГА Петербург согласовал проект",
            "главный архитектор Петербурга",
            "Петербург ПЗЗ",
            "Петербург новый квартал архитектура",
            "Петербург проект застройки",
            "Петербург архитектура",
        ],
        "words": ["градсовет", "конкурс", "пзз", "генплан", "кга", "квартал", "застрой",
                  "главный архитектор", "небоскр", "высотн", "реновац", "проект"],
    },
]

# В каком порядке проверять слова разделов (самые «узкие» темы — первыми).
# Например, «КГИОП согласовал проект реставрации» — это Реставрация, а не Охрана.
CLASSIFY_ORDER = ["expertise", "demolition", "restoration", "heritage", "projects"]

PER_SECTION = 6   # максимум новостей в одном разделе
MAX_TOTAL = 20    # максимум новостей во всём дайджесте

# Тема архитектуры. Слова сравниваются с НАЧАЛА слова: «снос» найдёт «сносить»,
# но не «безопасность».
# Новость берётся, если в заголовке есть хотя бы одно «сильное» слово...
STRONG_WORDS = [
    "реставр", "наслед", "кгиоп", "снос", "снес", "фасад", "особняк",
    "доходн", "усадьб", "реконструк", "архитект", "ансамбл", "градсовет", "градостро",
    "пзз", "генплан", "кга", "застрой", "реновац", "небоскр", "высотк", "памятник архитект",
    "объект культурного", "историческ", "градозащит", "конструктивизм",
    "историко-культурн", "гикэ", "экспертный совет", "публичные слушания", "публичных слушаний",
    # «охрана» — только в архитектурном смысле (иначе пролезают новости про охранников)
    "охранный статус", "охранного статуса", "охранное обязательство", "охранного обязательства",
    "охранной зон", "охранная зон", "охранные зон", "зон охраны", "зоны охраны", "предмет охраны", "охраны памятник",
    "охране памятник", "охрана наследия", "охраны наследия", "охране наследия",
]
# ...или хотя бы два «слабых» (одно слово «мост» или «проект» — ещё не про архитектуру)
WEAK_WORDS = [
    "памятник", "здани", "дом", "проект", "квартал", "набережн", "мост", "площад",
    "собор", "храм", "церк", "дворц", "дворе", "капремонт", "конкурс", "сквер", "сад",
    "экспертиз", "обсужден", "слушани", "кгиоп", "смольн",
]

# Слова-исключения: если есть в заголовке или описании — новость пропускаем
EXCLUDE = [
    "гороскоп", "погод", "футбол", "хоккей", "ярмарк", "церковный праздник",
    "бдение", "литурги", "митрополит", "богослужен", "ипотек", "цены на квартир",
    "дтп", "убийств", "спасл", "спасател", "пожар", "погиб", "задержа", "мошенни",
    "спрыгн", "суицид", "охранник", "охранниц", "росгвард", "нокаут", "сизо", "наркот", "кайф",
]

# Новость должна быть про Петербург: хотя бы одно из этих слов в заголовке или описании
SPB_WORDS = [
    "петербург", "спб", "питер", "ленинград", "кгиоп", "смольн", "невск",
    "васильевск", "петроградск", "кронштадт", "петергоф", "пушкин", "павловск",
    "ораниенбаум", "гатчин", "царское село", "эрмитаж", "исаакиев",
    "северной столиц", "северная столиц",
]

# ПРОВЕРЕННЫЕ ИСТОЧНИКИ — новости берутся только с этих сайтов (поддомены тоже).
# Чтобы добавить издание, допишите его домен; чтобы убрать — удалите строку.
TRUSTED_SOURCES = {
    # Официальные
    "gov.spb.ru": "Правительство Петербурга",
    "spbdnevnik.ru": "Петербургский дневник",
    "spbvedomosti.ru": "Санкт-Петербургские ведомости",
    "vppress.ru": "Вечерний Санкт-Петербург",
    # Городские и деловые СМИ
    "fontanka.ru": "Фонтанка",
    "dp.ru": "Деловой Петербург",
    "rbc.ru": "РБК Петербург",
    "kommersant.ru": "Коммерсантъ",
    "interfax-russia.ru": "Интерфакс Северо-Запад",
    "interfax.ru": "Интерфакс",
    "tass.ru": "ТАСС",
    "ria.ru": "РИА Новости",
    "rosbalt.ru": "Росбалт",
    "abnews.ru": "Агентство Бизнес Новостей",
    "paperpaper.ru": "Бумага",
    "paperpaper.io": "Бумага",
    "mr7.ru": "Мой район",
    "78.ru": "78.ru",
    "peterburg2.ru": "Peterburg2",
    "sobaka.ru": "Собака.ru",
    # Отраслевые
    "asninfo.ru": "Строительный еженедельник",
    "ktostroit.ru": "Кто строит в Петербурге",
    "archi.ru": "Архи.ру",
}

MEMORY_DAYS = 7          # сколько дней помнить отправленные новости
SUMMARY_MAX_CHARS = 300  # длина краткого описания
# ===========================================================================

GN_SEARCH = "https://news.google.com/rss/search?q={q}&hl=ru&gl=RU&ceid=RU:ru"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TG_LIMIT = 4000  # у Telegram лимит 4096 символов на сообщение
MSK = timezone(timedelta(hours=3))
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


# ----------------------------- сеть ----------------------------------------
def http_get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(1_500_000)  # статьи бывают тяжёлыми — читаем не больше 1,5 МБ
        charset = r.headers.get_content_charset()
    if not charset:
        # кодировка не указана в заголовках — ищем её в самой странице (часто windows-1251)
        m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4000], flags=re.I)
        charset = m.group(1).decode() if m else "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def http_post(url: str, data: dict, timeout: int = 20) -> str:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ------------------------- Google Новости ----------------------------------
def parse_feed(xml_text: str):
    """Достаёт из RSS Google Новостей: заголовок, ссылку, издание, его домен, дату."""
    root = ET.fromstring(xml_text)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        src_url = src_el.get("url", "") if src_el is not None else ""
        domain = urllib.parse.urlparse(src_url).netloc.lower().removeprefix("www.")
        pub = item.findtext("pubDate")
        try:
            dt = parsedate_to_datetime(pub) if pub else None
        except (TypeError, ValueError):
            dt = None
        # Google дописывает « - Издание» в конец заголовка — убираем
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)].strip()
        if title and link:
            yield {"title": title, "link": link, "source": source, "dt": dt, "domain": domain}


def trusted_name(domain: str) -> str | None:
    """Название издания из списка проверенных (поддомены тоже: kgiop.gov.spb.ru → gov.spb.ru)."""
    for d, name in TRUSTED_SOURCES.items():
        if domain == d or domain.endswith("." + d):
            return name
    return None


def trusted(domain: str) -> bool:
    return trusted_name(domain) is not None


def has_any(text: str, stems) -> bool:
    """Есть ли в тексте слово, НАЧИНАЮЩЕЕСЯ с одного из корней."""
    low = text.lower().replace("ё", "е")
    return any(re.search(r"(?<![а-яa-z0-9])" + re.escape(s.replace("ё", "е")), low) for s in stems)


def count_any(text: str, stems) -> int:
    low = text.lower().replace("ё", "е")
    return sum(1 for s in stems if re.search(r"(?<![а-яa-z0-9])" + re.escape(s), low))


def resolve_google_link(gn_url: str) -> str | None:
    """
    Google Новости отдают не прямую ссылку на статью, а свою зашифрованную.
    Здесь мы спрашиваем у Google настоящий адрес статьи.
    Если не получилось — возвращаем None, и новость пойдёт без описания.
    """
    m = re.search(r"/articles/([^?/]+)", gn_url)
    if not m:
        return None
    art_id = m.group(1)
    try:
        page = http_get(f"https://news.google.com/articles/{art_id}")
        sg = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page)
        if not (sg and ts):
            return None
        inner = [
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None,
              None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None,
             0, 0, None, 0],
            art_id, int(ts.group(1)), sg.group(1),
        ]
        freq = json.dumps([[["Fbv4je", json.dumps(inner), None, "generic"]]])
        resp = http_post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            {"f.req": freq},
        )
        payload = json.loads(resp.split("\n\n", 1)[1])
        url = json.loads(payload[0][2])[1]
        return url if url.startswith("http") else None
    except Exception as e:
        print(f"[warn] не удалось раскрыть ссылку Google: {e}", file=sys.stderr)
        return None


# ------------------------ описание статьи ----------------------------------
META_PATTERNS = [
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description',
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description',
    r'<meta[^>]+name=["\']twitter:description["\'][^>]+content=["\']([^"\']+)',
]

# Служебные фразы сайтов, которые не являются описанием статьи
BOILERPLATE = [
    "cookie", "всегда актуальные новости", "последние новости", "читайте также",
    "подписывайтесь", "все права защищены", "главные новости", "наш телеграм",
    "telegram-канал", "телеграм-канал", "присоединяйтесь", "erid",
]
# С чего начинаются подписи к фото и прочие служебные абзацы
SKIP_PREFIXES = ("фото", "источник", "читайте", "подпис", "видео:", "смотрите", "автор:")


def clean(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()


def is_boilerplate(text: str) -> bool:
    low = text.lower()
    if any(b in low for b in BOILERPLATE):
        return True
    # куски CSS/JS-кода, которые некоторые сайты подсовывают вместо описания
    return bool(re.search(r"[{}]|\w+\s*:\s*#[0-9a-f]{3,6}|function\s*\(", low))


def good_text(t: str) -> bool:
    """Нормальный абзац текста: достаточно длинный, по-русски, не служебный."""
    low = t.lower()
    return (len(t) >= 60 and re.search(r"[а-яё]{3}", low) is not None
            and not low.startswith(SKIP_PREFIXES) and not is_boilerplate(t))


def repeats_title(text: str, title: str) -> bool:
    """Текст почти дословно повторяет заголовок — такой «пересказ» бесполезен."""
    if text.lower().startswith(title.lower()[:40]):
        return True
    tw, xw = title_words(title), title_words(text)
    return bool(xw) and len(tw & xw) / len(xw) >= 0.8


def meta_description(page: str) -> str:
    for pat in META_PATTERNS:
        m = re.search(pat, page, flags=re.I)
        if m:
            d = clean(m.group(1))
            if len(d) >= 40 and not is_boilerplate(d):
                return d
    return ""


def jsonld_fields(page: str) -> dict:
    """Многие сайты прячут текст статьи в разметке JSON-LD (articleBody, description)."""
    found = {}

    def walk(x):
        if isinstance(x, dict):
            for k in ("articleBody", "description"):
                v = x.get(k)
                if isinstance(v, str) and k not in found and len(v) > 40:
                    found[k] = clean(v)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
                            page, flags=re.S | re.I):
        try:
            walk(json.loads(block.strip()))
        except ValueError:
            continue
    return found


def article_paragraphs(page: str) -> list:
    """Абзацы самой статьи: без меню, подвала, подписей к фото и рекламы."""
    body = re.sub(r"<(script|style|noscript|header|footer|nav|aside|figure|figcaption|form)"
                  r"\b.*?</\1>", " ", page, flags=re.S | re.I)
    articles = re.findall(r"<article\b.*?</article>", body, flags=re.S | re.I)
    if articles:
        body = max(articles, key=len)
    paras, seen = [], set()
    for p in re.findall(r"<p\b[^>]*>(.*?)</p>", body, flags=re.S | re.I):
        t = clean(p)
        if good_text(t) and t not in seen:
            seen.add(t)
            paras.append(t)
    return paras


# Сокращения, после которых точка — не конец предложения
ABBREVIATIONS = ("г.", "гг.", "ул.", "д.", "пр.", "просп.", "наб.", "пл.", "пер.", "им.",
                 "т.", "т.е.", "т.д.", "т.п.", "св.", "кв.", "м.", "млн.", "млрд.", "руб.", "в.")


def split_sentences(text: str) -> list:
    parts = [s.strip() for s in re.split(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«\"0-9])", text) if s.strip()]
    merged = []
    for p in parts:
        prev = merged[-1] if merged else ""
        last_word = prev.rsplit(" ", 1)[-1].lower() if prev else ""
        # «Ф.» (инициал) или «ул.» — склеиваем со следующим куском
        if prev and (re.fullmatch(r"[а-яёa-z]\.", last_word) or last_word in ABBREVIATIONS):
            merged[-1] = f"{prev} {p}"
        else:
            merged.append(p)
    return merged


def shorten(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # стараемся обрезать по концу предложения
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end > limit * 0.5:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def build_summary(page: str, title: str) -> str:
    """
    Краткое описание: лид статьи, а если он короткий или пустой —
    добираем первые предложения из текста самой статьи (до ~300 символов).
    """
    ld = jsonld_fields(page)
    lead = meta_description(page) or ld.get("description", "")
    if lead and (repeats_title(lead, title) or not re.search(r"[а-яё]{3}", lead.lower())):
        lead = ""

    # Текст статьи: из JSON-LD (самый чистый) или из абзацев страницы
    if ld.get("articleBody") and good_text(ld["articleBody"]):
        sentences = split_sentences(ld["articleBody"])
    else:
        sentences = [s for p in article_paragraphs(page) for s in split_sentences(p)]

    text = lead
    for sent in sentences:
        if len(text) >= 220:
            break
        if len(sent) < 25 or repeats_title(sent, title) or is_boilerplate(sent):
            continue
        if sent[:60] in text:      # лид часто дословно совпадает с первым предложением
            continue
        text = f"{text} {sent}".strip()
    return shorten(text) if len(text) >= 40 else ""


def get_summary(article_url: str, title: str) -> str:
    try:
        page = http_get(article_url)
    except Exception as e:
        print(f"[warn] статья не открылась ({article_url}): {e}", file=sys.stderr)
        return ""
    try:
        return build_summary(page, title)
    except Exception as e:  # кривая вёрстка не должна ломать весь дайджест
        print(f"[warn] не удалось разобрать статью ({article_url}): {e}", file=sys.stderr)
        return ""


# ----------------------------- память --------------------------------------
def title_words(title: str) -> set:
    """Значимые слова заголовка (по первым 5 буквам, чтобы «дома»/«дом» совпадали)."""
    t = re.sub(r"[^\w\s]", " ", title.lower())
    return {w[:5] for w in t.split() if len(w) > 3}


def is_duplicate(words: set, seen: list, threshold: float = 0.6) -> bool:
    """Одна новость в разных изданиях: заголовки совпадают по большинству слов."""
    for other in seen:
        if not words or not other:
            continue
        overlap = len(words & other) / min(len(words), len(other))
        if overlap >= threshold:
            return True
    return False


def load_memory(path: str) -> dict:
    """Что уже отправляли за последние MEMORY_DAYS дней: ссылки и заголовки."""
    try:
        with open(path, encoding="utf-8") as f:
            mem = json.load(f)
    except (OSError, ValueError):
        return {"links": {}, "titles": []}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MEMORY_DAYS)).isoformat()
    links = {k: v for k, v in mem.get("links", {}).items() if v >= cutoff}
    titles = [t for t in mem.get("titles", []) if t.get("at", "") >= cutoff]
    return {"links": links, "titles": titles}


def save_memory(path: str, mem: dict, items: list):
    now = datetime.now(timezone.utc).isoformat()
    for it in items:
        for link in {it["link"], it.get("gn_link", "")} - {""}:
            mem["links"][link] = now
        mem["titles"].append({"words": sorted(title_words(it["title"])), "at": now})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False)


# ----------------------------- сбор ----------------------------------------
def relevant(title: str) -> bool:
    """Новость по теме архитектуры: одно «сильное» слово или два «слабых»."""
    if has_any(title, EXCLUDE):
        return False
    return has_any(title, STRONG_WORDS) or count_any(title, WEAK_WORDS) >= 2


def classify(title: str, summary: str, fallback: str) -> str:
    """Раздел новости: сначала смотрим заголовок (он точнее), потом описание."""
    by_key = {s["key"]: s for s in SECTIONS}
    for text in (title, title + " " + summary):
        for key in CLASSIFY_ORDER:
            if has_any(text, by_key[key]["words"]):
                return key
    return fallback


def search_candidates(hours_back: int, mem: dict) -> list:
    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    days = max(1, (hours_back + 23) // 24)
    seen_links = set(mem["links"])
    seen_titles = [set(t["words"]) for t in mem["titles"]]
    candidates = []

    for sec in SECTIONS:
        for q in sec["queries"]:
            url = GN_SEARCH.format(q=urllib.parse.quote(f"{q} when:{days}d"))
            try:
                entries = list(parse_feed(http_get(url)))
            except Exception as e:  # один упавший запрос не ломает весь дайджест
                print(f"[warn] запрос «{q}» не удался: {e}", file=sys.stderr)
                continue
            for it in entries:
                if it["dt"] and it["dt"] < since:
                    continue
                if not trusted(it["domain"]) or not relevant(it["title"]):
                    continue
                words = title_words(it["title"])
                if it["link"] in seen_links or is_duplicate(words, seen_titles):
                    continue
                seen_links.add(it["link"])
                seen_titles.append(words)
                it["query_section"] = sec["key"]
                candidates.append(it)
            time.sleep(1)

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    candidates.sort(key=lambda x: x["dt"] or epoch, reverse=True)
    return candidates


def collect(hours_back: int, mem: dict) -> dict:
    """Возвращает {раздел: [новости]} — уже с описаниями и прямыми ссылками."""
    candidates = search_candidates(hours_back, mem)
    print(f"Кандидатов после фильтров: {len(candidates)}")

    by_section = {s["key"]: [] for s in SECTIONS}
    real_links = set(mem["links"])
    total = 0
    for it in candidates:
        if total >= MAX_TOTAL:
            break
        # раздел по заголовку — чтобы не тратить время на статьи для заполненных разделов
        key = classify(it["title"], "", it["query_section"])
        if len(by_section[key]) >= PER_SECTION:
            continue

        real = resolve_google_link(it["link"])
        time.sleep(1)
        if real and real in real_links:
            continue  # та же статья (или уже отправленная раньше)
        it["gn_link"] = it["link"]
        it["summary"] = ""
        if real:
            real_links.add(real)
            it["link"] = real
            it["summary"] = get_summary(real, it["title"])

        text = it["title"] + " " + it["summary"]
        if has_any(text, EXCLUDE):
            continue
        if not has_any(text, SPB_WORDS):
            continue  # новость не про Петербург
        key = classify(it["title"], it["summary"], it["query_section"])
        if len(by_section[key]) >= PER_SECTION:
            continue
        by_section[key].append(it)
        total += 1
    return by_section


# --------------------------- оформление ------------------------------------
def human_date(d: datetime) -> str:
    return f"{WEEKDAYS[d.weekday()].capitalize()}, {d.day} {MONTHS[d.month - 1]}"


def plural_news(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} новость"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} новости"
    return f"{n} новостей"


def format_item(it: dict) -> str:
    title = html.escape(it["title"])
    link = html.escape(it["link"], quote=True)
    # Название издания — аккуратное, из нашего списка (а не «spbdnevnik.ru»)
    source = html.escape(trusted_name(it.get("domain", "")) or it.get("source") or "Источник")
    lines = [f"▫️ <b>{title}</b>"]
    if it.get("summary"):
        lines.append(html.escape(it["summary"]))
    meta = f'<a href="{link}">{source} →</a>'
    if it.get("dt"):
        local = it["dt"].astimezone(MSK)
        today = datetime.now(MSK).date()
        when = local.strftime("%H:%M") if local.date() == today else local.strftime("%d.%m, %H:%M")
        meta += f" · {when}"
    lines.append(meta)
    return "\n".join(lines)


def is_evening(now: datetime | None = None) -> bool:
    """Вечерний выпуск — всё, что запускается после 14:00 по Петербургу."""
    return (now or datetime.now(MSK)).hour >= 14


def build_messages(by_section: dict) -> list:
    now = datetime.now(MSK)
    evening = is_evening(now)
    total = sum(len(v) for v in by_section.values())
    edition = "вечерний выпуск" if evening else "утренний выпуск"
    header = (f"🏛 <b>Наследие Петербурга</b> · {edition}\n"
              f"<i>{human_date(now)} · {plural_news(total) if total else 'тихий день'}</i>")

    if not total:
        if evening:
            return []  # вечером без новостей не беспокоим — ничего не присылаем
        return [header + "\n\nСвежих новостей по теме в проверенных источниках пока нет — "
                         "всё важное уже было в прошлых подборках. До вечера! ☕"]

    blocks = [header]
    for sec in SECTIONS:
        items = by_section.get(sec["key"]) or []
        if not items:
            continue
        blocks.append(f"<b>{sec['title']}</b>")
        blocks.extend(format_item(it) for it in items)
    blocks.append("<i>Хорошего вечера! Следующая подборка — утром.</i>" if evening
                  else "<i>Хорошего дня! Вечером пришлю, что появится за день.</i>")

    # Склеиваем блоки в сообщения, не разрывая новость посередине
    messages, cur = [], ""
    for block in blocks:
        sep = "\n\n" if cur else ""
        if len(cur) + len(sep) + len(block) > TG_LIMIT:
            messages.append(cur)
            cur, sep = "", ""
        cur += sep + block
    if cur:
        messages.append(cur)
    return messages


# --------------------------- Telegram --------------------------------------
def send(token: str, chat_id: str, text: str):
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read())
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram ответил ошибкой: {resp}")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
    # 48 часов: так не теряются новости выходных; повторов не будет — бот помнит отправленное
    hours_back = int(os.environ.get("HOURS_BACK", "48"))
    sent_file = os.environ.get("SENT_FILE", "sent.json")
    dry_run = "--dry-run" in sys.argv

    if not dry_run and (not token or not chat_ids):
        sys.exit("Не заданы TELEGRAM_BOT_TOKEN и/или TELEGRAM_CHAT_ID")

    try:
        mem = load_memory(sent_file)
        by_section = collect(hours_back, mem)
        messages = build_messages(by_section)
        items = [it for v in by_section.values() for it in v]
        with_summary = sum(1 for it in items if it.get("summary"))
        print(f"В дайджесте: {len(items)}, с описанием: {with_summary}")

        if dry_run:
            print("\n\n=====\n\n".join(messages))
            return

        for chat_id in chat_ids:
            for msg in messages:
                send(token, chat_id, msg)
                time.sleep(0.5)
        save_memory(sent_file, mem, items)
        print("Дайджест отправлен.")
    except Exception as e:
        # Сообщаем владельцу (первый chat_id), чтобы бот не «молчал» незаметно
        if not dry_run and token and chat_ids:
            try:
                send(token, chat_ids[0],
                     f"⚠️ Бот «Наследие Петербурга» не смог собрать подборку.\n"
                     f"Ошибка: {html.escape(str(e))[:500]}")
            except Exception:
                pass
        raise


if __name__ == "__main__":
    main()
