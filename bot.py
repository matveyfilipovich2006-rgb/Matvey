"""
Утренний дайджест новостей об охране наследия и реставрации в Санкт-Петербурге.

Что делает:
  1. Ищет свежие новости в Google Новостях по ключевым запросам.
  2. Убирает дубли (одна новость часто выходит в нескольких изданиях).
  3. Для каждой новости открывает саму статью и берёт оттуда краткое описание (лид).
  4. Отправляет в Telegram: заголовок, 1–3 предложения о сути, издание, ссылка.

Работает на стандартной библиотеке Python — ничего ставить не нужно.

Переменные окружения:
  TELEGRAM_BOT_TOKEN  — токен бота от @BotFather
  TELEGRAM_CHAT_ID    — кому слать; можно несколько через запятую
  HOURS_BACK          — за сколько часов брать новости (по умолчанию 24)

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

# ---------------------------------------------------------------------------
# НАСТРОЙКИ — здесь можно менять запросы под интересы папы
# ---------------------------------------------------------------------------
QUERIES = [
    "КГИОП",
    "Петербург реставрация",
    'Петербург "объект культурного наследия"',
    "Петербург памятник архитектуры",
    "Петербург охранный статус здания",
    "Петербург снос исторического здания",
    "Петербург историческая застройка",
    "Петербург реконструкция фасада",
    "Петербург доходный дом",
    "Петербург особняк реставрация",
]

# Новость попадает в дайджест, только если в заголовке есть хоть одно из этих слов
# (отсекает случайные совпадения). Пустой список — фильтр выключен.
MUST_CONTAIN_ANY = [
    "реставр", "наслед", "памятник", "кгиоп", "охран", "истори", "снос",
    "фасад", "особняк", "доходн", "усадьб", "собор", "церк", "храм",
    "реконструк", "архитект", "дворц", "дворе", "ансамбл", "капремонт",
    "здани", "дом ",
]

# Слова-исключения: если есть в заголовке или описании — новость пропускаем
EXCLUDE = [
    "гороскоп", "погода", "футбол", "хоккей", "ярмарк", "церковный праздник",
    "бдение", "литурги", "митрополит", "богослужен",
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
    "gov.spb.ru": "Правительство Петербурга / КГИОП / КГА",
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
    "sobaka.ru": "Собака.ru (раздел «Реставрации»)",
    # Отраслевые
    "asninfo.ru": "Строительный еженедельник",
    "ktostroit.ru": "Кто строит в Петербурге",
    "archi.ru": "Архи.ру",
}

MAX_ITEMS = 15          # максимум новостей в одном дайджесте
SUMMARY_MAX_CHARS = 350 # длина краткого описания
# ---------------------------------------------------------------------------

GN_SEARCH = "https://news.google.com/rss/search?q={q}&hl=ru&gl=RU&ceid=RU:ru"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TG_LIMIT = 4000  # у Telegram лимит 4096 символов на сообщение
MSK = timezone(timedelta(hours=3))


# ----------------------------- сеть ----------------------------------------
def http_get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(1_500_000)  # статьи бывают тяжёлыми — читаем не больше 1,5 МБ
        charset = r.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


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
    """Достаёт из RSS Google Новостей: заголовок, ссылку, издание, дату."""
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


def trusted(domain: str) -> bool:
    """Сайт есть в списке проверенных (учитываются и поддомены: kgiop.gov.spb.ru → gov.spb.ru)."""
    return any(domain == d or domain.endswith("." + d) for d in TRUSTED_SOURCES)


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


def clean(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()


def first_paragraph(page: str) -> str:
    """Запасной вариант: первый содержательный абзац статьи."""
    for p in re.findall(r"<p[^>]*>(.*?)</p>", page, flags=re.S | re.I):
        t = clean(p)
        if len(t) > 80 and "cookie" not in t.lower():
            return t
    return ""


def shorten(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # стараемся обрезать по концу предложения
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end > limit * 0.5:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def get_summary(article_url: str, title: str) -> str:
    try:
        page = http_get(article_url)
    except Exception as e:
        print(f"[warn] статья не открылась ({article_url}): {e}", file=sys.stderr)
        return ""
    desc = ""
    for pat in META_PATTERNS:
        m = re.search(pat, page, flags=re.I)
        if m:
            desc = clean(m.group(1))
            if len(desc) > 40:
                break
    # Если описание пустое или просто повторяет заголовок — берём первый абзац
    if len(desc) < 40 or desc.lower().startswith(title.lower()[:40]):
        para = first_paragraph(page)
        if para:
            desc = para
    return shorten(desc) if len(desc) >= 40 else ""


# ----------------------------- сбор ----------------------------------------
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


def relevant(title: str) -> bool:
    low = title.lower() + " "
    if any(x in low for x in EXCLUDE):
        return False
    if not MUST_CONTAIN_ANY:
        return True
    return any(x in low for x in MUST_CONTAIN_ANY)


def collect(hours_back: int):
    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    days = max(1, (hours_back + 23) // 24)
    seen_links, seen_titles, items = set(), [], []

    for q in QUERIES:
        url = GN_SEARCH.format(q=urllib.parse.quote(f"{q} when:{days}d"))
        try:
            entries = list(parse_feed(http_get(url)))
        except Exception as e:  # один упавший запрос не ломает весь дайджест
            print(f"[warn] запрос «{q}» не удался: {e}", file=sys.stderr)
            continue
        for it in entries:
            if it["dt"] and it["dt"] < since:
                continue
            if not trusted(it["domain"]):
                continue
            if not relevant(it["title"]):
                continue
            words = title_words(it["title"])
            if it["link"] in seen_links or is_duplicate(words, seen_titles):
                continue
            seen_links.add(it["link"])
            seen_titles.append(words)
            items.append(it)
        time.sleep(1)

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda x: x["dt"] or epoch, reverse=True)
    items = items[: MAX_ITEMS * 2]  # с запасом: часть отсеется после чтения описаний

    # Для каждой новости — настоящая ссылка и краткое описание
    result, real_links = [], set()
    for it in items:
        real = resolve_google_link(it["link"])
        if real and real in real_links:
            continue  # та же статья, найденная разными запросами
        if real:
            real_links.add(real)
            it["link"] = real
            it["summary"] = get_summary(real, it["title"])
        else:
            it["summary"] = ""
        time.sleep(1)
        text = (it["title"] + " " + it["summary"]).lower()
        if any(x in text for x in EXCLUDE):
            continue
        if not any(x in text for x in SPB_WORDS):
            continue  # новость не про Петербург (Пенза, Ставрополь и т.п.)
        result.append(it)
    return result[:MAX_ITEMS]


# --------------------------- Telegram --------------------------------------
def format_item(i: int, it: dict) -> str:
    title = html.escape(it["title"])
    link = html.escape(it["link"], quote=True)
    parts = [f"<b>{i}. {title}</b>"]
    if it.get("summary"):
        parts.append(html.escape(it["summary"]))
    meta = []
    if it.get("source"):
        meta.append(html.escape(it["source"]))
    if it.get("dt"):
        meta.append(it["dt"].astimezone(MSK).strftime("%d.%m %H:%M"))
    meta_str = " · ".join(meta)
    parts.append(f'<a href="{link}">Читать →</a>' + (f"  <i>{meta_str}</i>" if meta_str else ""))
    return "\n".join(parts)


def build_messages(items):
    today = datetime.now(MSK).strftime("%d.%m.%Y")
    header = f"🏛 <b>Наследие Петербурга — {today}</b>\n\n"

    if not items:
        return [header + "За последние сутки новых новостей не нашлось."]

    blocks = [format_item(i, it) for i, it in enumerate(items, 1)]
    messages, cur = [], header
    for block in blocks:
        if len(cur) + len(block) + 2 > TG_LIMIT:
            messages.append(cur.rstrip())
            cur = ""
        cur += block + "\n\n"
    if cur.strip():
        messages.append(cur.rstrip())
    return messages


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
    hours_back = int(os.environ.get("HOURS_BACK", "24"))
    dry_run = "--dry-run" in sys.argv

    if not dry_run and (not token or not chat_ids):
        sys.exit("Не заданы TELEGRAM_BOT_TOKEN и/или TELEGRAM_CHAT_ID")

    items = collect(hours_back)
    messages = build_messages(items)
    with_summary = sum(1 for it in items if it.get("summary"))
    print(f"Найдено новостей: {len(items)}, с описанием: {with_summary}")

    if dry_run:
        print("\n\n---\n\n".join(messages))
        return

    for chat_id in chat_ids:
        for msg in messages:
            send(token, chat_id, msg)
            time.sleep(0.5)
    print("Дайджест отправлен.")


if __name__ == "__main__":
    main()
