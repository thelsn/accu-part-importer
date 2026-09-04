from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from curl_cffi import requests as cffi_requests

from .fuzzy import combined_rank_score, product_fuzzy_score
from .settings import COOKIE_PATH

ALGOLIA_APP_ID = "B7KB9U05EO"
ALGOLIA_SEARCH_KEY = "9d5826efb8cb34d798525302f730f868"
ALGOLIA_INDEX = "new_products"
ALGOLIA_URL = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"
ALGOLIA_OBJECT_URL = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}"

PRODUCT_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?accu\.(?:co\.uk|com)(?:/[a-z]{2})?/[^/\s]+/(\d+)(?:-[^\s/]+)?",
    re.I,
)
BARE_ID_RE = re.compile(r"^\d{3,7}$")
STEP_HEADER = b"ISO-10303"

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


@dataclass
class AccuProduct:
    product_id: int
    reference: str
    title: str
    category: str
    uri: str
    image: str
    attributes: str
    manufacturer: str
    features: dict[str, str] = field(default_factory=dict)
    breadcrumb: str = ""
    fuzzy_score: float = 0.0
    combined_score: float = 0.0

    def page_url(self, base_url: str) -> str:
        if self.uri.startswith("http"):
            return self.uri
        return f"{base_url.rstrip('/')}{self.uri}"


def _algolia_json(url: str, payload: dict | None = None, timeout: int = 20) -> dict:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": CHROME_UA,
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "X-Algolia-API-Key": ALGOLIA_SEARCH_KEY,
    }
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        method = "POST"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Accu catalogue HTTP {exc.code}: {body[:240]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Accu catalogue network error: {exc.reason}") from exc
    return json.loads(raw)


def _breadcrumb_text(loc: dict) -> str:
    crumbs = loc.get("breadcrumbs") if isinstance(loc, dict) else None
    names: list[str] = []
    if isinstance(crumbs, list):
        for crumb in crumbs:
            if isinstance(crumb, dict):
                name = str(crumb.get("name") or "").strip()
                if name:
                    names.append(name)
    category = str(loc.get("category_name") or "").strip() if isinstance(loc, dict) else ""
    if names:
        leaf = names[0]
        parent = names[-1] if len(names) > 1 else ""
        if parent and parent != leaf:
            return f"{parent} > {leaf}"
        return leaf
    return category


def preview_image_urls(url: str) -> list[str]:
    """Accu's CDN often serves AVIF for .png URLs. Qt cannot decode AVIF, so force PNG/JPEG."""
    raw = (url or "").strip()
    if not raw:
        return []
    seen: list[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate not in seen:
            seen.append(candidate)

    parts = urlsplit(raw)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for fmt in ("png", "jpg"):
        query_copy = dict(query)
        query_copy["format"] = fmt
        add(urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_copy), parts.fragment)))
    if parts.path.lower().endswith(".png"):
        jpg_path = parts.path[:-4] + ".jpg"
        add(urlunsplit((parts.scheme, parts.netloc, jpg_path, parts.query, parts.fragment)))
    add(raw)
    return seen


@dataclass
class CadModel:
    model_id: int
    product_id: int
    name: str
    path: str
    format: str
    downloads_remaining: int | None = None
    guest: bool = True


def _locale_block(hit: dict) -> dict:
    block = hit.get("en")
    if isinstance(block, dict) and block.get("title"):
        return block
    for key in ("us", "de", "fr", "pl"):
        block = hit.get(key)
        if isinstance(block, dict) and block.get("title"):
            return block
    return {}


def product_from_hit(hit: dict) -> AccuProduct | None:
    try:
        product_id = int(hit.get("objectID") or hit.get("id") or 0)
    except (TypeError, ValueError):
        return None
    if product_id <= 0:
        return None
    loc = _locale_block(hit)
    features = loc.get("features") if isinstance(loc.get("features"), dict) else {}
    return AccuProduct(
        product_id=product_id,
        reference=str(hit.get("reference") or ""),
        title=str(loc.get("title") or hit.get("reference") or f"Product {product_id}"),
        category=str(loc.get("category_name") or ""),
        uri=str(loc.get("uri") or ""),
        image=str(hit.get("image") or ""),
        attributes=str(loc.get("attributes") or ""),
        manufacturer=str(hit.get("manufacturer_name") or ""),
        features={str(k): str(v) for k, v in features.items()},
        breadcrumb=_breadcrumb_text(loc),
    )


class AccuClient:
    def __init__(self, base_url: str = "https://www.accu.co.uk") -> None:
        self.base_url = base_url.rstrip("/")
        self.session = cffi_requests.Session(impersonate="chrome")
        self.session.headers.update(
            {
                "User-Agent": CHROME_UA,
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            }
        )
        self._lock = threading.Lock()
        self._load_cookies()

    def _load_cookies(self) -> None:
        if not COOKIE_PATH.exists():
            return
        try:
            data = json.loads(COOKIE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.session.cookies.update(data)
        except (OSError, json.JSONDecodeError):
            pass

    def _cookie_dict(self) -> dict[str, str]:
        cookies: dict[str, str] = {}
        jar = self.session.cookies
        getter = getattr(jar, "get_dict", None)
        if callable(getter):
            try:
                cookies = dict(getter())
            except Exception:
                cookies = {}
        if not cookies:
            try:
                for cookie in jar:
                    name = getattr(cookie, "name", None)
                    value = getattr(cookie, "value", None)
                    if name:
                        cookies[str(name)] = str(value or "")
            except TypeError:
                try:
                    cookies = dict(jar)
                except Exception:
                    cookies = {}
        return cookies

    def _save_cookies(self) -> None:
        COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
        COOKIE_PATH.write_text(json.dumps(self._cookie_dict(), indent=2), encoding="utf-8")

    def extract_product_id(self, text: str) -> int | None:
        raw = (text or "").strip()
        if not raw:
            return None
        match = PRODUCT_URL_RE.search(raw)
        if match:
            return int(match.group(1))
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        if parsed.netloc.endswith("accu.co.uk") or parsed.netloc.endswith("accu.com"):
            match = PRODUCT_URL_RE.search(raw)
            if match:
                return int(match.group(1))
        if BARE_ID_RE.match(raw):
            return int(raw)
        return None

    def _algolia_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Algolia-Application-Id": ALGOLIA_APP_ID,
            "X-Algolia-API-Key": ALGOLIA_SEARCH_KEY,
        }

    def get_product(self, product_id: int) -> AccuProduct | None:
        try:
            data = _algolia_json(f"{ALGOLIA_OBJECT_URL}/{product_id}")
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
        return product_from_hit(data)

    def search(self, query: str, hits_per_page: int = 40) -> list[AccuProduct]:
        query = (query or "").strip()
        if not query:
            return []

        product_id = self.extract_product_id(query)
        if product_id:
            product = self.get_product(product_id)
            if product:
                product.fuzzy_score = 100.0
                product.combined_score = 100.0
                return [product]

        payload = _algolia_json(
            ALGOLIA_URL,
            {
                "query": query,
                "hitsPerPage": hits_per_page,
                "restrictSearchableAttributes": [
                    "manufacturer_name",
                    "reference",
                    "en.title",
                    "en.meta_keywords",
                    "en.meta_description",
                    "en.category_name",
                    "en.attributes",
                ],
            },
        )
        hits = payload.get("hits") or []
        products: list[AccuProduct] = []
        for index, hit in enumerate(hits):
            product = product_from_hit(hit)
            if product is None:
                continue
            product.fuzzy_score = product_fuzzy_score(
                query,
                product.reference,
                product.title,
                product.category,
                product.attributes,
            )
            product.combined_score = combined_rank_score(
                product.fuzzy_score, index, len(hits)
            )
            products.append(product)
        products.sort(key=lambda item: item.combined_score, reverse=True)
        return products

    def _accu_get(self, path: str, params: dict | None = None, allow_redirects: bool = True):
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        with self._lock:
            return self.session.get(
                url, params=params, timeout=45, allow_redirects=allow_redirects
            )

    def _accu_post(self, path: str, data=None, json_body=None, allow_redirects: bool = True):
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        with self._lock:
            return self.session.post(
                url,
                data=data,
                json=json_body,
                timeout=45,
                allow_redirects=allow_redirects,
            )

    def logged_in(self) -> bool:
        try:
            response = self._accu_get("/api/user")
            if response.status_code != 200:
                return False
            if "application/json" not in (response.headers.get("content-type") or ""):
                return False
            data = response.json()
            return bool(data.get("email") or data.get("id_customer") or data.get("data"))
        except Exception:
            return False

    def login(self, email: str, password: str) -> None:
        if not email or not password:
            raise RuntimeError("Accu email and password are required to download CAD models.")

        self._accu_get("/authentication")
        response = self._accu_post(
            "/authentication",
            data={
                "email": email,
                "passwd": password,
                "SubmitLogin": "1",
                "back": "",
            },
        )
        self._save_cookies()
        if self.logged_in():
            return
        if response.status_code >= 400:
            raise RuntimeError(f"Accu login returned HTTP {response.status_code}.")
        raise RuntimeError(
            "Could not sign in to Accu. Check the email and password in Settings."
        )

    def ensure_login(self, email: str, password: str) -> None:
        if self.logged_in():
            return
        self.login(email, password)

    def list_models(self, product_id: int) -> tuple[list[CadModel], dict]:
        response = self._accu_get(
            "/api/models", params={"action": "list", "id_product": product_id}
        )
        if "just a moment" in response.text[:400].lower():
            raise RuntimeError(
                "Accu blocked the request with Cloudflare. Try again in a moment."
            )
        if response.status_code != 200:
            raise RuntimeError(f"Model list failed: HTTP {response.status_code}")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError("Accu model list did not return JSON. You may need to log in.") from exc
        models = []
        for item in payload.get("models") or []:
            fmt = str(item.get("format") or "").upper()
            models.append(
                CadModel(
                    model_id=int(item["id"]),
                    product_id=int(item.get("product_id") or product_id),
                    name=str(item.get("name") or item.get("path") or ""),
                    path=str(item.get("path") or ""),
                    format=fmt,
                    downloads_remaining=payload.get("downloads"),
                    guest=bool(payload.get("guest", True)),
                )
            )
        return models, payload

    def pick_step_model(self, product_id: int) -> CadModel:
        models, payload = self.list_models(product_id)
        preferred = next((m for m in models if m.format in {"STP", "STEP", "STPZ"}), None)
        if preferred is None:
            preferred = next((m for m in models if m.format in {"IGS", "IGES"}), None)
        if preferred is None:
            raise RuntimeError("Accu has no STEP or IGES model for this product.")
        remaining = payload.get("downloads")
        if remaining == 0 and payload.get("guest") is False:
            raise RuntimeError(
                "Accu daily CAD download limit has been reached. It resets within 24 hours."
            )
        return preferred

    def download_model(self, model: CadModel, dest: Path, email: str = "", password: str = "") -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not self.logged_in():
            if email and password:
                self.ensure_login(email, password)
            else:
                raise RuntimeError(
                    "Accu requires an account login to download CAD models. Add it in Settings."
                )

        response = self._accu_get(
            "/api/models",
            params={"action": "download", "id": model.model_id},
            allow_redirects=False,
        )
        if response.status_code in {301, 302, 303, 307, 308}:
            raise RuntimeError(
                "Accu redirected the CAD download. The saved login may have expired; try Settings again."
            )
        content_type = (response.headers.get("content-type") or "").lower()
        body = response.content or b""

        if response.status_code in {401, 403} or b"log in" in body[:800].lower() or (
            "text/html" in content_type and STEP_HEADER not in body[:200]
        ):
            raise RuntimeError("Accu requires an account login to download CAD models.")

        if response.status_code != 200:
            raise RuntimeError(f"CAD download failed: HTTP {response.status_code}")
        if "text/html" in content_type and STEP_HEADER not in body[:200]:
            snippet = body[:300].decode("utf-8", errors="ignore").lower()
            if "limit" in snippet:
                raise RuntimeError("Accu daily CAD download limit has been reached.")
            if "log in" in snippet or "sign in" in snippet:
                raise RuntimeError("Accu requires an account login to download CAD models.")
            raise RuntimeError("Accu returned a web page instead of a CAD file.")
        if not body:
            raise RuntimeError("Accu returned an empty CAD file.")

        dest.write_bytes(body)
        self._save_cookies()
        return dest

    def fetch_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": CHROME_UA})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read() or b""
        except Exception:
            with self._lock:
                response = self.session.get(url, timeout=20)
            response.raise_for_status()
            return response.content or b""

    def fetch_preview_image(self, url: str) -> bytes:
        last_error: Exception | None = None
        for candidate in preview_image_urls(url):
            try:
                data = self.fetch_bytes(candidate)
            except Exception as exc:
                last_error = exc
                continue
            if len(data) >= 12 and data[4:8] == b"ftyp":
                continue
            if data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff") or data.startswith(b"RIFF"):
                return data
            if data and data[:1] not in {b"<", b"{"}:
                return data
        if last_error is not None:
            raise last_error
        raise RuntimeError("Accu image was AVIF or empty; Qt cannot display it.")
