import html
import logging
import os
import re
import sqlite3
from typing import Any

import requests
from deep_translator import GoogleTranslator
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SPOONACULAR_API_KEY = os.getenv("SPOONACULAR_API_KEY")

SPOONACULAR_FIND_URL = "https://api.spoonacular.com/recipes/findByIngredients"
SPOONACULAR_INFO_URL = "https://api.spoonacular.com/recipes/{recipe_id}/information"
CAPTION_LIMIT = 1024
RECIPES_PER_PAGE = 3
SHOW_MORE_CALLBACK = "show_more"
SAVE_CALLBACK_PREFIX = "save_"
DB_PATH = "favorites.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)

INGREDIENT_TRANSLATIONS: dict[str, str] = {
    "курица": "chicken",
    "курицы": "chicken",
    "куриное": "chicken",
    "куриная": "chicken",
    "куриный": "chicken",
    "курочка": "chicken",
    "грудка": "breast",
    "филе": "fillet",
    "мясо": "meat",
    "говядина": "beef",
    "свинина": "pork",
    "баранина": "lamb",
    "индейка": "turkey",
    "утка": "duck",
    "фарш": "minced meat",
    "бекон": "bacon",
    "колбаса": "sausage",
    "сосиски": "sausages",
    "ветчина": "ham",
    "рыба": "fish",
    "лосось": "salmon",
    "семга": "salmon",
    "тунец": "tuna",
    "треска": "cod",
    "форель": "trout",
    "креветки": "shrimp",
    "морепродукты": "seafood",
    "яйца": "eggs",
    "яйцо": "egg",
    "молоко": "milk",
    "сливки": "cream",
    "сметана": "sour cream",
    "творог": "cottage cheese",
    "сыр": "cheese",
    "масло": "butter",
    "сливочное масло": "butter",
    "растительное масло": "vegetable oil",
    "оливковое масло": "olive oil",
    "йогурт": "yogurt",
    "кефир": "kefir",
    "рис": "rice",
    "гречка": "buckwheat",
    "овсянка": "oats",
    "овес": "oats",
    "макароны": "pasta",
    "паста": "pasta",
    "спагетти": "spaghetti",
    "лапша": "noodles",
    "мука": "flour",
    "хлеб": "bread",
    "батон": "bread",
    "картошка": "potato",
    "картофель": "potato",
    "помидоры": "tomatoes",
    "помидор": "tomato",
    "томат": "tomato",
    "томаты": "tomatoes",
    "огурцы": "cucumbers",
    "огурец": "cucumber",
    "лук": "onion",
    "луковица": "onion",
    "чеснок": "garlic",
    "морковь": "carrot",
    "морковка": "carrot",
    "капуста": "cabbage",
    "брокколи": "broccoli",
    "перец": "pepper",
    "болгарский перец": "bell pepper",
    "кабачок": "zucchini",
    "баклажан": "eggplant",
    "свекла": "beetroot",
    "грибы": "mushrooms",
    "шампиньоны": "mushrooms",
    "зелень": "herbs",
    "укроп": "dill",
    "петрушка": "parsley",
    "базилик": "basil",
    "кинза": "cilantro",
    "салат": "lettuce",
    "шпинат": "spinach",
    "кукуруза": "corn",
    "горох": "peas",
    "фасоль": "beans",
    "чечевица": "lentils",
    "нут": "chickpeas",
    "яблоко": "apple",
    "яблоки": "apples",
    "банан": "banana",
    "бананы": "bananas",
    "лимон": "lemon",
    "апельсин": "orange",
    "ягоды": "berries",
    "клубника": "strawberry",
    "сахар": "sugar",
    "соль": "salt",
    "перец черный": "black pepper",
    "специи": "spices",
    "приправы": "seasoning",
    "паприка": "paprika",
    "куркума": "turmeric",
    "корица": "cinnamon",
    "имбирь": "ginger",
    "мед": "honey",
    "соевый соус": "soy sauce",
    "уксус": "vinegar",
    "кетчуп": "ketchup",
    "майонез": "mayonnaise",
    "горчица": "mustard",
    "томатная паста": "tomato paste",
    "вода": "water",
    "бульон": "broth",
    "орехи": "nuts",
    "грецкие орехи": "walnuts",
    "шоколад": "chocolate",
}


def _normalize_ingredient(text: str) -> str:
    cleaned = text.strip().lower().replace("ё", "е")
    cleaned = re.sub(r"[.!?]+$", "", cleaned)
    return re.sub(r"\s+", " ", cleaned)


def _has_cyrillic(text: str) -> bool:
    return bool(CYRILLIC_RE.search(text))


_en_ru_cache: dict[str, str] = {}


def _translate_with_google(text: str) -> str:
    try:
        translated = GoogleTranslator(source="ru", target="en").translate(text)
        if translated:
            return translated.strip()
    except Exception:
        logger.warning("Не удалось автоматически перевести ингредиент: %s", text)
    return text


def _translate_words_en_to_ru(text: str) -> str:
    out: list[str] = []
    for word in text.split():
        if not re.search(r"[A-Za-z]", word):
            out.append(word)
            continue
        try:
            translated = GoogleTranslator(source="en", target="ru").translate(word)
            out.append(translated.strip() if translated and translated.strip() else word)
        except Exception:
            logger.warning("Не удалось перевести слово: %s", word)
            out.append(word)
    return " ".join(out)


def _translate_en_to_ru(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return cleaned
    if _has_cyrillic(cleaned) and not re.search(r"[A-Za-z]", cleaned):
        return cleaned
    cached = _en_ru_cache.get(cleaned.casefold())
    if cached is not None:
        return cached
    try:
        translated = GoogleTranslator(source="en", target="ru").translate(cleaned)
        if translated and translated.strip():
            result = translated.strip()
        else:
            result = _translate_words_en_to_ru(cleaned)
    except Exception:
        logger.warning("Не удалось перевести строку, перевожу по словам: %s", cleaned)
        result = _translate_words_en_to_ru(cleaned)
    _en_ru_cache[cleaned.casefold()] = result
    return result


_AMOUNT_TOKEN = (
    r"(?:\d+[½¼¾⅓⅔⅛⅜⅝⅞]"
    r"|\d+\s*-\s*\d+/\d+"
    r"|\d+(?:[.,]\d+)?(?:\s+\d+/\d+)?"
    r"|\d+/\d+"
    r"|[½¼¾⅓⅔⅛⅜⅝⅞])"
)
_RANGE_SEP = r"(?:\s*[-–—]\s*|\s+(?:to|до)\s+)"
_AMOUNT_OR_RANGE = rf"(?:{_AMOUNT_TOKEN}{_RANGE_SEP}{_AMOUNT_TOKEN}|{_AMOUNT_TOKEN})"
_UNICODE_FRACTIONS = {
    "½": 0.5,
    "¼": 0.25,
    "¾": 0.75,
    "⅓": 1 / 3,
    "⅔": 2 / 3,
    "⅛": 0.125,
    "⅜": 0.375,
    "⅝": 0.625,
    "⅞": 0.875,
}
_LIQUID_RE = re.compile(
    r"бульон|молоко|вода|сливк|сок\b|уксус|вино|соус|кефир|йогурт|"
    r"\bbroth\b|\bstock\b|\bmilk\b|\bwater\b|\bcream\b|\bjuice\b|"
    r"\boil\b|\bwine\b|\bvinegar\b|\bsauce\b|\bkefir\b|\byogurt\b",
    re.IGNORECASE,
)
_CUP_GRAMS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"петрушк|parsley", re.IGNORECASE), 60),
    (re.compile(r"укроп|dill", re.IGNORECASE), 60),
    (re.compile(r"кинз|cilantro", re.IGNORECASE), 60),
    (re.compile(r"базилик|basil", re.IGNORECASE), 40),
    (re.compile(r"зелень|herbs", re.IGNORECASE), 60),
    (re.compile(r"морков|carrot", re.IGNORECASE), 150),
    (
        re.compile(
            r"куриц|курочк|chicken|индейк|turkey|мяс[оа]|говядин|свинин|"
            r"\bbeef\b|\bpork\b|\bmeat\b",
            re.IGNORECASE,
        ),
        140,
    ),
    (re.compile(r"мук[аиеуой]|flour", re.IGNORECASE), 120),
    (re.compile(r"\bрис(?:а|у|ом|е)?\b|\brice\b", re.IGNORECASE), 180),
    (re.compile(r"\bbutter\b", re.IGNORECASE), 227),
    (
        re.compile(
            r"сахар|соль|овсян|макарон|хлеб|сыр|карто|лук|фасол|чечевиц|"
            r"\bsugar\b|\bsalt\b|\boats\b|\bpasta\b|\bbread\b|\bcheese\b|"
            r"\bpotato|\bonion|\bbeans?\b|\blentil",
            re.IGNORECASE,
        ),
        150,
    ),
)
_UNIT_LB = r"(?:pounds?|lbs?\.?|фунт(?:а|ов|у|е|ами)?)"
_UNIT_OZ = r"(?:ounces?|oz\.?|унц(?:ия|ии|ий|ию|иями|иях)?)"
_UNIT_CUP = r"(?:cups?|чаш(?:ка|ки|ек|ку|кой|ками)?|стакан(?:а|ов|у|е|ами)?)"
_UNIT_TBSP = r"(?:tablespoons?|tbsp\.?|tbs\.?|столов(?:ая|ой|ую|ые|ых|ыми)?[\s\u00a0]*ложк\w*)"
_UNIT_TSP = r"(?:teaspoons?|tsp\.?|чайн(?:ая|ой|ую|ые|ых|ыми)?[\s\u00a0]*ложк\w*)"


def _parse_amount(raw: str) -> float | None:
    text = raw.strip().replace(",", ".")
    for symbol, value in _UNICODE_FRACTIONS.items():
        if symbol in text:
            text = text.replace(symbol, f" {value}")
            break
    text = re.sub(r"\s+", " ", text).strip()
    mixed_hyphen = re.fullmatch(r"(\d+)\s*-\s*(\d+)/(\d+)", text)
    if mixed_hyphen:
        denom = int(mixed_hyphen.group(3))
        return (
            int(mixed_hyphen.group(1)) + int(mixed_hyphen.group(2)) / denom
            if denom
            else None
        )
    mixed = re.fullmatch(r"(\d+(?:\.\d+)?)\s+(\d+)/(\d+)", text)
    if mixed:
        return float(mixed.group(1)) + int(mixed.group(2)) / int(mixed.group(3))
    fraction = re.fullmatch(r"(\d+)/(\d+)", text)
    if fraction:
        denom = int(fraction.group(2))
        return int(fraction.group(1)) / denom if denom else None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_amount_or_range(raw: str) -> tuple[float, float | None] | None:
    text = raw.strip()
    to_split = re.split(r"\s+(?:to|до)\s+", text, maxsplit=1, flags=re.IGNORECASE)
    if len(to_split) == 2:
        left, right = _parse_amount(to_split[0]), _parse_amount(to_split[1])
        if left is not None and right is not None:
            return left, right
        return None
    dash_split = re.split(r"\s*[-–—]\s*", text, maxsplit=1)
    if len(dash_split) == 2:
        left, right = _parse_amount(dash_split[0]), _parse_amount(dash_split[1])
        if left is None or right is None:
            return None
        if left == int(left) and 0 < right < 1:
            return left + right, None
        return left, right
    amount = _parse_amount(text)
    if amount is None:
        return None
    return amount, None


def _ru_count_form(number: float) -> str:
    if abs(number - round(number)) > 1e-6:
        return "genitive"
    value = abs(int(round(number)))
    if 11 <= value % 100 <= 14:
        return "many"
    last = value % 10
    if last == 1:
        return "one"
    if 2 <= last <= 4:
        return "few"
    return "many"


UNIT_FORMS: dict[str, dict[str, str]] = {
    "tsp": {
        "one": "чайная ложка",
        "few": "чайные ложки",
        "many": "чайных ложек",
        "genitive": "чайной ложки",
    },
    "tbsp": {
        "one": "столовая ложка",
        "few": "столовые ложки",
        "many": "столовых ложек",
        "genitive": "столовой ложки",
    },
    "cup": {
        "one": "стакан",
        "few": "стакана",
        "many": "стаканов",
        "genitive": "стакана",
    },
    "g": {
        "one": "грамм",
        "few": "грамма",
        "many": "граммов",
        "genitive": "грамма",
    },
    "ml": {
        "one": "миллилитр",
        "few": "миллилитра",
        "many": "миллилитров",
        "genitive": "миллилитра",
    },
}
_UNIT_ALIAS_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"чайны(?:е|х)\s+лож(?:ки|ек)|чайная\s+ложка|чайной\s+ложки|"
        r"чайную\s+ложку|teaspoons?|tsp\.?",
        "tsp",
    ),
    (
        r"столов(?:ые|ых)\s+лож(?:ки|ек)|столовая\s+ложка|столовой\s+ложки|"
        r"столовую\s+ложку|tablespoons?|tbsp\.?|tbs\.?",
        "tbsp",
    ),
    (
        r"стакан(?:а|ов|у|е|ами)?|чаш(?:ка|ки|ек|ку|кой|ками)?|cups?",
        "cup",
    ),
    (
        r"миллилитр(?:а|ов)?|\bмл\b|milliliters?|\bml\b",
        "ml",
    ),
    (
        r"грамм(?:а|ов)?|\bг\b|grams?|\bg\b",
        "g",
    ),
)


def _canonical_unit(unit: str) -> str | None:
    text = unit.strip().casefold()
    aliases = {
        "tsp": "tsp",
        "teaspoon": "tsp",
        "teaspoons": "tsp",
        "tbsp": "tbsp",
        "tbs": "tbsp",
        "tablespoon": "tbsp",
        "tablespoons": "tbsp",
        "cup": "cup",
        "cups": "cup",
        "стакан": "cup",
        "чашка": "cup",
        "g": "g",
        "г": "g",
        "gram": "g",
        "grams": "g",
        "ml": "ml",
        "мл": "ml",
        "milliliter": "ml",
        "milliliters": "ml",
    }
    if text in aliases:
        return aliases[text]
    if text in UNIT_FORMS:
        return text
    for pattern, key in _UNIT_ALIAS_PATTERNS:
        if re.fullmatch(pattern, text, flags=re.IGNORECASE):
            return key
    return None


def decline_unit(number: float, unit: str) -> str:
    key = _canonical_unit(unit)
    if key is None:
        return unit
    form = _ru_count_form(number)
    return UNIT_FORMS[key][form]


def apply_unit_declension(text: str) -> str:
    if not text:
        return text
    unit_alt = "|".join(pattern for pattern, _ in _UNIT_ALIAS_PATTERNS)
    pattern = re.compile(
        rf"(?P<amount>{_AMOUNT_OR_RANGE})(?P<space>[\s\u00a0]+)(?P<unit>{unit_alt})",
        flags=re.IGNORECASE,
    )

    def _replace(match: re.Match[str]) -> str:
        parsed = _parse_amount_or_range(match.group("amount"))
        if parsed is None:
            return match.group(0)
        left, right = parsed
        number = right if right is not None else left
        declined = decline_unit(number, match.group("unit"))
        return f"{match.group('amount')}{match.group('space')}{declined}"

    return pattern.sub(_replace, text)


def _format_metric(left: float, right: float | None, factor: float, unit: str) -> str:
    first = int(round(left * factor))
    if right is None:
        return f"{first} {decline_unit(first, unit)}"
    second = int(round(right * factor))
    return f"{first}-{second} {decline_unit(second, unit)}"


def _format_number(value: float) -> str:
    if abs(value - 0.5) < 1e-6:
        return "1/2"
    if abs(value - 0.25) < 1e-6:
        return "1/4"
    if abs(value - 0.75) < 1e-6:
        return "3/4"
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return str(value)


def _format_spoons(raw_amount: str, unit: str) -> str:
    parsed = _parse_amount_or_range(raw_amount)
    if parsed is None:
        label = re.sub(r"\s+(?:to|до)\s+", "-", raw_amount.strip(), flags=re.IGNORECASE)
        label = re.sub(r"\s*[-–—]\s*", "-", label)
        return f"{label} {unit}"
    left, right = parsed
    agree = right if right is not None else left
    if right is None:
        shown = _format_number(left)
    else:
        shown = f"{_format_number(left)}-{_format_number(right)}"
    return f"{shown} {decline_unit(agree, unit)}"


def _cup_factor_and_unit(full_text: str) -> tuple[float, str]:
    if _LIQUID_RE.search(full_text):
        return 240, "ml"
    for pattern, grams in _CUP_GRAMS:
        if pattern.search(full_text):
            return float(grams), "g"
    return 150.0, "g"


def _unit_pair_pattern(unit_regex: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?:(?P<amt1>{_AMOUNT_OR_RANGE})[\s\u00a0]*(?P<u1>{unit_regex})"
        rf"|(?P<u2>{unit_regex})[\s\u00a0]*(?P<amt2>{_AMOUNT_OR_RANGE}))",
        re.IGNORECASE,
    )


def convert_units(text: str) -> str:
    if not text:
        return text

    def metric_replacer(factor: float, unit: str):
        def _replace(match: re.Match[str]) -> str:
            raw_amount = match.group("amt1") or match.group("amt2")
            parsed = _parse_amount_or_range(raw_amount)
            if parsed is None:
                return match.group(0)
            left, right = parsed
            return _format_metric(left, right, factor, unit)

        return _replace

    def cup_replacer(match: re.Match[str]) -> str:
        raw_amount = match.group("amt1") or match.group("amt2")
        parsed = _parse_amount_or_range(raw_amount)
        if parsed is None:
            return match.group(0)
        left, right = parsed
        factor, unit = _cup_factor_and_unit(text)
        return _format_metric(left, right, factor, unit)

    def spoon_replacer(unit: str):
        def _replace(match: re.Match[str]) -> str:
            raw_amount = match.group("amt1") or match.group("amt2")
            return _format_spoons(raw_amount, unit)

        return _replace

    result = text
    replacements: tuple[tuple[re.Pattern[str], Any], ...] = (
        (_unit_pair_pattern(_UNIT_LB), metric_replacer(453.6, "g")),
        (_unit_pair_pattern(_UNIT_OZ), metric_replacer(28.35, "g")),
        (_unit_pair_pattern(_UNIT_CUP), cup_replacer),
        (_unit_pair_pattern(_UNIT_TBSP), spoon_replacer("tbsp")),
        (_unit_pair_pattern(_UNIT_TSP), spoon_replacer("tsp")),
    )
    for pattern, replacer in replacements:
        result = pattern.sub(replacer, result)
    return result


def _lookup_ingredient(phrase: str) -> str:
    if not phrase:
        return phrase
    if phrase in INGREDIENT_TRANSLATIONS:
        return INGREDIENT_TRANSLATIONS[phrase]

    words = phrase.split()
    if len(words) > 1:
        mapped_words = []
        for word in words:
            if word in INGREDIENT_TRANSLATIONS:
                mapped_words.append(INGREDIENT_TRANSLATIONS[word])
            elif _has_cyrillic(word):
                mapped_words.append(_translate_with_google(word))
            else:
                mapped_words.append(word)
        return " ".join(mapped_words)

    if _has_cyrillic(phrase):
        return _translate_with_google(phrase)
    return phrase


def translate_ingredients(raw_query: str) -> str:
    parts = re.split(r"[,;\n]+", raw_query)
    translated: list[str] = []
    seen: set[str] = set()
    for part in parts:
        phrase = _normalize_ingredient(part)
        if not phrase:
            continue
        english = _lookup_ingredient(phrase)
        key = english.casefold()
        if key not in seen:
            seen.add(key)
            translated.append(english)
    return ",".join(translated)


def _require_env() -> None:
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("SPOONACULAR_API_KEY", SPOONACULAR_API_KEY),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Не заданы переменные окружения: " + ", ".join(missing)
        )


def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER NOT NULL,
                recipe_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                source_url TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, recipe_id)
            )
            """
        )
        conn.commit()


def _is_favorite(user_id: int, recipe_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND recipe_id = ? LIMIT 1",
            (user_id, recipe_id),
        )
        return cur.fetchone() is not None


def _add_favorite(
    user_id: int, recipe_id: int, title: str, source_url: str
) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id, recipe_id, title, source_url) "
            "VALUES (?, ?, ?, ?)",
            (user_id, recipe_id, title, source_url or ""),
        )
        conn.commit()
        return cur.rowcount > 0


def _get_favorites(user_id: int) -> list[tuple[str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT title, source_url FROM favorites WHERE user_id = ? "
            "ORDER BY added_at DESC, recipe_id DESC",
            (user_id,),
        )
        return [(row[0], row[1] or "") for row in cur.fetchall()]


def _format_ingredient(item: dict[str, Any]) -> str:
    original = (item.get("original") or "").strip()
    if original:
        return original
    amount = item.get("amount")
    unit = (item.get("unitLong") or item.get("unit") or "").strip()
    name = (item.get("originalName") or item.get("name") or "").strip()
    parts: list[str] = []
    if isinstance(amount, (int, float)):
        parts.append(str(int(amount)) if float(amount).is_integer() else str(amount))
    elif amount not in (None, ""):
        parts.append(str(amount))
    if unit:
        parts.append(unit)
    if name:
        parts.append(name)
    return " ".join(parts)


def _collect_ingredients(recipe: dict[str, Any]) -> list[str]:
    used = [_format_ingredient(i) for i in recipe.get("usedIngredients") or []]
    missed = [_format_ingredient(i) for i in recipe.get("missedIngredients") or []]
    lines = [line for line in used + missed if line]
    seen: set[str] = set()
    unique: list[str] = []
    for line in lines:
        key = line.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(line)
    return unique


def _build_caption(title: str, ingredients: list[str], source_url: str) -> str:
    title_ru = _translate_en_to_ru(title or "Без названия")
    title_html = html.escape(title_ru)
    if ingredients:
        lines_html: list[str] = []
        for line in ingredients:
            converted = convert_units(line)
            translated = _translate_en_to_ru(converted)
            declined = apply_unit_declension(translated)
            lines_html.append(f"• {html.escape(declined)}")
        ingredients_html = "\n".join(lines_html)
    else:
        ingredients_html = "• список ингредиентов недоступен"

    link = html.escape(source_url) if source_url else ""
    link_block = f'\n\n<a href="{link}">Открыть полный рецепт</a>' if link else ""

    return (
        f"<b>{title_html}</b>\n\n"
        f"<b>Ингредиенты:</b>\n{ingredients_html}"
        f"{link_block}"
    )


def fetch_recipes(
    ingredients_query: str,
    number: int = 3,
    offset: int = 0,
) -> list[dict[str, Any]]:
    translated_query = translate_ingredients(ingredients_query)
    if not translated_query:
        return []
    logger.info(
        "Поиск рецептов по ингредиентам: %s -> %s (offset=%d)",
        ingredients_query, translated_query, offset,
    )
    find_response = requests.get(
        SPOONACULAR_FIND_URL,
        params={
            "ingredients": translated_query,
            "number": number,
            "offset": offset,
            "ranking": 1,
            "ignorePantry": True,
            "apiKey": SPOONACULAR_API_KEY,
        },
        timeout=20,
    )
    find_response.raise_for_status()
    found = find_response.json()
    if not isinstance(found, list) or not found:
        return []

    recipes: list[dict[str, Any]] = []
    for item in found[:number]:
        recipe_id = item.get("id")
        info: dict[str, Any] = {}
        if recipe_id is not None:
            info_response = requests.get(
                SPOONACULAR_INFO_URL.format(recipe_id=recipe_id),
                params={"apiKey": SPOONACULAR_API_KEY},
                timeout=20,
            )
            info_response.raise_for_status()
            info = info_response.json()

        recipes.append(
            {
                "id": recipe_id,
                "title": info.get("title") or item.get("title") or "Без названия",
                "image": info.get("image") or item.get("image") or "",
                "source_url": info.get("sourceUrl")
                or info.get("spoonacularSourceUrl")
                or "",
                "ingredients": _collect_ingredients(item)
                or [
                    _format_ingredient(i)
                    for i in (info.get("extendedIngredients") or [])
                    if _format_ingredient(i)
                ],
            }
        )
    return recipes


def _recipe_keyboard(
    recipe_id: Any, with_more: bool
) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    if recipe_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    "❤️ Сохранить",
                    callback_data=f"{SAVE_CALLBACK_PREFIX}{recipe_id}",
                )
            ]
        )
    if with_more:
        rows.append(
            [
                InlineKeyboardButton(
                    "Показать ещё", callback_data=SHOW_MORE_CALLBACK
                )
            ]
        )
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


async def _send_recipes(
    chat,
    context: ContextTypes.DEFAULT_TYPE,
    ingredients_query: str,
    offset: int,
    is_more: bool,
) -> None:
    try:
        recipes = fetch_recipes(
            ingredients_query,
            number=RECIPES_PER_PAGE,
            offset=offset,
        )
    except requests.HTTPError as exc:
        logger.exception("Spoonacular HTTP error: %s", exc)
        status = exc.response.status_code if exc.response is not None else None
        if status == 401:
            await chat.send_message(
                "Не удалось обратиться к Spoonacular: проверьте SPOONACULAR_API_KEY."
            )
        elif status == 402:
            await chat.send_message(
                "Лимит запросов Spoonacular исчерпан. Попробуйте позже."
            )
        else:
            await chat.send_message(
                "Сервис рецептов временно недоступен. Попробуйте ещё раз чуть позже."
            )
        return
    except requests.RequestException:
        logger.exception("Spoonacular request failed")
        await chat.send_message(
            "Не получилось связаться с сервисом рецептов. "
            "Проверьте интернет и попробуйте снова."
        )
        return

    if not recipes:
        if is_more:
            await chat.send_message("Больше рецептов нет")
        else:
            await chat.send_message(
                "По этим продуктам рецепты не нашлись. "
                "Попробуйте другой набор ингредиентов."
            )
        return

    has_more = len(recipes) == RECIPES_PER_PAGE
    last_index = len(recipes) - 1

    known = context.user_data.setdefault("known_recipes", {})

    for index, recipe in enumerate(recipes):
        recipe_id = recipe.get("id")
        if recipe_id is not None:
            known[recipe_id] = {
                "title": recipe["title"],
                "source_url": recipe["source_url"],
            }

        caption = _build_caption(
            recipe["title"],
            recipe["ingredients"],
            recipe["source_url"],
        )
        image_url = recipe["image"]
        with_more = has_more and index == last_index
        keyboard = _recipe_keyboard(recipe_id, with_more)

        try:
            if image_url and len(caption) <= CAPTION_LIMIT:
                await chat.send_photo(
                    photo=image_url,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
            elif image_url:
                await chat.send_photo(photo=image_url)
                await chat.send_message(
                    caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
            else:
                await chat.send_message(
                    caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
        except Exception:
            logger.exception("Failed to send recipe: %s", recipe.get("title"))
            await chat.send_message(
                caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )

    if is_more and not has_more:
        await chat.send_message("Больше рецептов нет")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message is None:
        return
    await update.message.reply_text(
        "Привет! Я помогу найти рецепты по продуктам, которые у вас уже есть.\n\n"
        "Перечислите ингредиенты через запятую, например:\n"
        "<i>курица, рис, помидоры</i>\n\n"
        "Подробнее — /help",
        parse_mode=ParseMode.HTML,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message is None:
        return
    await update.message.reply_text(
        "<b>Что умеет бот</b>\n"
        "Ищет рецепты по продуктам, которые у вас уже есть, переводит их на русский "
        "и конвертирует единицы измерения (cups, oz, lb → граммы/миллилитры).\n\n"
        "<b>Как пользоваться</b>\n"
        "Отправьте список продуктов через запятую — бот пришлёт до 3 рецептов. "
        "Под последним рецептом есть кнопка «Показать ещё», а под каждым — "
        "«❤️ Сохранить».\n\n"
        "<b>Команды</b>\n"
        "/start — приветствие\n"
        "/help — эта справка\n"
        "/favorites — список сохранённых рецептов\n\n"
        "<b>Пример</b>\n"
        "<i>курица, рис, помидоры</i>",
        parse_mode=ParseMode.HTML,
    )


async def favorites_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    del context
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    favorites = _get_favorites(user_id)
    if not favorites:
        await update.message.reply_text("У вас пока нет избранных рецептов")
        return

    lines: list[str] = ["<b>Ваши избранные рецепты:</b>", ""]
    for number, (title, url) in enumerate(favorites, 1):
        title_html = html.escape(title or "Без названия")
        if url:
            lines.append(f'{number}. <a href="{html.escape(url)}">{title_html}</a>')
        else:
            lines.append(f"{number}. {title_html}")
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def handle_ingredients(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.message is None or not update.message.text:
        return

    ingredients_query = " ".join(update.message.text.split())
    if not ingredients_query:
        await update.message.reply_text(
            "Напишите список продуктов через запятую, например: курица, рис, помидоры."
        )
        return

    await update.message.reply_text("Ищу рецепты, подождите немного...")

    context.user_data["query"] = ingredients_query
    context.user_data["offset"] = 0

    await _send_recipes(
        update.message.chat,
        context,
        ingredients_query,
        offset=0,
        is_more=False,
    )


async def show_more(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    await query.answer()

    ingredients_query = context.user_data.get("query")
    if not ingredients_query:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.reply_text(
            "Не помню, что искали. Отправьте список продуктов заново."
        )
        return

    offset = context.user_data.get("offset", 0) + RECIPES_PER_PAGE
    context.user_data["offset"] = offset

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await _send_recipes(
        query.message.chat,
        context,
        ingredients_query,
        offset=offset,
        is_more=True,
    )


async def save_favorite(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return

    try:
        recipe_id = int(query.data[len(SAVE_CALLBACK_PREFIX):])
    except (ValueError, TypeError):
        await query.answer()
        return

    user = update.effective_user
    if user is None:
        await query.answer("Не удалось определить пользователя", show_alert=True)
        return
    user_id = user.id

    info = context.user_data.get("known_recipes", {}).get(recipe_id)
    if not info:
        await query.answer(
            "Не удалось найти данные рецепта. Выполните поиск заново.",
            show_alert=True,
        )
        return

    if _is_favorite(user_id, recipe_id):
        await query.answer("Уже в избранном")
        return

    title_ru = _translate_en_to_ru(info.get("title") or "Без названия")
    source_url = info.get("source_url") or ""

    if _add_favorite(user_id, recipe_id, title_ru, source_url):
        await query.answer("Добавлено в избранное")
    else:
        await query.answer("Уже в избранном")


import os
from flask import Flask
import threading

app = Flask(__name__)

@app.route('/')
def health():
    return "Bot is running"

def run_bot():
    _require_env()
    _init_db()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("favorites", favorites_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ingredients)
    )
    application.add_handler(
        CallbackQueryHandler(show_more, pattern=rf"^{SHOW_MORE_CALLBACK}$")
    )
    application.add_handler(
        CallbackQueryHandler(
            save_favorite, pattern=rf"^{SAVE_CALLBACK_PREFIX}\d+$"
        )
    )
    logger.info("Bot started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

def main() -> None:
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()