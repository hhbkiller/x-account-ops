#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests


ENV_ALIASES: Dict[str, List[str]] = {
    "client_id": ["Client ID", "X_CLIENT_ID", "CLIENT_ID"],
    "client_secret": ["Client Secret", "X_CLIENT_SECRET", "CLIENT_SECRET"],
    "access_token": ["Access Token", "X_ACCESS_TOKEN", "ACCESS_TOKEN"],
    "refresh_token": ["Refresh Token", "X_REFRESH_TOKEN", "REFRESH_TOKEN"],
    "user_id": ["User ID", "X_USER_ID", "USER_ID"],
    "consumer_key": ["Consumer Key", "X_CONSUMER_KEY", "X_API_KEY"],
    "consumer_secret": ["Consumer Key Secret", "X_CONSUMER_SECRET", "X_API_SECRET"],
    "auth1_access_token": ["auth1 Access Token", "X_AUTH1_ACCESS_TOKEN", "X_ACCESS_TOKEN_OAUTH1"],
    "auth1_access_secret": ["auth1 Access Secret", "X_AUTH1_ACCESS_SECRET", "X_ACCESS_TOKEN_SECRET"],
    "http_proxy": ["HTTP_PROXY", "http_proxy"],
    "https_proxy": ["HTTPS_PROXY", "https_proxy"],
    "all_proxy": ["ALL_PROXY", "all_proxy"],
}

PREFERRED_ENV_KEYS = {
    "client_id": "Client ID",
    "client_secret": "Client Secret",
    "access_token": "Access Token",
    "refresh_token": "Refresh Token",
    "user_id": "User ID",
    "consumer_key": "Consumer Key",
    "consumer_secret": "Consumer Key Secret",
    "auth1_access_token": "auth1 Access Token",
    "auth1_access_secret": "auth1 Access Secret",
    "http_proxy": "HTTP_PROXY",
    "https_proxy": "HTTPS_PROXY",
    "all_proxy": "ALL_PROXY",
}

TOKEN_URL = "https://api.x.com/2/oauth2/token"
API_BASE = "https://api.x.com/2"
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def truthy(value: Any) -> bool:
    return bool(value)


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[。！？.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def normalize_article_text(text: str) -> List[str]:
    raw_lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\s*#+\s*", "", line.rstrip())
        raw_lines.append(cleaned)
    normalized = "\n".join(raw_lines)
    paragraphs = []
    for block in re.split(r"\n\s*\n", normalized):
        compact = re.sub(r"\s+", " ", block).strip()
        if compact:
            paragraphs.append(compact)
    return paragraphs


def split_long_unit(unit: str, max_chars: int) -> List[str]:
    if len(unit) <= max_chars:
        return [unit]
    sentences = split_sentences(unit)
    if len(sentences) > 1:
        return pack_units(sentences, max_chars)
    words = unit.split()
    parts: List[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            parts.append(current)
        if len(word) <= max_chars:
            current = word
            continue
        for start in range(0, len(word), max_chars):
            parts.append(word[start : start + max_chars])
        current = ""
    if current:
        parts.append(current)
    return parts


def pack_units(units: Sequence[str], max_chars: int) -> List[str]:
    chunks: List[str] = []
    current = ""
    for unit in units:
        for segment in split_long_unit(unit, max_chars):
            candidate = segment if not current else f"{current}\n\n{segment}"
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = segment
    if current:
        chunks.append(current)
    return chunks


def build_thread_segments(text: str, max_chars: int, title: Optional[str] = None) -> List[str]:
    numbering_reserve = 8
    usable = max(40, max_chars - numbering_reserve)
    units = normalize_article_text(text)
    if title:
        units.insert(0, title.strip())
    chunks = pack_units(units, usable)
    total = len(chunks)
    return [f"{index}/{total}\n\n{chunk}".strip() for index, chunk in enumerate(chunks, start=1)]


def response_json(response: requests.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


class SkillError(RuntimeError):
    pass


class XApiError(SkillError):
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self.payload = payload
        super().__init__(self._build_message(payload))

    @staticmethod
    def _build_message(payload: Any) -> str:
        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                first = errors[0]
                if isinstance(first, dict):
                    return first.get("detail") or first.get("title") or json.dumps(first, ensure_ascii=False)
            detail = payload.get("detail")
            if isinstance(detail, str):
                return detail
        return json.dumps(payload, ensure_ascii=False)


class NetworkError(SkillError):
    pass


def percent_encode(value: Any) -> str:
    return urllib.parse.quote(str(value), safe="~-._")


def build_oauth1_header(
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    parsed = urllib.parse.urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": os.urandom(16).hex(),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    signature_params: List[Tuple[str, str]] = []
    for key, value in query_params:
        signature_params.append((percent_encode(key), percent_encode(value)))
    if params:
        for key, value in params.items():
            signature_params.append((percent_encode(key), percent_encode(value)))
    for key, value in oauth_params.items():
        signature_params.append((percent_encode(key), percent_encode(value)))
    signature_params.sort()
    parameter_string = "&".join(f"{key}={value}" for key, value in signature_params)
    base_string = "&".join(
        [
            method.upper(),
            percent_encode(base_url),
            percent_encode(parameter_string),
        ]
    )
    signing_key = f"{percent_encode(consumer_secret)}&{percent_encode(token_secret)}"
    digest = hmac.new(signing_key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1).digest()
    oauth_params["oauth_signature"] = base64.b64encode(digest).decode("ascii")
    return "OAuth " + ", ".join(
        f'{percent_encode(key)}="{percent_encode(value)}"' for key, value in sorted(oauth_params.items())
    )


class EnvStore:
    def __init__(self, path: Path):
        self.path = path
        self.lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True) if self.path.exists() else []

    def _iter_entries(self) -> Iterable[Tuple[int, str, str, str]]:
        for index, line in enumerate(self.lines):
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            line_ending = "\n" if line.endswith("\n") else ""
            yield index, key.strip(), value.rstrip("\r\n"), line_ending

    def get(self, aliases: Sequence[str]) -> Optional[str]:
        for alias in aliases:
            env_value = os.environ.get(alias)
            if env_value:
                return env_value
        alias_set = {normalize_key(alias) for alias in aliases}
        for _, key, value, _ in self._iter_entries():
            if normalize_key(key) in alias_set:
                return value
        return None

    def set(self, aliases: Sequence[str], preferred_key: str, value: str) -> None:
        alias_set = {normalize_key(alias) for alias in aliases}
        for index, key, _, line_ending in self._iter_entries():
            if normalize_key(key) in alias_set:
                ending = line_ending or "\n"
                self.lines[index] = f"{key}={value}{ending}"
                return
        if self.lines and not self.lines[-1].endswith("\n"):
            self.lines[-1] = self.lines[-1] + "\n"
        self.lines.append(f"{preferred_key}={value}\n")

    def save(self) -> None:
        self.path.write_text("".join(self.lines), encoding="utf-8")


@dataclass
class Credentials:
    client_id: str
    client_secret: Optional[str]
    access_token: str
    refresh_token: Optional[str]
    user_id: Optional[str]
    consumer_key: Optional[str]
    consumer_secret: Optional[str]
    auth1_access_token: Optional[str]
    auth1_access_secret: Optional[str]
    http_proxy: Optional[str]
    https_proxy: Optional[str]
    all_proxy: Optional[str]


class CredentialStore:
    def __init__(self, env_file: Path):
        self.env_file = env_file
        self.env = EnvStore(env_file)

    def load(self) -> Credentials:
        values = {}
        for field_name, aliases in ENV_ALIASES.items():
            values[field_name] = self.env.get(aliases)
        missing = [name for name in ("client_id", "access_token") if not values.get(name)]
        if missing:
            raise SkillError(f"Missing required credentials: {', '.join(missing)}")
        return Credentials(
            client_id=values["client_id"],
            client_secret=values.get("client_secret"),
            access_token=values["access_token"],
            refresh_token=values.get("refresh_token"),
            user_id=values.get("user_id"),
            consumer_key=values.get("consumer_key"),
            consumer_secret=values.get("consumer_secret"),
            auth1_access_token=values.get("auth1_access_token"),
            auth1_access_secret=values.get("auth1_access_secret"),
            http_proxy=values.get("http_proxy"),
            https_proxy=values.get("https_proxy"),
            all_proxy=values.get("all_proxy"),
        )

    def save_tokens(self, access_token: str, refresh_token: Optional[str], user_id: Optional[str]) -> None:
        self.env.set(ENV_ALIASES["access_token"], PREFERRED_ENV_KEYS["access_token"], access_token)
        if refresh_token:
            self.env.set(ENV_ALIASES["refresh_token"], PREFERRED_ENV_KEYS["refresh_token"], refresh_token)
        if user_id:
            self.env.set(ENV_ALIASES["user_id"], PREFERRED_ENV_KEYS["user_id"], user_id)
        self.env.save()


class XClient:
    def __init__(self, store: CredentialStore):
        self.store = store
        self.credentials = store.load()
        self.session = requests.Session()
        self._apply_proxies()

    def _apply_proxies(self) -> None:
        proxies = {}
        if self.credentials.http_proxy:
            proxies["http"] = self.credentials.http_proxy
        if self.credentials.https_proxy:
            proxies["https"] = self.credentials.https_proxy
        if self.credentials.all_proxy:
            proxies.setdefault("http", self.credentials.all_proxy)
            proxies.setdefault("https", self.credentials.all_proxy)
        if proxies:
            self.session.proxies.update(proxies)

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.credentials.access_token}"}

    def has_oauth1(self) -> bool:
        return all(
            [
                self.credentials.consumer_key,
                self.credentials.consumer_secret,
                self.credentials.auth1_access_token,
                self.credentials.auth1_access_secret,
            ]
        )

    def _oauth1_headers(self, method: str, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        if not self.has_oauth1():
            raise SkillError("OAuth 1.0a credentials are missing; media fallback is unavailable.")
        return {
            "Authorization": build_oauth1_header(
                method,
                url,
                self.credentials.consumer_key or "",
                self.credentials.consumer_secret or "",
                self.credentials.auth1_access_token or "",
                self.credentials.auth1_access_secret or "",
                params=params,
            )
        }

    def _oauth1_request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        expected: Sequence[int] = (200,),
    ) -> Any:
        headers = self._oauth1_headers(method, url, params=data if data else params)
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                data=data,
                json=json_body,
                files=files,
                headers=headers,
                timeout=60,
            )
        except requests.RequestException as exc:
            raise NetworkError(f"OAuth1 request failed for {method} {url}: {exc}") from exc
        payload = response_json(response)
        if response.status_code not in expected:
            raise XApiError(response.status_code, payload)
        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        expected: Sequence[int] = (200,),
        retry_on_auth: bool = True,
    ) -> Any:
        url = f"{API_BASE}{path}"
        headers = self._auth_headers()
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                json=json_body,
                data=data,
                files=files,
                headers=headers,
                timeout=60,
            )
        except requests.RequestException as exc:
            raise NetworkError(f"Network request failed for {method} {path}: {exc}") from exc
        if response.status_code == 401 and retry_on_auth and self.credentials.refresh_token:
            self.refresh_access_token()
            return self._request(
                method,
                path,
                params=params,
                json_body=json_body,
                data=data,
                files=files,
                expected=expected,
                retry_on_auth=False,
            )
        payload = response_json(response)
        if response.status_code not in expected:
            raise XApiError(response.status_code, payload)
        return payload

    def refresh_access_token(self) -> Dict[str, Any]:
        if not self.credentials.refresh_token:
            raise SkillError("Refresh Token is missing; cannot refresh access token.")
        form = {
            "refresh_token": self.credentials.refresh_token,
            "grant_type": "refresh_token",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self.credentials.client_secret:
            raw = f"{self.credentials.client_id}:{self.credentials.client_secret}".encode("utf-8")
            headers["Authorization"] = f"Basic {base64.b64encode(raw).decode('ascii')}"
        else:
            form["client_id"] = self.credentials.client_id
        try:
            response = self.session.post(TOKEN_URL, data=form, headers=headers, timeout=60)
        except requests.RequestException as exc:
            raise NetworkError(f"Token refresh request failed: {exc}") from exc
        payload = response_json(response)
        if response.status_code != 200:
            raise XApiError(response.status_code, payload)
        access_token = payload.get("access_token")
        if not access_token:
            raise SkillError("Refresh succeeded but no access_token was returned.")
        refresh_token = payload.get("refresh_token") or self.credentials.refresh_token
        self.credentials.access_token = access_token
        self.credentials.refresh_token = refresh_token
        self.store.save_tokens(access_token, refresh_token, self.credentials.user_id)
        return {
            "ok": True,
            "refreshed_at": iso_now(),
            "has_refresh_token": bool(refresh_token),
        }

    def me(self) -> Dict[str, Any]:
        payload = self._request(
            "GET",
            "/users/me",
            params={"user.fields": "created_at,description,public_metrics,username,verified"},
        )
        data = payload.get("data", {})
        user_id = data.get("id")
        if user_id and user_id != self.credentials.user_id:
            self.credentials.user_id = user_id
            self.store.save_tokens(self.credentials.access_token, self.credentials.refresh_token, user_id)
        return payload

    def verify_oauth1_account(self) -> Dict[str, Any]:
        if not self.has_oauth1():
            raise SkillError("OAuth 1.0a credentials are missing; cannot verify Auth1 account.")
        return self._oauth1_request(
            "GET",
            "https://api.x.com/1.1/account/verify_credentials.json",
            expected=(200,),
        )

    def ensure_user_id(self) -> str:
        if self.credentials.user_id:
            return self.credentials.user_id
        payload = self.me()
        user_id = payload.get("data", {}).get("id")
        if not user_id:
            raise SkillError("Could not determine user id for the authenticated account.")
        self.credentials.user_id = user_id
        return user_id

    def search_recent(self, query: str, max_results: int) -> Dict[str, Any]:
        params = {
            "query": query,
            "max_results": clamp(max_results, 10, 100),
            "tweet.fields": "author_id,conversation_id,created_at,lang,public_metrics,referenced_tweets",
            "expansions": "author_id",
            "user.fields": "name,username,verified",
        }
        return self._request("GET", "/tweets/search/recent", params=params)

    def lookup_post(self, post_id: str) -> Dict[str, Any]:
        params = {
            "tweet.fields": "author_id,conversation_id,created_at,lang,public_metrics,referenced_tweets",
            "expansions": "author_id",
            "user.fields": "name,username,verified",
        }
        payload = self._request("GET", f"/tweets/{post_id}", params=params)
        if isinstance(payload, dict) and payload.get("errors") and not payload.get("data"):
            first = payload["errors"][0]
            if isinstance(first, dict):
                detail = first.get("detail") or first.get("title") or json.dumps(first, ensure_ascii=False)
            else:
                detail = str(first)
            raise SkillError(detail)
        return payload

    def create_post(
        self,
        text: str,
        *,
        media_ids: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
        quote_tweet_id: Optional[str] = None,
        reply_settings: Optional[str] = None,
        made_with_ai: bool = False,
        prefer_oauth1: bool = False,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"text": text}
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        if reply_to:
            body["reply"] = {
                "in_reply_to_tweet_id": reply_to,
                "auto_populate_reply_metadata": True,
            }
        if quote_tweet_id:
            body["quote_tweet_id"] = quote_tweet_id
        if reply_settings:
            body["reply_settings"] = reply_settings
        if made_with_ai:
            body["made_with_ai"] = True
        if prefer_oauth1:
            return self._oauth1_request("POST", f"{API_BASE}/tweets", json_body=body, expected=(201,))
        return self._request("POST", "/tweets", json_body=body, expected=(201,))

    def upload_image(self, image_path: Path) -> Dict[str, Any]:
        if not image_path.exists():
            raise SkillError(f"Image not found: {image_path}")
        size = image_path.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise SkillError(f"Image exceeds 5 MB limit: {image_path}")
        mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
        image_bytes = image_path.read_bytes()
        if self.has_oauth1():
            payload = self._oauth1_request(
                "POST",
                "https://upload.twitter.com/1.1/media/upload.json",
                files={"media": (image_path.name, image_bytes, mime_type)},
            )
            media_id = payload.get("media_id_string") or payload.get("media_id")
            if media_id:
                return {"data": {"id": str(media_id)}, "raw": payload}
            return payload
        files = {"media": (image_path.name, image_bytes, mime_type)}
        data = {
            "media_type": mime_type,
            "media_category": "tweet_image",
        }
        return self._request("POST", "/media/upload", data=data, files=files)

    def like(self, post_id: str) -> Dict[str, Any]:
        user_id = self.ensure_user_id()
        return self._request(
            "POST",
            f"/users/{user_id}/likes",
            json_body={"tweet_id": post_id},
            expected=(200,),
        )

    def repost(self, post_id: str) -> Dict[str, Any]:
        user_id = self.ensure_user_id()
        return self._request(
            "POST",
            f"/users/{user_id}/retweets",
            json_body={"tweet_id": post_id},
            expected=(200,),
        )

    def delete_post(self, post_id: str) -> Dict[str, Any]:
        if self.has_oauth1():
            return self._oauth1_request("DELETE", f"{API_BASE}/tweets/{post_id}", expected=(200,))
        return self._request("DELETE", f"/tweets/{post_id}", expected=(200,))


def format_search_results(payload: Dict[str, Any], sort_mode: str) -> Dict[str, Any]:
    users = {
        user["id"]: user
        for user in payload.get("includes", {}).get("users", [])
        if isinstance(user, dict) and user.get("id")
    }
    results = []
    now = datetime.now(timezone.utc)
    for item in payload.get("data", []):
        metrics = item.get("public_metrics", {})
        author = users.get(item.get("author_id"), {})
        created_at = parse_iso(item.get("created_at"))
        age_hours = max(0.05, ((now - created_at).total_seconds() / 3600.0) if created_at else 1.0)
        engagement = (
            metrics.get("like_count", 0)
            + 2 * metrics.get("reply_count", 0)
            + 2 * metrics.get("retweet_count", 0)
            + 3 * metrics.get("quote_count", 0)
        )
        hot_score = round(engagement / (age_hours + 2.0) ** 0.35, 2)
        results.append(
            {
                "id": item.get("id"),
                "text": item.get("text"),
                "created_at": item.get("created_at"),
                "author_id": item.get("author_id"),
                "author_name": author.get("name"),
                "author_username": author.get("username"),
                "author_verified": author.get("verified"),
                "public_metrics": metrics,
                "engagement_score": engagement,
                "hot_score": hot_score,
                "is_reply": any(ref.get("type") == "replied_to" for ref in item.get("referenced_tweets", []) if isinstance(ref, dict)),
                "is_quote": any(ref.get("type") == "quoted" for ref in item.get("referenced_tweets", []) if isinstance(ref, dict)),
                "is_repost": any(ref.get("type") == "retweeted" for ref in item.get("referenced_tweets", []) if isinstance(ref, dict)),
                "starts_with_mention": str(item.get("text") or "").lstrip().startswith("@"),
                "url": f"https://x.com/{author.get('username', 'i')}/status/{item.get('id')}",
            }
        )
    if sort_mode == "hot":
        results.sort(key=lambda item: (item["hot_score"], item["engagement_score"]), reverse=True)
    return {
        "meta": payload.get("meta", {}),
        "results": results,
    }


def filter_ranked_results(
    results: Sequence[Dict[str, Any]],
    *,
    skip_replies: bool,
    skip_mentions: bool,
    skip_reposts: bool,
) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for item in results:
        if skip_replies and truthy(item.get("is_reply")):
            continue
        if skip_mentions and truthy(item.get("starts_with_mention")):
            continue
        if skip_reposts and truthy(item.get("is_repost")):
            continue
        filtered.append(item)
    return filtered


def load_text_argument(args: argparse.Namespace, flag_name: str) -> Optional[str]:
    direct = getattr(args, flag_name, None)
    if direct:
        return direct.strip()
    file_value = getattr(args, f"{flag_name}_file", None)
    if file_value:
        return read_text_file(Path(file_value))
    return None


def render_reply_text(template: str, post: Dict[str, Any], topic: str) -> str:
    excerpt = post["text"].strip().replace("\n", " ")
    excerpt = excerpt[:120] + ("..." if len(excerpt) > 120 else "")
    mapping = {
        "author": post.get("author_name") or "",
        "author_name": post.get("author_name") or "",
        "username": post.get("author_username") or "",
        "text": post.get("text") or "",
        "excerpt": excerpt,
        "id": post.get("id") or "",
        "url": post.get("url") or "",
        "topic": topic,
    }
    try:
        return template.format(**mapping).strip()
    except KeyError as exc:
        raise SkillError(f"Unknown placeholder in reply template: {exc}") from exc


def command_me(client: XClient, _: argparse.Namespace) -> Dict[str, Any]:
    return client.me()


def command_refresh(client: XClient, _: argparse.Namespace) -> Dict[str, Any]:
    return client.refresh_access_token()


def command_doctor(client: XClient, args: argparse.Namespace) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "env_file": str(Path(args.env_file).resolve()),
        "oauth2": {
            "has_client_id": bool(client.credentials.client_id),
            "has_access_token": bool(client.credentials.access_token),
            "has_refresh_token": bool(client.credentials.refresh_token),
        },
        "oauth1": {
            "has_consumer_key": bool(client.credentials.consumer_key),
            "has_consumer_secret": bool(client.credentials.consumer_secret),
            "has_access_token": bool(client.credentials.auth1_access_token),
            "has_access_secret": bool(client.credentials.auth1_access_secret),
        },
    }
    try:
        oauth2_me = client.me()
        report["oauth2"]["ok"] = True
        report["oauth2"]["user"] = oauth2_me.get("data", {})
    except SkillError as exc:
        report["oauth2"]["ok"] = False
        report["oauth2"]["error"] = str(exc)
    if client.has_oauth1():
        try:
            auth1_me = client.verify_oauth1_account()
            report["oauth1"]["ok"] = True
            report["oauth1"]["user"] = {
                "id": auth1_me.get("id_str") or auth1_me.get("id"),
                "name": auth1_me.get("name"),
                "username": auth1_me.get("screen_name"),
            }
        except SkillError as exc:
            report["oauth1"]["ok"] = False
            report["oauth1"]["error"] = str(exc)
    else:
        report["oauth1"]["ok"] = False
        report["oauth1"]["error"] = "OAuth 1.0a credentials missing"
    report["media_upload_path"] = "oauth1" if client.has_oauth1() else "oauth2"
    return report


def command_search(client: XClient, args: argparse.Namespace) -> Dict[str, Any]:
    payload = client.search_recent(args.query, args.search_max)
    formatted = format_search_results(payload, args.sort)
    filtered = filter_ranked_results(
        formatted["results"],
        skip_replies=args.skip_replies,
        skip_mentions=args.skip_mentions,
        skip_reposts=args.skip_reposts,
    )
    limited = filtered[: args.limit]
    return {
        "query": args.query,
        "sort": args.sort,
        "limit": args.limit,
        "meta": formatted["meta"],
        "results": limited,
    }


def command_lookup(client: XClient, args: argparse.Namespace) -> Dict[str, Any]:
    return client.lookup_post(args.tweet_id)


def command_post(client: XClient, args: argparse.Namespace) -> Dict[str, Any]:
    text = load_text_argument(args, "text")
    if not text:
        raise SkillError("Post text is required.")
    media_ids: List[str] = []
    if args.image:
        upload = client.upload_image(Path(args.image))
        media_id = upload.get("data", {}).get("id")
        if not media_id:
            raise SkillError(f"Image upload did not return a media id: {json.dumps(upload, ensure_ascii=False)}")
        media_ids.append(media_id)
    if args.dry_run:
        return {
            "dry_run": True,
            "text": text,
            "media_ids": media_ids,
            "reply_settings": args.reply_settings,
            "made_with_ai": args.made_with_ai,
        }
    created = client.create_post(
        text,
        media_ids=media_ids or None,
        reply_settings=args.reply_settings,
        made_with_ai=args.made_with_ai,
        prefer_oauth1=bool(media_ids),
    )
    return {"ok": True, "created": created}


def _publish_thread(client: XClient, args: argparse.Namespace) -> Dict[str, Any]:
    text = load_text_argument(args, "text")
    if not text:
        raise SkillError("Thread text is required.")
    segments = build_thread_segments(text, args.max_chars, title=args.title)
    preview = [{"index": idx + 1, "text": segment} for idx, segment in enumerate(segments)]
    if args.dry_run:
        return {"dry_run": True, "segments": preview}
    media_ids: List[str] = []
    if args.image:
        upload = client.upload_image(Path(args.image))
        media_id = upload.get("data", {}).get("id")
        if not media_id:
            raise SkillError(f"Image upload did not return a media id: {json.dumps(upload, ensure_ascii=False)}")
        media_ids.append(media_id)
    created_posts = []
    previous_post_id: Optional[str] = None
    for index, segment in enumerate(segments):
        response = client.create_post(
            segment,
            media_ids=media_ids if index == 0 and media_ids else None,
            reply_to=previous_post_id,
            made_with_ai=args.made_with_ai if index == 0 else False,
            prefer_oauth1=bool(index == 0 and media_ids),
        )
        created_posts.append(response)
        previous_post_id = response.get("data", {}).get("id")
        if not previous_post_id:
            raise SkillError(f"Thread step {index + 1} did not return a post id.")
        time.sleep(1.0)
    return {"ok": True, "segments": len(segments), "posts": created_posts}


def command_thread(client: XClient, args: argparse.Namespace) -> Dict[str, Any]:
    return _publish_thread(client, args)


def command_article(client: XClient, args: argparse.Namespace) -> Dict[str, Any]:
    if getattr(args, "as_thread", False):
        return _publish_thread(client, args)
    raise SkillError(
        "X Articles and threads are different publishing modes. "
        "This skill no longer maps `article` to a thread by default. "
        "Use `thread` if you want a numbered thread, or pass `article --as-thread` only for backward compatibility."
    )


def command_reply(client: XClient, args: argparse.Namespace) -> Dict[str, Any]:
    text = load_text_argument(args, "text")
    if not text:
        raise SkillError("Reply text is required.")
    if args.dry_run:
        return {"dry_run": True, "tweet_id": args.tweet_id, "text": text}
    response = client.create_post(text, reply_to=args.tweet_id)
    return {"ok": True, "reply": response}


def command_like(client: XClient, args: argparse.Namespace) -> Dict[str, Any]:
    if args.dry_run:
        return {"dry_run": True, "tweet_id": args.tweet_id}
    return {"ok": True, "like": client.like(args.tweet_id)}


def command_repost(client: XClient, args: argparse.Namespace) -> Dict[str, Any]:
    if args.dry_run:
        return {"dry_run": True, "tweet_id": args.tweet_id}
    return {"ok": True, "repost": client.repost(args.tweet_id)}


def command_delete(client: XClient, args: argparse.Namespace) -> Dict[str, Any]:
    if args.dry_run:
        return {"dry_run": True, "tweet_id": args.tweet_id}
    return {"ok": True, "delete": client.delete_post(args.tweet_id)}


def command_hot_reply(client: XClient, args: argparse.Namespace) -> Dict[str, Any]:
    direct_text = load_text_argument(args, "reply_text")
    reply_template = args.reply_template
    if not direct_text and not reply_template:
        raise SkillError("Provide --reply-text, --reply-text-file, or --reply-template.")
    payload = client.search_recent(args.query, args.search_max)
    formatted = format_search_results(payload, "hot")
    ranked = filter_ranked_results(
        formatted["results"],
        skip_replies=args.skip_replies,
        skip_mentions=args.skip_mentions,
        skip_reposts=args.skip_reposts,
    )
    me_id = client.ensure_user_id()
    candidates = []
    for post in ranked:
        if args.skip_self and post.get("author_id") == me_id:
            continue
        if post["hot_score"] < args.min_hot_score:
            continue
        reply_text = direct_text or render_reply_text(reply_template, post, args.query)
        candidates.append(
            {
                "target": post,
                "reply_text": reply_text,
            }
        )
        if len(candidates) >= args.limit:
            break
    if args.dry_run:
        return {"dry_run": True, "query": args.query, "targets": candidates}
    sent = []
    for item in candidates:
        if args.channel == "quote":
            response = client.create_post(item["reply_text"], quote_tweet_id=item["target"]["id"])
        else:
            response = client.create_post(item["reply_text"], reply_to=item["target"]["id"])
        sent.append(
            {
                "target": item["target"],
                "channel": args.channel,
                "reply_text": item["reply_text"],
                "response": response,
            }
        )
        time.sleep(1.0)
    return {"ok": True, "query": args.query, "channel": args.channel, "replied": sent}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate an X account with OAuth2 user tokens stored in .env")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the env file containing X credentials. Default: ./.env",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check OAuth2/Auth1 credential readiness and active paths")
    subparsers.add_parser("me", help="Show the authenticated account")
    subparsers.add_parser("refresh", help="Refresh the OAuth access token")

    search = subparsers.add_parser("search", help="Search recent posts and optionally rank them as hot")
    search.add_argument("--query", required=True, help="X search query")
    search.add_argument("--sort", choices=["recent", "hot"], default="recent", help="Sort mode")
    search.add_argument("--limit", type=int, default=10, help="Returned result count after sorting")
    search.add_argument("--search-max", type=int, default=25, help="How many posts to fetch from recent search")
    search.add_argument("--skip-replies", action=argparse.BooleanOptionalAction, default=True, help="Skip replies in returned results")
    search.add_argument("--skip-mentions", action=argparse.BooleanOptionalAction, default=False, help="Skip posts that begin with @mentions")
    search.add_argument("--skip-reposts", action=argparse.BooleanOptionalAction, default=True, help="Skip reposts in returned results")

    lookup = subparsers.add_parser("lookup", help="Lookup a post by id")
    lookup.add_argument("--tweet-id", required=True, help="Target post id")

    post = subparsers.add_parser("post", help="Publish a text post or image-plus-text post")
    post.add_argument("--text", help="Post text")
    post.add_argument("--text-file", help="Path to a UTF-8 text file with the post text")
    post.add_argument("--image", help="Optional image path")
    post.add_argument(
        "--reply-settings",
        choices=["following", "mentionedUsers", "subscribers", "verified"],
        help="Restrict who can reply",
    )
    post.add_argument("--made-with-ai", action="store_true", help="Mark the post as containing AI-generated media")
    post.add_argument("--dry-run", action="store_true", help="Return the payload without posting")

    thread = subparsers.add_parser("thread", help="Publish a numbered thread from long text")
    thread.add_argument("--title", help="Optional thread title for the first segment")
    thread.add_argument("--text", help="Thread body")
    thread.add_argument("--text-file", required=True, help="Path to the thread text or markdown file")
    thread.add_argument("--image", help="Optional image for the first post in the thread")
    thread.add_argument("--max-chars", type=int, default=260, help="Max characters per thread segment")
    thread.add_argument("--made-with-ai", action="store_true", help="Mark the first post as containing AI-generated media")
    thread.add_argument("--dry-run", action="store_true", help="Preview the generated segments only")

    article = subparsers.add_parser("article", help="Reserved for native X Articles; no longer auto-converts to a thread")
    article.add_argument("--title", help="Reserved for future native Article publishing")
    article.add_argument("--text", help="Reserved for future native Article publishing")
    article.add_argument("--text-file", help="Reserved for future native Article publishing")
    article.add_argument("--image", help="Reserved for future native Article publishing")
    article.add_argument("--max-chars", type=int, default=260, help="Used only when --as-thread is explicitly set")
    article.add_argument("--made-with-ai", action="store_true", help="Used only when --as-thread is explicitly set")
    article.add_argument("--dry-run", action="store_true", help="Used only when --as-thread is explicitly set")
    article.add_argument("--as-thread", action="store_true", help="Backward-compatible escape hatch to publish as a thread explicitly")

    reply = subparsers.add_parser("reply", help="Reply to a specific post")
    reply.add_argument("--tweet-id", required=True, help="Target post id")
    reply.add_argument("--text", help="Reply text")
    reply.add_argument("--text-file", help="Path to a UTF-8 text file with the reply text")
    reply.add_argument("--dry-run", action="store_true", help="Preview without replying")

    like = subparsers.add_parser("like", help="Like a post")
    like.add_argument("--tweet-id", required=True, help="Target post id")
    like.add_argument("--dry-run", action="store_true", help="Preview without sending")

    repost = subparsers.add_parser("repost", help="Repost a post")
    repost.add_argument("--tweet-id", required=True, help="Target post id")
    repost.add_argument("--dry-run", action="store_true", help="Preview without sending")

    delete = subparsers.add_parser("delete", help="Delete a post owned by the authenticated account")
    delete.add_argument("--tweet-id", required=True, help="Target post id")
    delete.add_argument("--dry-run", action="store_true", help="Preview without sending")

    hot_reply = subparsers.add_parser("hot-reply", help="Search hot posts and engage via quote tweet or reply")
    hot_reply.add_argument("--query", required=True, help="X search query")
    hot_reply.add_argument("--limit", type=int, default=1, help="How many top posts to reply to")
    hot_reply.add_argument("--search-max", type=int, default=25, help="How many recent posts to inspect before reranking")
    hot_reply.add_argument("--min-hot-score", type=float, default=0.0, help="Minimum hot score for an eligible target")
    hot_reply.add_argument("--channel", choices=["quote", "reply"], default="quote", help="Engagement channel; quote is safer for restricted conversations")
    hot_reply.add_argument("--reply-text", help="Reply body to reuse for every target")
    hot_reply.add_argument("--reply-text-file", help="Path to a UTF-8 text file with the reply body")
    hot_reply.add_argument(
        "--reply-template",
        help="Template using placeholders such as {author_name}, {username}, {text}, {excerpt}, {url}, {topic}",
    )
    hot_reply.add_argument("--skip-self", action=argparse.BooleanOptionalAction, default=True, help="Skip posts authored by the authenticated account")
    hot_reply.add_argument("--skip-replies", action=argparse.BooleanOptionalAction, default=True, help="Skip reply posts as targets")
    hot_reply.add_argument("--skip-mentions", action=argparse.BooleanOptionalAction, default=False, help="Skip posts that begin with @mentions")
    hot_reply.add_argument("--skip-reposts", action=argparse.BooleanOptionalAction, default=True, help="Skip reposts as targets")
    hot_reply.add_argument("--dry-run", action="store_true", help="Preview targets and replies without sending")

    hot_quote = subparsers.add_parser("hot-quote", help="Search hot posts and quote tweet the top matches")
    hot_quote.add_argument("--query", required=True, help="X search query")
    hot_quote.add_argument("--limit", type=int, default=1, help="How many top posts to quote")
    hot_quote.add_argument("--search-max", type=int, default=25, help="How many recent posts to inspect before reranking")
    hot_quote.add_argument("--min-hot-score", type=float, default=0.0, help="Minimum hot score for an eligible target")
    hot_quote.add_argument("--reply-text", help="Quote text to reuse for every target")
    hot_quote.add_argument("--reply-text-file", help="Path to a UTF-8 text file with the quote text")
    hot_quote.add_argument(
        "--reply-template",
        help="Template using placeholders such as {author_name}, {username}, {text}, {excerpt}, {url}, {topic}",
    )
    hot_quote.add_argument("--skip-self", action=argparse.BooleanOptionalAction, default=True, help="Skip posts authored by the authenticated account")
    hot_quote.add_argument("--skip-replies", action=argparse.BooleanOptionalAction, default=True, help="Skip reply posts as targets")
    hot_quote.add_argument("--skip-mentions", action=argparse.BooleanOptionalAction, default=False, help="Skip posts that begin with @mentions")
    hot_quote.add_argument("--skip-reposts", action=argparse.BooleanOptionalAction, default=True, help="Skip reposts as targets")
    hot_quote.add_argument("--dry-run", action="store_true", help="Preview targets and quotes without sending")
    hot_quote.set_defaults(channel="quote")

    return parser


COMMANDS = {
    "doctor": command_doctor,
    "me": command_me,
    "refresh": command_refresh,
    "search": command_search,
    "lookup": command_lookup,
    "post": command_post,
    "thread": command_thread,
    "article": command_article,
    "reply": command_reply,
    "like": command_like,
    "repost": command_repost,
    "delete": command_delete,
    "hot-reply": command_hot_reply,
    "hot-quote": command_hot_reply,
}


def emit_json(payload: Dict[str, Any]) -> None:
    pretty = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        print(pretty)
    except UnicodeEncodeError:
        print(json.dumps(payload, ensure_ascii=True, indent=2))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    env_file = Path(args.env_file).resolve()
    store = CredentialStore(env_file)
    try:
        client = XClient(store)
        result = COMMANDS[args.command](client, args)
    except (SkillError, XApiError) as exc:
        payload = {
            "ok": False,
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
        }
        if isinstance(exc, XApiError):
            payload["error"]["status_code"] = exc.status_code
            payload["error"]["payload"] = exc.payload
        emit_json(payload)
        return 1
    emit_json({"ok": True, "result": result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
