# Auto-generated from 00_common_helpers.ipynb.
# Import/exec this file instead of using %run on the notebook, so nbformat is not required.

# Dependencies used by the notebooks: requests pandas tqdm rapidfuzz pymorphy2 pymorphy2-dicts-ru

# --- Cell 4 from 00_common_helpers.ipynb ---
import os
import re
import json
import time
import random
import hashlib
import datetime as dt
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

import requests
import pandas as pd
from tqdm.auto import tqdm

# --- Cell 6 from 00_common_helpers.ipynb ---
WDQS_ENDPOINT = "https://query.wikidata.org/sparql"
WIKI_API = "https://www.wikidata.org/w/api.php"

USER_AGENT = "YandexGPT-reversal-curse-benchmark (contact: salam121asd@gmail.com)"

MIN_SECONDS_BETWEEN_REQUESTS = 1.1
MAX_RETRIES = 4
TIMEOUT_SECONDS = 30

MAX_BACKOFF_SECONDS = 20
POOL_FAIL_FAST = True
POOL_MAX_RETRIES = 4
POOL_TIMEOUT_SECONDS = 30
POOL_MAX_BACKOFF_SECONDS = 20

POOL_BUILD_DELAY_S = 4.0 
DOMAIN_PAUSE_S = 2.0     

OUT_DIR = "out_wikidata_benchmark"
CACHE_DIR = os.path.join(OUT_DIR, "cache")
POOLS_DIR = os.path.join(OUT_DIR, "pools")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(POOLS_DIR, exist_ok=True)

DATASET_PATH = os.path.join(OUT_DIR, "dataset_ru_multicriteria.jsonl")

# === OUTPUT DATASET PATHS ===
DATASET_PATH_MAIN = os.path.join(OUT_DIR, "dataset_main.jsonl")
DATASET_PATH_ZERO = os.path.join(OUT_DIR, "dataset_zero.jsonl")

ZERO_TARGET_PER_BUCKET = {
    "default": {"L1": 0, "L2": 0, "L3": 0, "L4": 0, "L5": 0},
    "cinema":  {"L1": 0, "L2": 0, "L3": 0, "L4": 0, "L5": 0},
}

# === Debug ===
DEBUG_GENERATOR_ERRORS = False

# --- Cell 8 from 00_common_helpers.ipynb ---
class DiskCache:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir

    def _path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.json")

    def get(self, key: str) -> Optional[dict]:
        path = self._path(key)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                try:
                    os.rename(path, path + ".bad")
                except Exception:
                    pass
        return None

    def set(self, key: str, value: dict) -> None:
        path = self._path(key)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


class WikidataClient:
    def __init__(
        self,
        endpoint: str = WDQS_ENDPOINT,
        user_agent: str = USER_AGENT,
        min_delay: float = MIN_SECONDS_BETWEEN_REQUESTS,
        timeout: int = TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRIES,
        cache: Optional[DiskCache] = None,
    ):
        self.endpoint = endpoint
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/sparql-results+json"
        })
        self.min_delay = min_delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache = cache or DiskCache(CACHE_DIR)
        self._last_request_ts = 0.0

    def _sleep_if_needed(self):
        now = time.time()
        delta = now - self._last_request_ts
        if delta < self.min_delay:
            time.sleep(self.min_delay - delta)
    def _sleep_backoff(self, attempt: int, resp: Optional[requests.Response] = None) -> None:
        """Экспоненциальный backoff с учётом Retry-After (если есть)."""
        ra = None
        if resp is not None:
            ra = resp.headers.get("Retry-After")
        if ra:
            try:
                sec = float(str(ra).strip())
                time.sleep(sec + random.random())
                return
            except Exception:
                pass
        base = min(MAX_BACKOFF_SECONDS, (2 ** attempt))
        time.sleep(base + random.random())


    def sparql_select(self, query: str, use_cache: bool = True) -> dict:
        q = query.strip()
        key = _sha1("SELECT:" + q)
        if use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                return cached

        last_err: Optional[Exception] = None

        for attempt in range(self.max_retries):
            self._sleep_if_needed()
            resp = None
            try:
                resp = self.session.post(
                    self.endpoint,
                    params={"format": "json"},
                    data={"query": q},
                    headers={"Accept": "application/sparql-results+json"},
                    timeout=self.timeout,
                )
                self._last_request_ts = time.time()

                if resp.status_code in (429, 500, 502, 503, 504):
                    last_err = RuntimeError(f"WDQS transient HTTP {resp.status_code}")
                    self._sleep_backoff(attempt, resp)
                    continue

                resp.raise_for_status()

                text = resp.text or ""
                t = text.lstrip()

                if not t.startswith("{"):
                    bad_dir = os.path.join(OUT_DIR, "bad_responses")
                    os.makedirs(bad_dir, exist_ok=True)
                    with open(os.path.join(bad_dir, f"{key}.nonjson.txt"), "w", encoding="utf-8", errors="replace") as bf:
                        bf.write(text[:250_000])
                    last_err = ValueError("Non-JSON WDQS response")
                    self._sleep_backoff(attempt, resp)
                    continue

                if not text.rstrip().endswith("}"):
                    bad_dir = os.path.join(OUT_DIR, "bad_responses")
                    os.makedirs(bad_dir, exist_ok=True)
                    with open(os.path.join(bad_dir, f"{key}.truncated.txt"), "w", encoding="utf-8", errors="replace") as bf:
                        bf.write(text[:250_000])
                    last_err = ValueError("Truncated WDQS JSON")
                    self._sleep_backoff(attempt, resp)
                    continue

                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = json.loads(text, strict=False)

                if not isinstance(data, dict) or "results" not in data:
                    bad_dir = os.path.join(OUT_DIR, "bad_responses")
                    os.makedirs(bad_dir, exist_ok=True)
                    with open(os.path.join(bad_dir, f"{key}.weird.json"), "w", encoding="utf-8", errors="replace") as bf:
                        bf.write(text[:250_000])
                    last_err = ValueError("Unexpected WDQS JSON format")
                    self._sleep_backoff(attempt, resp)
                    continue

                if use_cache:
                    self.cache.set(key, data)
                return data

            except (requests.RequestException, ValueError, json.JSONDecodeError) as e:
                last_err = e
                if attempt == self.max_retries - 1:
                    raise RuntimeError(f"WDQS failed after {self.max_retries} retries: {e}") from e
                self._sleep_backoff(attempt, resp)

        raise RuntimeError(f"WDQS failed: {last_err}")


    def api_search_entity(self, text: str, language: str = "ru", limit: int = 10) -> dict:
        params = {
            "action": "wbsearchentities",
            "search": text,
            "language": language,
            "format": "json",
            "limit": limit,
        }

        last_err: Optional[Exception] = None

        for attempt in range(self.max_retries):
            self._sleep_if_needed()
            resp = None
            try:
                resp = self.session.get(WIKI_API, params=params, timeout=self.timeout)
                self._last_request_ts = time.time()

                if resp.status_code in (429, 500, 502, 503, 504):
                    last_err = RuntimeError(f"Wikidata API transient HTTP {resp.status_code}")
                    self._sleep_backoff(attempt, resp)
                    continue

                resp.raise_for_status()
                try:
                    return resp.json()
                except json.JSONDecodeError:
                    return json.loads(resp.text or "", strict=False)

            except (requests.RequestException, ValueError, json.JSONDecodeError) as e:
                last_err = e
                if attempt == self.max_retries - 1:
                    raise RuntimeError(f"Wikidata API search failed after {self.max_retries} retries: {e}") from e
                self._sleep_backoff(attempt, resp)

        raise RuntimeError(f"Unreachable: {last_err}")


wd = WikidataClient()


wd = WikidataClient()

# --- Cell 10 from 00_common_helpers.ipynb ---
QID_RE = re.compile(r"Q\d+")

def uri_to_qid(uri: str) -> Optional[str]:
    if not uri:
        return None
    m = QID_RE.search(uri)
    return m.group(0) if m else None

def rows_from_select(data: dict) -> List[Dict[str, str]]:
    bindings = data.get("results", {}).get("bindings", [])
    rows = []
    for b in bindings:
        row = {}
        for k, v in b.items():
            row[k] = v.get("value")
        rows.append(row)
    return rows

def resolve_qid(text: str, lang: str = "ru") -> Optional[str]:
    res = wd.api_search_entity(text, language=lang, limit=5)
    if "search" not in res or not res["search"]:
        return None
    return res["search"][0]["id"]

def ensure_qid(label: str, fallback_qid: Optional[str] = None) -> str:
    qid = resolve_qid(label, "ru") or resolve_qid(label, "en") or fallback_qid
    if not qid:
        raise ValueError(f"Cannot resolve QID for '{label}'")
    return qid

# --- Cell 12 from 00_common_helpers.ipynb ---
@dataclass
class BenchmarkExample:
    id: str
    domain: str                 # e.g. cinema | geo_ru | books | videogames | music_albums | people
    complexity: str             # L1...L5
    query_text_ru: str
    constraints: Dict[str, Any]
    requested_count: int
    gold_answer_qids: List[str]
    gold_answer_labels_ru: List[str]
    sparql_query: str
    created_at: str

    # Added in v2-clean: the same generated task in English + English gold labels.
    # Defaults keep backward compatibility with older JSONL / generator code.
    query_text_en: str = ""
    gold_answer_labels_en: List[str] = field(default_factory=list)

    is_advanced: bool = False
    template_id: Optional[str] = None
    template_family: str = "default"
    gold_truncated: bool = False                # True if the gold set is limited by LIMIT
    ask_validator_sparql: Optional[str] = None  # ASK template for validating one candidate

    # Optional fields used by IMDb-backed movie/cinema generation. Defaults keep
    # all other domains backward-compatible.
    local_validator: Optional[Dict[str, Any]] = None
    gold_collection_meta: Optional[Dict[str, Any]] = None
    gold_answer_imdb_ids: List[str] = field(default_factory=list)
    gold_answer_imdb_titles: List[str] = field(default_factory=list)

def save_jsonl(examples: List[BenchmarkExample], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(asdict(ex), ensure_ascii=False) + "\n")

# --- Cell 14 from 00_common_helpers.ipynb ---
from pathlib import Path

def utc_now_z() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

def _pool_path(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_\-\.]+", "_", name)
    return os.path.join(POOLS_DIR, f"{safe}.json")

def load_pool_df(name: str) -> Optional[pd.DataFrame]:
    path = _pool_path(name)
    if os.path.exists(path):
        try:
            return pd.read_json(path, lines=False)
        except ValueError:
            try:
                os.rename(path, path + ".bad")
            except Exception:
                pass
            return None
    return None


def save_pool_df(name: str, df: pd.DataFrame) -> None:
    path = _pool_path(name)
    tmp = path + ".tmp"
    df.to_json(tmp, force_ascii=False, orient="records")
    os.replace(tmp, path)


def load_or_build_pool(name: str, builder):
    """Загрузить пул из диска или построить.

    Важно: построение пулов — самый тяжёлый этап (WDQS).
    Поэтому здесь есть 'fail-fast' режим: на время построения пула
    снижаем max_retries/timeout/backoff, чтобы не висеть минутами.
    """
    df = load_pool_df(name)
    if df is not None and len(df) > 0:
        return df

    wd_obj = globals().get("wd", None)
    orig_retries = getattr(wd_obj, "max_retries", None) if wd_obj is not None else None
    orig_timeout = getattr(wd_obj, "timeout", None) if wd_obj is not None else None
    orig_backoff = globals().get("MAX_BACKOFF_SECONDS", None)

    try:
        if globals().get("POOL_FAIL_FAST", True) and wd_obj is not None:
            try:
                wd_obj.max_retries = int(globals().get("POOL_MAX_RETRIES", 2))
                wd_obj.timeout = int(globals().get("POOL_TIMEOUT_SECONDS", 25))
                if orig_backoff is not None:
                    globals()["MAX_BACKOFF_SECONDS"] = int(globals().get("POOL_MAX_BACKOFF_SECONDS", 6))
            except Exception:
                pass

        df = builder()

    except Exception as e:
        print(f"[WARN] pool '{name}' build failed: {e}")
        return pd.DataFrame()

    finally:
        if wd_obj is not None:
            if orig_retries is not None:
                wd_obj.max_retries = orig_retries
            if orig_timeout is not None:
                wd_obj.timeout = orig_timeout
        if orig_backoff is not None:
            globals()["MAX_BACKOFF_SECONDS"] = orig_backoff

    try:
        save_pool_df(name, df)
    except Exception as e:
        print(f"[WARN] pool '{name}' save failed: {e}")

    if 'POOL_BUILD_DELAY_S' in globals() and POOL_BUILD_DELAY_S and POOL_BUILD_DELAY_S > 0:
        time.sleep(float(POOL_BUILD_DELAY_S))
    return df


def safe_int(x, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return default

qid_from_uri = uri_to_qid

# --- Cell 16 from 00_common_helpers.ipynb ---
def build_value_pool_ru(
    item_class_qid: str,
    pid: str,
    val_name: str,
    limit: int = 400,
) -> pd.DataFrame:
    """
    Лёгкий пул значений свойства pid, которые реально встречаются у item_class_qid.
    Без COUNT/GROUP BY (так меньше таймаутов). Возвращает QID + RU-лейбл.
    """
    sparql = f"""
    SELECT DISTINCT ?val ?valLabelRu WHERE {{
      ?item wdt:P31/wdt:P279* wd:{item_class_qid} ;
            wdt:{pid} ?val .
      ?val rdfs:label ?valLabelRu FILTER(LANG(?valLabelRu) = "ru") .
    }}
    LIMIT {int(limit)}
    """
    rows = rows_from_select(wd.sparql_select(sparql))
    data = []
    for r in rows:
        qid = uri_to_qid(r.get("val", ""))
        lbl = r.get("valLabelRu")
        if qid and lbl:
            data.append({f"{val_name}_qid": qid, f"{val_name}LabelRu": lbl})
    return pd.DataFrame(data).drop_duplicates().reset_index(drop=True)

def select_items_with_ru_label(
    item_class_qid: str,
    where_lines: List[str],
    limit: int = 300,
    item_var: str = "item",
) -> Tuple[str, List[Tuple[str,str]]]:
    """
    Выполняет SELECT для items и возвращает (sparql, [(qid, ru_label), ...]).
    """
    where = "\n      ".join(where_lines)
    sparql = f"""
    SELECT DISTINCT ?{item_var} ?{item_var}LabelRu WHERE {{
      ?{item_var} wdt:P31/wdt:P279* wd:{item_class_qid} .
      {where}
      ?{item_var} rdfs:label ?{item_var}LabelRu FILTER(LANG(?{item_var}LabelRu) = "ru") .
    }}
    LIMIT {int(limit)}
    """
    rows = rows_from_select(wd.sparql_select(sparql))
    items = []
    for r in rows:
        qid = uri_to_qid(r.get(item_var, ""))
        lbl = r.get(f"{item_var}LabelRu")
        if qid and lbl:
            items.append((qid, lbl))
    return sparql.strip(), items

def build_ask_validator(item_class_qid: str, where_lines: List[str], item_var: str = "item") -> str:
    """
    Возвращает ASK запрос, где item подставляется как wd:{{ITEM}}.
    Так можно валидировать ответы модели честно, даже если gold был LIMIT-нут.
    """
    where = "\n      ".join(where_lines)
    sparql = f"""
    ASK WHERE {{
      BIND(wd:{{ITEM}} AS ?{item_var})
      ?{item_var} wdt:P31/wdt:P279* wd:{item_class_qid} .
      {where}
    }}
    """
    return sparql.strip()

def pick_from_df(df: pd.DataFrame, qid_col: str, label_col: str, rng: random.Random):
    row = df.sample(1, random_state=rng.randint(0, 10**9)).iloc[0]
    return row[qid_col], row[label_col]

def build_value_pool_ru_direct(
    item_class_qid: str,
    pid: str,
    val_name: str,
    limit: int = 200,
) -> pd.DataFrame:
    """
    Ещё более лёгкий пул: только прямые instance of (P31=class), без P279*.
    Иногда это существенно снижает нагрузку и уменьшает 429/обрывы ответа.
    """
    sparql = f"""
    SELECT DISTINCT ?val ?valLabelRu WHERE {{
      ?item wdt:P31 wd:{item_class_qid} ;
            wdt:{pid} ?val .
      ?val rdfs:label ?valLabelRu FILTER(LANG(?valLabelRu) = "ru") .
    }}
    LIMIT {int(limit)}
    """
    rows = rows_from_select(wd.sparql_select(sparql))
    data = []
    for r in rows:
        qid = uri_to_qid(r.get("val"))
        lab = r.get("valLabelRu")
        if qid and lab:
            data.append({f"{val_name}_qid": qid, f"{val_name}LabelRu": lab, "n": 1})
    return pd.DataFrame(data).drop_duplicates()

def pool_from_anchor_labels(
    labels: List[str],
    qid_col: str,
    label_col: str,
    fallback_qids: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """Строит маленький пул через API-поиск QID по строкам (без WDQS)."""
    rows = []
    for lab in labels:
        fb = (fallback_qids or {}).get(lab)
        try:
            qid = ensure_qid(lab, fallback_qid=fb)
        except Exception:
            continue
        rows.append({qid_col: qid, label_col: lab, "n": 1})
    return pd.DataFrame(rows).drop_duplicates()


def build_class_entities_pool_ru(
    class_qid: str,
    val_name: str,
    limit: int = 300,
    subclasses: bool = True,
) -> pd.DataFrame:
    """
    Возвращает пул сущностей, которые являются instance of (или subclass chain) class_qid.
    Пример: class_qid="Q9143" (programming language) -> список языков.
    """
    path = "wdt:P31/wdt:P279* " if subclasses else "wdt:P31 "
    sparql = f"""
    SELECT DISTINCT ?val ?valLabelRu WHERE {{
      ?val {path}wd:{class_qid} .
      ?val rdfs:label ?valLabelRu FILTER(LANG(?valLabelRu) = "ru") .
    }}
    LIMIT {int(limit)}
    """
    rows = rows_from_select(wd.sparql_select(sparql))
    data = []
    for r in rows:
        qid = uri_to_qid(r.get("val", ""))
        lab = r.get("valLabelRu")
        if qid and lab:
            data.append({f"{val_name}_qid": qid, f"{val_name}LabelRu": lab, "n": 1})
    return pd.DataFrame(data).drop_duplicates()

def select_items_with_ru_label_direct(
    item_class_qid: str,
    where_lines: List[str],
    limit: int = 300,
    item_var: str = "item",
) -> Tuple[str, List[Tuple[str, str]]]:
    """
    Как select_items_with_ru_label, но БЕЗ /wdt:P279* (только прямой P31=class).
    Для широких классов (типа "software") это может резко снизить нагрузку.
    """
    where = "\n      ".join(where_lines)
    sparql = f"""
    SELECT DISTINCT ?{item_var} ?{item_var}LabelRu WHERE {{
      ?{item_var} wdt:P31 wd:{item_class_qid} .
      {where}
      ?{item_var} rdfs:label ?{item_var}LabelRu FILTER(LANG(?{item_var}LabelRu) = "ru") .
    }}
    LIMIT {int(limit)}
    """
    rows = rows_from_select(wd.sparql_select(sparql))
    items = []
    for r in rows:
        qid = uri_to_qid(r.get(item_var, ""))
        lbl = r.get(f"{item_var}LabelRu")
        if qid and lbl:
            items.append((qid, lbl))
    return sparql.strip(), items

import os, time, random, json, requests
from typing import Optional

class WDQSTransientError(RuntimeError):
    """Transient WDQS failure (5xx/429/timeouts). Safe to retry/skip."""
    pass

_TRANSIENT_SUBSTRINGS = [
    "transient HTTP 429",
    "transient HTTP 500",
    "transient HTTP 502",
    "transient HTTP 503",
    "transient HTTP 504",
    "Read timed out",
    "timed out",
    "Response ended prematurely",
    "Truncated JSON",
    "Non-JSON response",
    "RemoteDisconnected",
    "Connection aborted",
]

def is_transient_wdqs_error(err: Exception) -> bool:
    s = str(err)
    if any(t in s for t in _TRANSIENT_SUBSTRINGS):
        return True
    # requests.* errors are almost always transient for WDQS
    if isinstance(err, requests.RequestException):
        return True
    if isinstance(err, json.JSONDecodeError):
        return True
    return False


OUT_DIR = globals().get("OUT_DIR", "out_wikidata_benchmark")

def _looks_like_complete_wdqs_json(text: str) -> bool:
    t = (text or "").strip()
    if not (t.startswith("{") and t.endswith("}")):
        return False
    return ('"results"' in t and '"bindings"' in t and '"head"' in t)

def _looks_like_html_or_text(text: str) -> bool:
    t = (text or "").lstrip().lower()
    return t.startswith("<!doctype") or t.startswith("<html") or t.startswith("<head") or t.startswith("<body")

def _dump_bad_response(key: str, text: str, suffix: str = "txt") -> None:
    bad_dir = os.path.join(OUT_DIR, "bad_responses")
    os.makedirs(bad_dir, exist_ok=True)
    path = os.path.join(bad_dir, f"{key}.{suffix}")
    max_chars = 250_000
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write((text or "")[:max_chars])

def _sleep_backoff(attempt: int, resp: Optional[requests.Response] = None) -> None:
    ra = None
    if resp is not None:
        ra = resp.headers.get("Retry-After")
    if ra and ra.strip().isdigit():
        time.sleep(float(ra.strip()))
        return
    time.sleep((2 ** attempt) + random.random())

def sparql_select_robust(self, query: str, use_cache: bool = True) -> dict:
    q = query.strip()
    key = _sha1("SELECT:" + q)

    if use_cache:
        cached = self.cache.get(key)
        if cached is not None:
            return cached

    last_err = None
    last_text = None
    last_resp = None

    for attempt in range(self.max_retries):
        self._sleep_if_needed()
        try:
            headers = {"Accept": "application/sparql-results+json", "Connection": "close", "Accept-Encoding": "identity"}
            resp = self.session.post(
                self.endpoint,
                params={"format": "json"},
                data={"query": q},
                headers=headers,
                timeout=self.timeout,
            )
            self._last_request_ts = time.time()
            last_resp = resp

            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"WDQS transient HTTP {resp.status_code}")
                last_text = resp.text
                _sleep_backoff(attempt, resp)
                continue

            resp.raise_for_status()
            text = resp.text
            last_text = text

            if _looks_like_html_or_text(text) or not text.strip().startswith("{"):
                last_err = ValueError(f"Non-JSON response, ct={resp.headers.get('Content-Type')}, http={resp.status_code}")
                _dump_bad_response(key, text, "nonjson.txt")
                _sleep_backoff(attempt, resp)
                continue

            if not text.strip().endswith("}"):
                last_err = json.JSONDecodeError("Truncated JSON (no closing brace)", text, len(text))
                _dump_bad_response(key, text, "truncated.txt")
                _sleep_backoff(attempt, resp)
                continue

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = json.loads(text, strict=False)

            if not isinstance(data, dict) or "results" not in data:
                last_err = ValueError("Parsed JSON but not WDQS results format")
                _dump_bad_response(key, text, "weird.json")
                _sleep_backoff(attempt, resp)
                continue

            if use_cache:
                self.cache.set(key, data)
            return data

        except (requests.RequestException, ValueError, json.JSONDecodeError) as e:
            last_err = e
            if last_text:
                _dump_bad_response(key, last_text, "bad.txt")
            if attempt == self.max_retries - 1:
                raise (WDQSTransientError(f"WDQS transient failure after {self.max_retries} retries: {e}") if is_transient_wdqs_error(e) else RuntimeError(f"WDQS failed after {self.max_retries} retries: {e}")) from e
            _sleep_backoff(attempt, last_resp)

    raise (WDQSTransientError(f"WDQS transient failure: {last_err}") if (last_err is not None and is_transient_wdqs_error(last_err)) else RuntimeError(f"WDQS failed: {last_err}"))

WikidataClient.sparql_select = sparql_select_robust


def load_or_build_pool_safe(name: str, builder):
    df = load_pool_df(name)
    if df is not None and len(df) > 0:
        return df
    try:
        df = builder()
    except Exception as e:
        print(f"[WARN] pool '{name}' build failed: {e}")
        return pd.DataFrame()
    try:
        save_pool_df(name, df)
    except Exception as e:
        print(f"[WARN] pool '{name}' save failed: {e}")
    return df

load_or_build_pool = load_or_build_pool_safe

print("✅ Patched: WikidataClient.sparql_select (robust) + load_or_build_pool (safe)")


def select_items_with_label_ru_en(
    item_class_qid: str,
    where_lines: List[str],
    limit: int = 300,
    item_var: str = "item",
    use_subclass_closure: bool = True,
) -> Tuple[str, List[Tuple[str,str]]]:
    """
    Выполняет SELECT для items и возвращает (sparql, [(qid, label_ru_or_en), ...]).
    Важно: НЕ требуем rdfs:label LANG='ru' — иначе много нулевых gold на доменах без RU-лейблов.
    """
    where = "\n      ".join(where_lines)
    class_line = (
        f"?{item_var} wdt:P31/wdt:P279* wd:{item_class_qid} ."
        if use_subclass_closure
        else f"?{item_var} wdt:P31 wd:{item_class_qid} ."
    )
    sparql = f"""
    SELECT DISTINCT ?{item_var} ?{item_var}Label WHERE {{
      {class_line}
      {where}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "ru,en". }}
    }}
    LIMIT {int(limit)}
    """
    rows = rows_from_select(wd.sparql_select(sparql))
    items: List[Tuple[str,str]] = []
    for r in rows:
        qid = uri_to_qid(r.get(item_var, ""))
        lbl = r.get(f"{item_var}Label")
        if qid and lbl:
            items.append((qid, lbl))
    return sparql.strip(), items

def select_items_with_ru_label(item_class_qid: str, where_lines: List[str], limit: int = 300, item_var: str = "item"):
    return select_items_with_label_ru_en(item_class_qid, where_lines, limit=limit, item_var=item_var, use_subclass_closure=True)

def select_items_with_ru_label_direct(item_class_qid: str, where_lines: List[str], limit: int = 300, item_var: str = "item"):
    return select_items_with_label_ru_en(item_class_qid, where_lines, limit=limit, item_var=item_var, use_subclass_closure=False)

_POOL_QID_RE = re.compile(r"^Q\d+$")

def _best_label_ru_en_from_entity(ent: Optional[dict]) -> Optional[str]:
    if not ent or not isinstance(ent, dict):
        return None
    labels = (ent.get("labels") or {})
    for lang in ("ru", "en"):
        val = ((labels.get(lang) or {}).get("value") or "").strip()
        if val and not _POOL_QID_RE.fullmatch(val):
            return val
    return None

def fetch_best_labels_ru_en(qids: List[str]) -> Dict[str, str]:
    """
    Добирает нормальные label'ы по QID через wbgetentities.
    Это чинит кейс, когда SERVICE wikibase:label возвращает сам QID (Q12345),
    и такой мусор потом попадает в query_text_ru / constraints.
    """
    out: Dict[str, str] = {}
    uniq = [str(q).strip() for q in dict.fromkeys(qids or []) if str(q).strip()]
    if not uniq:
        return out

    missing: List[str] = []
    cache = getattr(wd, "cache", None)

    for q in uniq:
        cache_key = f"wbterms_ruen_{q}"
        cached = cache.get(cache_key) if cache is not None else None
        if isinstance(cached, dict):
            lbl = str(cached.get("label") or "").strip()
            if lbl and not _POOL_QID_RE.fullmatch(lbl):
                out[q] = lbl
                continue
        missing.append(q)

    for i in range(0, len(missing), 50):
        chunk = missing[i:i+50]
        params = {
            "action": "wbgetentities",
            "ids": "|".join(chunk),
            "props": "labels",
            "languages": "ru|en",
            "format": "json",
        }
        try:
            resp = requests.get(WIKI_API, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            continue

        entities = (data or {}).get("entities", {}) or {}
        for q in chunk:
            ent = entities.get(q)
            lbl = _best_label_ru_en_from_entity(ent)
            if lbl:
                out[q] = lbl
                if cache is not None:
                    try:
                        cache.set(f"wbterms_ruen_{q}", {"label": lbl})
                    except Exception:
                        pass
    return out

def repair_pool_labels(df: pd.DataFrame, qid_col: str, label_col: str) -> pd.DataFrame:
    """
    Нормализует пул значений:
    - убирает пустые/missing labels
    - чинит строки вида Q12345 через wbgetentities (ru -> en)
    - выкидывает строки, которые так и остались QID'ами
    """
    if df is None or len(df) == 0:
        cols = list(df.columns) if isinstance(df, pd.DataFrame) else [qid_col, label_col]
        return pd.DataFrame(columns=cols)

    out = df.copy()

    if qid_col not in out.columns:
        out[qid_col] = None
    if label_col not in out.columns:
        out[label_col] = None

    out[qid_col] = out[qid_col].astype(str).str.strip()
    out[label_col] = out[label_col].fillna("").astype(str).str.strip()

    bad_mask = (
        out[qid_col].str.fullmatch(r"Q\d+").fillna(False)
        & (
            out[label_col].eq("")
            | out[label_col].str.fullmatch(r"Q\d+").fillna(False)
          )
    )

    bad_qids = [q for q in out.loc[bad_mask, qid_col].tolist() if q]
    if bad_qids:
        fixed = fetch_best_labels_ru_en(bad_qids)
        if fixed:
            repair_mask = bad_mask & out[qid_col].isin(fixed.keys())
            out.loc[repair_mask, label_col] = out.loc[repair_mask, qid_col].map(fixed)

    out[label_col] = out[label_col].fillna("").astype(str).str.strip()
    out = out[
        out[qid_col].str.fullmatch(r"Q\d+").fillna(False)
        & out[label_col].ne("")
        & ~out[label_col].str.fullmatch(r"Q\d+").fillna(False)
    ].copy()

    return out.drop_duplicates().reset_index(drop=True)

def build_value_pool_ru(item_class_qid: str, pid: str, val_name: str, limit: int = 400) -> pd.DataFrame:
    """
    Обновлённый пул значений: берём label с fallback "ru,en".
    Дополнительно ремонтируем кейсы, где label-сервис возвращает сам QID.
    """
    sparql = f"""
    SELECT DISTINCT ?val ?valLabel WHERE {{
      ?item wdt:P31/wdt:P279* wd:{item_class_qid} ;
            wdt:{pid} ?val .
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "ru,en". }}
    }}
    LIMIT {int(limit)}
    """
    rows = rows_from_select(wd.sparql_select(sparql))
    data = []
    for r in rows:
        qid = uri_to_qid(r.get("val", ""))
        lbl = r.get("valLabel")
        if qid:
            data.append({f"{val_name}_qid": qid, f"{val_name}LabelRu": lbl})
    df = pd.DataFrame(data)
    return repair_pool_labels(df, f"{val_name}_qid", f"{val_name}LabelRu")

print("✅ Patched: select_items_with_* используют ru/en fallback + repair_pool_labels чинит QID вместо label")
