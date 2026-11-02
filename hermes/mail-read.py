#!/usr/bin/env python3
"""Bounded INBOX broker: read-only reads; separately approved Gmail UID MOVE to Trash."""
import argparse
import contextlib
import datetime as dt
import email.policy
import email.utils
from email.parser import BytesParser
from html.parser import HTMLParser
import fcntl
import hashlib
import imaplib
import io
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import socket
import ssl
import stat
import struct
import subprocess
import sys
import time
import tomllib
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

SOCKET_PATH = "/run/hermes-mail-read.sock"
ACCOUNTS = {"gmail", "outlook"}
MAX_REQUEST = 131072  # Bounded JSON UID lists; no commands, paths or destinations in requests.
MAX_REPLY = 2_000_000
MAX_RAW = 25 * 1024 * 1024
MAX_ATTACHMENT = 10 * 1024 * 1024
MAX_TEXT = 60000
MAX_PARTS = 200
MAX_HEADERS = 2000
PASSWORD_PATH = Path("/var/lib/hermes-mail/secrets/gmail-app-password")
TRASH_DIR = Path("/var/lib/hermes-mail/trash-plans")
MAX_TRASH_IDS = 5000
MAX_PLAN_BYTES = 32 * 1024 * 1024
MAX_TRASH_PLANS = 20
TRASH_BATCH = 50
DRAFT_SECONDS = 86400
APPROVAL_SECONDS = 3600
UTC = dt.timezone.utc


class MailError(Exception):
    pass


def clean_text(value, limit=1000, multiline=False):
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    return "".join(c for c in value if not unicodedata.category(c).startswith("C")
                   or (multiline and c in "\n\t")).strip()[:limit]


def integer(value, low, high, code="invalid-request"):
    if type(value) is not int or not low <= value <= high:
        raise MailError(code)
    return value


def uid(value):
    if type(value) is not str or not re.fullmatch(r"[1-9][0-9]{0,9}", value) or int(value) > 4294967295:
        raise MailError("invalid-message-id")
    return value


def timestamp(value):
    if type(value) is not str or len(value) > 40:
        raise MailError("invalid-timestamp")
    try:
        result = dt.datetime.fromisoformat(value)
        if result.tzinfo is None or result.year < 1970:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, OverflowError):
        raise MailError("invalid-timestamp") from None


def plan_id(value):
    if type(value) is not str or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise MailError("invalid-trash-plan-id")
    return value


def fingerprint(value):
    if type(value) is not str or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise MailError("invalid-trash-fingerprint")
    return value


def gmail_id(value):
    if (type(value) is not str or not re.fullmatch(r"[1-9][0-9]{0,19}", value)
            or int(value) > 18446744073709551615):
        raise MailError("invalid-gmail-message-identity")
    return value


def human_peer(peer):
    # The model runs as hermes. A statement in a prompt is not authorization.
    allowed = {0}
    with contextlib.suppress(KeyError):
        echo = pwd.getpwnam("echo").pw_uid
        hermes = pwd.getpwnam("hermes").pw_uid
        if echo != hermes:
            allowed.add(echo)
    if type(peer) is not int or peer not in allowed:
        raise MailError("trash-approval-requires-server-human")


def validate_request(request):
    if type(request) is not dict:
        raise MailError("invalid-request")
    operation = request.get("operation")
    operations = {"list_headers", "candidates", "read_message", "trash_plan", "trash_show",
                  "trash_approve", "trash_revoke", "trash_apply"}
    if type(operation) is not str or operation not in operations:
        raise MailError("operation-not-allowed")
    account = request.get("account")
    if type(account) is not str or account not in ACCOUNTS:
        raise MailError("invalid-request")
    if operation == "list_headers":
        keys = {"operation", "account", "page", "page_size"}
        integer(request.get("page"), 1, 100, "invalid-pagination")
        integer(request.get("page_size"), 1, 50, "invalid-pagination")
    elif operation == "read_message":
        keys = {"operation", "account", "id", "uidvalidity"}
        uid(request.get("id"))
        uid(request.get("uidvalidity"))
    elif operation.startswith("trash_"):
        keys = {"operation", "account"}
        if account != "gmail":
            raise MailError("trash-is-gmail-only")
        if operation == "trash_plan":
            keys |= {"ids", "uidvalidity"}
            uid(request.get("uidvalidity"))
            ids = request.get("ids")
            if type(ids) is not list or not 1 <= len(ids) <= MAX_TRASH_IDS:
                raise MailError("invalid-trash-id-list")
            for item in ids:
                uid(item)
            if len(set(ids)) != len(ids):
                raise MailError("duplicate-trash-ids")
        else:
            keys.add("plan")
            plan_id(request.get("plan"))
            if operation == "trash_show":
                keys |= {"page", "page_size"}
                integer(request.get("page"), 1, 100, "invalid-pagination")
                integer(request.get("page_size"), 1, 50, "invalid-pagination")
            if operation in {"trash_approve", "trash_apply", "trash_revoke"}:
                keys.add("fingerprint")
                fingerprint(request.get("fingerprint"))
            if operation == "trash_approve":
                keys.add("count")
                integer(request.get("count"), 1, MAX_TRASH_IDS)
    else:
        keys = {"operation", "account", "since", "unread_cursor", "pending"}
        timestamp(request.get("since"))
        integer(request.get("unread_cursor"), 0, 1000000)
        pending = request.get("pending")
        if type(pending) is not list or len(pending) > MAX_HEADERS:
            raise MailError("invalid-request")
        for item in pending:
            uid(item)
    if set(request) != keys:
        raise MailError("invalid-request")
    if operation != "list_headers" and account != "gmail":
        raise MailError("full-content-is-gmail-only")
    return request


def private_file(path):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise MailError("config-must-be-private-and-owned-by-hermes-mail")
        # Reject symlinked parents too; only the private runtime tree is supported.
        for parent in path.parents:
            if parent.is_symlink():
                raise MailError("config-must-be-private-and-owned-by-hermes-mail")
        return path.read_bytes()
    except FileNotFoundError:
        raise MailError("account-not-configured") from None
    except OSError:
        raise MailError("invalid-or-missing-account-config") from None


def account_config(config, account):
    try:
        data = tomllib.loads(private_file(Path(config)).decode("utf-8"))
        entry = data.get("accounts", {}).get(account)
        if not isinstance(entry, dict):
            raise MailError("account-not-configured")
        backend = entry.get("backend")
        if not isinstance(backend, dict) or backend.get("type") != "imap":
            raise MailError("only-imap-backend-is-allowed")
        return entry
    except (UnicodeError, ValueError, AttributeError):
        raise MailError("invalid-or-missing-account-config") from None


def headers_only(items):
    if not isinstance(items, list):
        raise MailError("invalid-backend-response")
    output = []
    for item in items:
        if not isinstance(item, dict) or type(item.get("id")) not in (str, int):
            raise MailError("invalid-backend-response")
        sender = item.get("from") or {}
        if not isinstance(sender, dict):
            sender = {}
        output.append({"id": clean_text(item["id"], 20),
                       "from": {"name": clean_text(sender.get("name"), 200),
                                "address": clean_text(sender.get("address", sender.get("addr")), 254)},
                       "subject": clean_text(item.get("subject"), 500),
                       "date": clean_text(item.get("date"), 100)})
    return output


def himalaya_headers(request, himalaya, config):
    account_config(config, request["account"])
    command = [himalaya, "--config", config, "--output", "json", "envelope", "list",
               "--account", request["account"], "--folder", "INBOX", "--page", str(request["page"]),
               "--page-size", str(request["page_size"])]
    environment = {k: v for k, v in os.environ.items() if k in
                   {"PATH", "HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "SSL_CERT_FILE", "NIX_SSL_CERT_FILE", "LANG", "TZ"}}
    environment.update(RUST_LOG="off", NO_COLOR="1")
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=30, env=environment, check=False)
    except subprocess.TimeoutExpired:
        raise MailError("mail-server-timeout") from None
    except OSError:
        raise MailError("mail-client-unavailable") from None
    if result.returncode:
        raise MailError("mail-connection-failed-check-account-setup")
    if len(result.stdout) > MAX_REPLY:
        raise MailError("backend-response-too-large")
    try:
        return {"ok": True, "account": request["account"], "source": "untrusted-email-headers",
                "messages": headers_only(json.loads(result.stdout))}
    except (ValueError, UnicodeError):
        raise MailError("invalid-backend-response") from None


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fragments, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.hidden += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2") and not self.hidden:
            self.fragments.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.hidden:
            self.hidden -= 1

    def handle_data(self, text):
        if not self.hidden:
            self.fragments.append(text)


def html_text(value):
    parser = HTMLText()
    parser.feed(value)
    return "".join(parser.fragments)


def decode_text(payload, charset=None):
    try:
        return payload.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def ocr_image(data):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 12_000_000
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 12_000_000:
            raise ValueError("large-image")
        image.load()
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
    command = [os.environ.get("HERMES_MAIL_TESSERACT", "tesseract"), "stdin", "stdout", "-l", "pol+eng"]
    result = subprocess.run(command, input=buffer.getvalue(), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=15, check=False,
                            env={**os.environ, "OMP_THREAD_LIMIT": "1"})
    if result.returncode or len(result.stdout) > MAX_REPLY:
        raise ValueError("ocr-failed")
    return result.stdout.decode("utf-8", errors="replace")


def extract_attachment(payload, content_type, name, charset=None):
    """No attachment is executed or written to a user-selected path. No remote fetching."""
    suffix = Path(name).suffix.lower()
    if len(payload) > MAX_ATTACHMENT:
        return {"status": "size-limit", "text": "", "truncated": True}
    deadline = time.monotonic() + 35
    try:
        truncated = False
        if content_type == "application/pdf" or suffix == ".pdf":
            import pymupdf
            with pymupdf.open(stream=payload, filetype="pdf") as document:
                if document.needs_pass:
                    return {"status": "encrypted", "text": "", "truncated": False}
                texts = []
                pages = min(len(document), 40)
                for index in range(pages):
                    if time.monotonic() > deadline:
                        truncated = True
                        break
                    page = document[index]
                    value = page.get_text("text")
                    if len(value.strip()) < 30:
                        if page.rect.width * page.rect.height * 4 > 12_000_000:
                            truncated = True
                            continue
                        value = ocr_image(page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes("png"))
                    texts.append(f"[Strona {index + 1}]\n{value}")
                    if sum(map(len, texts)) > MAX_TEXT:
                        truncated = True
                        break
                text = "\n".join(texts)
                truncated |= len(document) > pages
        elif content_type.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}:
            from PIL import Image
            with Image.open(io.BytesIO(payload)) as image:
                # OCR reads the first raster; animated GIF/multipage TIFF is partial.
                truncated = getattr(image, "n_frames", 1) > 1
            text = ocr_image(payload)
        elif suffix in {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                members = archive.infolist()
                if len(members) > 1000 or sum(m.file_size for m in members) > 30 * 1024 * 1024:
                    return {"status": "archive-limit", "text": "", "truncated": True}
                if suffix == ".docx":
                    selected = [m for m in members if re.fullmatch(r"word/(document|header[0-9]+|footer[0-9]+|footnotes|endnotes)\.xml", m.filename)]
                elif suffix == ".xlsx":
                    selected = [m for m in members if m.filename == "xl/sharedStrings.xml" or re.fullmatch(r"xl/worksheets/sheet[0-9]+\.xml", m.filename)]
                elif suffix == ".pptx":
                    selected = [m for m in members if re.fullmatch(r"ppt/(slides/slide|notesSlides/notesSlide)[0-9]+\.xml", m.filename)]
                else:
                    selected = [m for m in members if m.filename == "content.xml"]
                if suffix == ".xlsx":
                    from openpyxl import load_workbook
                    workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True, keep_links=False)
                    texts = []
                    try:
                        for sheet in workbook:
                            texts.append(f"[Arkusz {sheet.title}]")
                            for number, row in enumerate(sheet.iter_rows(values_only=True)):
                                if number >= 5000 or sum(map(len, texts)) > MAX_TEXT or time.monotonic() > deadline:
                                    truncated = True
                                    break
                                texts.append(" | ".join("" if cell is None else str(cell) for cell in row))
                    finally:
                        workbook.close()
                    selected = []
                else:
                    texts = []
                # Images/charts/embedded objects are not represented by XML text extraction.
                truncated |= any(m.filename.startswith(("word/media/", "xl/media/", "xl/charts/", "ppt/media/", "Object ")) for m in members)
                for member in selected:
                    data = archive.read(member)
                    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                        raise ValueError("unsafe-xml")
                    root = ET.fromstring(data)
                    texts.append("\n".join(t for t in root.itertext() if t.strip()))
                text = "\n".join(texts)
        elif content_type.startswith("text/") or suffix in {".txt", ".csv", ".md", ".json", ".xml", ".ics", ".log", ".html", ".htm"}:
            text = decode_text(payload, charset)
            if content_type == "text/html" or suffix in {".html", ".htm"}:
                text = html_text(text)
        else:
            # Archives, legacy Office, audio/video and executables: explicit omission, never run.
            return {"status": "unsupported-format", "text": "", "truncated": False}
        truncated |= len(text) > MAX_TEXT
        return {"status": "extracted" if text.strip() else "no-readable-text",
                "text": clean_text(text, MAX_TEXT, multiline=True), "truncated": truncated}
    except Exception:
        # Parser and OCR diagnostics may contain private content; never relay them.
        return {"status": "extraction-failed", "text": "", "truncated": False}


def message_headers(message, identifier):
    name, address = email.utils.parseaddr(str(message.get("From", "")))
    return {"id": identifier, "from": {"name": clean_text(name, 200), "address": clean_text(address, 254)},
            "subject": clean_text(str(message.get("Subject", "")), 500),
            "date": clean_text(str(message.get("Date", "")), 100),
            "message_id": clean_text(str(message.get("Message-ID", "")), 500)}


def parse_message(raw, identifier):
    message = BytesParser(policy=email.policy.default).parsebytes(raw)
    result = message_headers(message, identifier)
    body = message.get_body(preferencelist=("plain", "html"))
    body_text = ""
    if body is not None:
        data = body.get_payload(decode=True) or b""
        body_text = decode_text(data, body.get_content_charset())
        if body.get_content_type() == "text/html":
            body_text = html_text(body_text)
    attachments = []
    deadline = time.monotonic() + 65
    parts = list(message.walk())
    for index, part in enumerate(parts[:MAX_PARTS]):
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        if part.is_multipart():
            if filename is not None or disposition == "attachment":
                attachments.append({"name": clean_text(filename or f"zalacznik-{index}", 200),
                                    "content_type": part.get_content_type(), "size": len(part.as_bytes()),
                                    "inline": False, "status": "unsupported-format", "text": "", "truncated": False})
            continue
        if part is body or (filename is None and disposition != "attachment" and part.get_content_type() in {"text/plain", "text/html"}):
            continue
        payload = part.get_payload(decode=True) or b""
        name = clean_text(filename or f"zalacznik-{index}", 200)
        info = {"name": name, "content_type": clean_text(part.get_content_type(), 100),
                "size": len(payload), "inline": disposition == "inline"}
        if len(attachments) >= 20:
            info.update(status="attachment-count-limit", text="", truncated=True)
        elif time.monotonic() > deadline:
            info.update(status="extraction-time-limit", text="", truncated=True)
        else:
            info.update(extract_attachment(payload, part.get_content_type(), name, part.get_content_charset()))
        attachments.append(info)
    result.update(body=clean_text(body_text, MAX_TEXT, multiline=True),
                  body_truncated=len(body_text) > MAX_TEXT, attachments=attachments,
                  mime_parts_truncated=len(parts) > MAX_PARTS)
    # Keep the socket reply bounded even for an email with many long attachments.
    remaining = 300000
    for attachment in attachments:
        text = attachment["text"]
        attachment["text"] = text[:max(0, remaining)]
        attachment["truncated"] |= len(text) > max(0, remaining)
        remaining -= len(attachment["text"])
    return result


class Gmail:
    def __init__(self, config):
        self.config = config
        self.connection = None

    def __enter__(self):
        entry = account_config(self.config, "gmail")
        backend = entry["backend"]
        encryption, auth = backend.get("encryption", {}), backend.get("auth", {})
        login = backend.get("login")
        if (backend.get("host") != "imap.gmail.com" or type(backend.get("port")) is not int
                or backend["port"] != 993 or not isinstance(encryption, dict)
                or encryption.get("type") != "tls" or not isinstance(auth, dict)
                or auth.get("type") != "password" or type(login) is not str
                or len(login) > 254 or not re.fullmatch(r"[^\s\x00-\x1f]+@[^\s\x00-\x1f]+", login)):
            raise MailError("only-fixed-gmail-tls-password-backend-is-allowed")
        self.login_name = login.lower()
        password = private_file(PASSWORD_PATH).decode("ascii").strip()
        if not re.fullmatch(r"[A-Za-z0-9]{16}", password):
            raise MailError("invalid-gmail-password-file")
        try:
            self.connection = imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ssl.create_default_context(), timeout=25)
            self.connection.login(login, password)
            status, _ = self.connection.select("INBOX", readonly=True)  # IMAP EXAMINE, not SELECT.
            if status != "OK":
                raise MailError("inbox-unavailable")
            validity = self.connection.response("UIDVALIDITY")[1]
            self.uidvalidity = uid(validity[0].decode("ascii"))
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise
        finally:
            password = None

    def __exit__(self, *unused):
        if self.connection:
            with contextlib.suppress(Exception):
                self.connection.logout()  # Never CLOSE/EXPUNGE/STORE.

    def search(self, *criteria):
        status, data = self.connection.uid("SEARCH", None, *criteria)
        if status != "OK" or not data or len(data[0] or b"") > 600000:
            raise MailError("mailbox-search-failed-or-too-large")
        return [uid(value.decode("ascii")) for value in (data[0] or b"").split()]

    def headers(self, identifiers, with_identity=False, deadline=None):
        output = {}
        extra = " X-GM-MSGID" if with_identity else ""
        fields = f"(UID FLAGS INTERNALDATE RFC822.SIZE{extra} BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])"
        for start in range(0, len(identifiers), 50):
            if deadline is not None and time.monotonic() >= deadline:
                raise MailError("trash-plan-preparation-time-limit")
            selected = identifiers[start:start + 50]
            status, data = self.connection.uid("FETCH", ",".join(selected), fields)
            if status != "OK":
                raise MailError("header-read-failed")
            for item in data:
                if not isinstance(item, tuple) or len(item) != 2:
                    continue
                metadata, raw = item
                if not isinstance(raw, bytes) or len(raw) > 65536:
                    raise MailError("invalid-backend-response")
                identifier = re.search(rb"\bUID ([0-9]+)", metadata)
                date = re.search(rb'\bINTERNALDATE "([^"]+)"', metadata)
                size = re.search(rb"\bRFC822.SIZE ([0-9]+)", metadata)
                flags = re.search(rb"\bFLAGS \(([^)]*)\)", metadata)
                if not identifier or not date or not size or not flags:
                    raise MailError("invalid-backend-response")
                identifier = uid(identifier[1].decode("ascii"))
                if identifier not in selected or identifier in output:
                    raise MailError("invalid-backend-response")
                internal = dt.datetime.strptime(date[1].decode("ascii").strip(), "%d-%b-%Y %H:%M:%S %z").astimezone(UTC)
                parsed = BytesParser(policy=email.policy.default).parsebytes(raw, headersonly=True)
                header = message_headers(parsed, identifier)
                flag_set = flags[1].split()
                header.update(internal_date=internal.isoformat(), unread=b"\\Seen" not in flag_set,
                              answered=b"\\Answered" in flag_set, size=int(size[1]))
                if with_identity:
                    identity = re.search(rb"\bX-GM-MSGID ([0-9]+)", metadata)
                    if not identity:
                        raise MailError("gmail-message-identity-unavailable")
                    header["gmail_message_id"] = gmail_id(identity[1].decode("ascii"))
                output[identifier] = header
        return [output[item] for item in identifiers if item in output]

    def list_headers(self, request):
        identifiers = sorted(self.search("ALL"), key=int, reverse=True)
        start = (request["page"] - 1) * request["page_size"]
        messages = self.headers(identifiers[start:start + request["page_size"]])
        return {"ok": True, "account": "gmail", "source": "untrusted-email-headers", "id_kind": "imap-uid",
                "uidvalidity": self.uidvalidity, "messages": messages}

    def candidates(self, request):
        since = timestamp(request["since"])
        # IMAP SINCE is a calendar-day comparison; overlap avoids timezone-boundary loss.
        day = (since - dt.timedelta(days=2)).strftime("%d-%b-%Y")
        recent_ids = self.search("SINCE", day)
        if len(recent_ids) > MAX_HEADERS:
            raise MailError("candidate-window-too-large")
        recent = [m for m in self.headers(recent_ids) if timestamp(m["internal_date"]) > since]
        unread_ids = sorted(self.search("UNSEEN"), key=int, reverse=True)
        cursor = request["unread_cursor"] % len(unread_ids) if unread_ids else 0
        old_selection = (unread_ids[cursor:] + unread_ids[:cursor])[:500]
        wanted = list(dict.fromkeys([m["id"] for m in recent] + request["pending"] + old_selection))
        if len(wanted) > MAX_HEADERS:
            raise MailError("pending-window-too-large")
        messages = self.headers(wanted)
        new_ids = {m["id"] for m in recent} | set(request["pending"])
        for message in messages:
            message["new"] = message["id"] in new_ids
        return {"ok": True, "account": "gmail", "source": "untrusted-email-headers", "id_kind": "imap-uid",
                "uidvalidity": self.uidvalidity, "messages": messages, "new_count": len(recent),
                "unread_count": len(unread_ids), "unread_cursor": cursor,
                "unread_window_limited": len(unread_ids) > len(old_selection)}

    def read_message(self, request):
        if request["uidvalidity"] != self.uidvalidity:
            raise MailError("mailbox-identity-changed")
        headers = self.headers([request["id"]])
        if not headers:
            raise MailError("message-not-found")
        result = headers[0]
        if result["size"] > MAX_RAW:
            result.update(body="", body_truncated=True, attachments=[], mime_parts_truncated=False,
                          content_status="message-size-limit")
        else:
            status, data = self.connection.uid("FETCH", request["id"], "(UID BODY.PEEK[])")
            raw = [item[1] for item in (data or []) if isinstance(item, tuple) and isinstance(item[1], bytes)]
            if status != "OK" or len(raw) != 1:
                raise MailError("message-read-failed")
            if len(raw[0]) > MAX_RAW:
                raise MailError("message-too-large")
            result.update(parse_message(raw[0], request["id"]))
            result["content_status"] = "read"
            after = self.headers([request["id"]])
            if after and after[0]["unread"] != headers[0]["unread"]:
                # A concurrent user may change flags; never attempt to "restore" them.
                result["concurrent_flag_change"] = True
        return {"ok": True, "account": "gmail", "source": "untrusted-email-content",
                "uidvalidity": self.uidvalidity, "message": result}


def trash_capabilities(mailbox):
    # IMAP clients cache pre-login capabilities; refresh after authentication.
    status, data = mailbox.connection.capability()
    if (status != "OK" or not data or any(not isinstance(v, bytes) for v in data)
            or sum(map(len, data)) > 65536):
        raise MailError("gmail-safe-move-not-supported")
    capabilities = {value.upper() for line in data for value in line.split()}
    if not {b"MOVE", b"X-GM-EXT-1"} <= capabilities:
        raise MailError("gmail-safe-move-not-supported")


def trash_destination(mailbox):
    # Destination is server-discovered SPECIAL-USE Trash, never caller/mail data.
    status, data = mailbox.connection.list()
    if status != "OK":
        raise MailError("gmail-trash-folder-unavailable")
    folders = []
    pattern = rb'\(([^\r\n()]*)\) (?:NIL|"(?:[^"\\\r\n]|\\["\\])*") ("(?:[^"\\\r\n]|\\["\\])*"|[^\s()"{}]+)'
    for line in data or []:
        if not isinstance(line, bytes):
            raise MailError("invalid-gmail-folder-response")
        match = re.fullmatch(pattern, line)
        if not match:
            raise MailError("invalid-gmail-folder-response")
        if b"\\TRASH" in {f.upper() for f in match[1].split()}:
            token = match[2]
            if (any(c < 32 or c >= 127 for c in token)
                    or token.strip(b'"').upper() in {b"NIL", b"INBOX"}):
                raise MailError("invalid-gmail-folder-response")
            folders.append(token)
    if len(folders) != 1:
        raise MailError("gmail-trash-folder-is-not-unique")
    return folders[0]


def trash_select(mailbox, folder, readonly, validity=None):
    status, _ = mailbox.connection.select(folder, readonly=readonly)
    if status != "OK":
        raise MailError("gmail-trash-mailbox-unavailable")
    if validity is not None:
        data = mailbox.connection.response("UIDVALIDITY")[1]
        current = uid(data[0].decode("ascii"))
        if current != validity:
            raise MailError("mailbox-identity-changed")


def trash_identities(mailbox, identifiers):
    if not identifiers:
        return {}
    if len(identifiers) > TRASH_BATCH:
        raise MailError("invalid-trash-batch")
    status, data = mailbox.connection.uid("FETCH", ",".join(identifiers), "(UID X-GM-MSGID FLAGS)")
    if status != "OK":
        raise MailError("gmail-message-identity-unavailable")
    result = {}
    for item in data or []:
        if item is None:
            continue
        if not isinstance(item, bytes):
            raise MailError("invalid-backend-response")
        identifier = re.search(rb"\bUID ([0-9]+)", item)
        identity = re.search(rb"\bX-GM-MSGID ([0-9]+)", item)
        flags = re.search(rb"\bFLAGS \(([^)]*)\)", item)
        if not identifier or not identity or not flags:
            raise MailError("invalid-backend-response")
        identifier = uid(identifier[1].decode("ascii"))
        if identifier not in identifiers or identifier in result:
            raise MailError("invalid-backend-response")
        result[identifier] = {"gmail_message_id": gmail_id(identity[1].decode("ascii")),
                              "unread": b"\\Seen" not in flags[1].split()}
    return result


def trash_membership(mailbox, destination, identities):
    # Read-only reconciliation is limited to the known Gmail message identities.
    trash_select(mailbox, destination, True)
    wanted = list(dict.fromkeys(identities))
    if not wanted or len(wanted) > TRASH_BATCH:
        raise MailError("invalid-trash-batch")
    clauses = ["X-GM-MSGID " + gmail_id(value) for value in wanted]
    criteria = "OR " * (len(clauses) - 1) + " ".join(clauses)
    identifiers = mailbox.search(criteria)
    if len(identifiers) > len(wanted) or len(set(identifiers)) != len(identifiers):
        raise MailError("invalid-backend-response")
    found = trash_identities(mailbox, identifiers)
    by_identity = {}
    for record in found.values():
        identity = record["gmail_message_id"]
        if identity not in wanted or identity in by_identity:
            raise MailError("invalid-backend-response")
        by_identity[identity] = record
    return by_identity


class TrashPlans:
    """Broker-private manifests. Only authenticated OS echo/root can authorize."""
    def __init__(self, directory=None, clock=None):
        self.directory = Path(directory) if directory is not None else TRASH_DIR
        self.clock = clock or (lambda: dt.datetime.now(UTC))
        try:
            info = self.directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or any(p.is_symlink() for p in self.directory.parents)):
                raise MailError("trash-state-must-be-private")
        except OSError:
            raise MailError("trash-state-directory-not-ready") from None

    @staticmethod
    def digest(manifest):
        return hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()

    @contextlib.contextmanager
    def locked(self, identifier, creating=False):
        if identifier != "new-plan":
            plan_id(identifier)
            if not creating and not (self.directory / (identifier + ".json")).exists():
                raise MailError("trash-plan-not-found")
        descriptor = os.open(self.directory / (identifier + ".lock"),
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise MailError("trash-state-must-be-private")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise MailError("trash-plan-busy") from None
            yield
        finally:
            os.close(descriptor)

    def save(self, plan):
        encoded = json.dumps(plan, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_PLAN_BYTES:
            raise MailError("trash-plan-size-limit")
        target = self.directory / (plan_id(plan["plan"]) + ".json")
        temporary = self.directory / ("." + secrets.token_hex(16) + ".new")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    def load(self, identifier):
        identifier = plan_id(identifier)
        path = self.directory / (identifier + ".json")
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or info.st_mode & 0o077 or info.st_size > MAX_PLAN_BYTES):
                    raise MailError("invalid-trash-plan-file")
                plan = json.loads(source.read(MAX_PLAN_BYTES + 1))
            self.validate(plan, identifier)
            return plan
        except FileNotFoundError:
            raise MailError("trash-plan-not-found") from None
        except (OSError, ValueError, UnicodeError, KeyError, TypeError, AttributeError):
            raise MailError("invalid-trash-plan-file") from None

    def validate(self, plan, identifier):
        if (type(plan) is not dict or set(plan) != {"version", "plan", "manifest", "fingerprint", "state"}
                or type(plan["version"]) is not int or plan["version"] != 1 or plan["plan"] != identifier):
            raise MailError("invalid-trash-plan-file")
        manifest, state = plan["manifest"], plan["state"]
        if (type(manifest) is not dict or set(manifest) != {"account", "mailbox_login", "uidvalidity", "created_at", "messages"}
                or manifest["account"] != "gmail" or type(manifest["mailbox_login"]) is not str
                or not re.fullmatch(r"[^\s\x00-\x1f]+@[^\s\x00-\x1f]+", manifest["mailbox_login"])
                or len(manifest["mailbox_login"]) > 254 or type(manifest["messages"]) is not list
                or not 1 <= len(manifest["messages"]) <= MAX_TRASH_IDS):
            raise MailError("invalid-trash-plan-file")
        uid(manifest["uidvalidity"])
        timestamp(manifest["created_at"])
        ids, identities = set(), set()
        header_keys = {"id", "from", "subject", "date", "message_id", "internal_date", "unread",
                       "answered", "size", "gmail_message_id"}
        for record in manifest["messages"]:
            if type(record) is not dict or set(record) != header_keys:
                raise MailError("invalid-trash-plan-file")
            identifier = uid(record["id"])
            identity = gmail_id(record["gmail_message_id"])
            if identifier in ids or identity in identities:
                raise MailError("invalid-trash-plan-file")
            ids.add(identifier)
            identities.add(identity)
            if (type(record["from"]) is not dict or set(record["from"]) != {"name", "address"}
                    or any(type(record["from"][k]) is not str or len(record["from"][k]) > limit
                           for k, limit in (("name", 200), ("address", 254)))
                    or any(type(record[k]) is not str or len(record[k]) > limit
                           for k, limit in (("subject", 500), ("date", 100), ("message_id", 500)))
                    or type(record["unread"]) is not bool or type(record["answered"]) is not bool):
                raise MailError("invalid-trash-plan-file")
            timestamp(record["internal_date"])
            integer(record["size"], 0, 2**63 - 1)
        fingerprint(plan["fingerprint"])
        if plan["fingerprint"] != self.digest(manifest):
            raise MailError("trash-plan-fingerprint-mismatch")
        state_keys = {"status", "approved_by", "approved_at", "approved_fingerprint", "approved_count",
                      "expires_at", "in_flight", "before", "outcomes"}
        if (type(state) is not dict or set(state) != state_keys
                or state["status"] not in {"draft", "approved", "revoked", "completed"}
                or type(state["in_flight"]) is not list or len(state["in_flight"]) > TRASH_BATCH
                or len(set(state["in_flight"])) != len(state["in_flight"])
                or not set(state["in_flight"]) <= ids
                or type(state["outcomes"]) is not dict or not set(state["outcomes"]) <= ids
                or set(state["outcomes"]) & set(state["in_flight"])
                or type(state["before"]) is not dict or set(state["before"]) != set(state["in_flight"])
                or any(type(v) is not bool for v in state["before"].values())):
            raise MailError("invalid-trash-plan-file")
        for outcome in state["outcomes"].values():
            if (type(outcome) is not dict or set(outcome) != {"result", "flag_change_observed"}
                    or outcome["result"] not in {"moved", "absent-before-move"}
                    or type(outcome["flag_change_observed"]) is not bool):
                raise MailError("invalid-trash-plan-file")
        if state["approved_by"] is None:
            if (state["status"] not in {"draft", "revoked"} or state["approved_at"] is not None
                    or state["approved_fingerprint"] is not None or state["approved_count"] is not None
                    or state["expires_at"] is not None or state["in_flight"] or state["outcomes"]):
                raise MailError("invalid-trash-plan-file")
        else:
            human_peer(state["approved_by"])
            approved, expires = timestamp(state["approved_at"]), timestamp(state["expires_at"])
            if (expires != approved + dt.timedelta(seconds=APPROVAL_SECONDS) or state["status"] == "draft"
                    or approved < timestamp(manifest["created_at"])
                    or state["approved_fingerprint"] != plan["fingerprint"]
                    or type(state["approved_count"]) is not int or state["approved_count"] != len(ids)):
                raise MailError("invalid-trash-plan-file")
        if state["status"] == "completed" and (state["in_flight"] or len(state["outcomes"]) != len(ids)):
            raise MailError("invalid-trash-plan-file")

    def summary(self, plan):
        state = plan["state"]
        moved = sum(v["result"] == "moved" for v in state["outcomes"].values())
        absent = sum(v["result"] == "absent-before-move" for v in state["outcomes"].values())
        count = len(plan["manifest"]["messages"])
        expired = state["expires_at"] is not None and timestamp(state["expires_at"]) <= self.clock()
        return {"ok": True, "account": "gmail", "plan": plan["plan"], "fingerprint": plan["fingerprint"],
                "uidvalidity": plan["manifest"]["uidvalidity"], "mailbox": plan["manifest"]["mailbox_login"],
                "count": count, "status": state["status"],
                "destination": "Gmail SPECIAL-USE Trash", "moved": moved, "absent_before_move": absent,
                "remaining": count - moved - absent, "in_flight": len(state["in_flight"]),
                "flag_changes_observed": sum(v["flag_change_observed"] for v in state["outcomes"].values()),
                "approved_by": state["approved_by"], "expires_at": state["expires_at"], "expired": expired,
                "needs_more": state["status"] == "approved" and not expired and count > moved + absent}

    def check_digest(self, plan, request):
        if request["fingerprint"] != plan["fingerprint"]:
            raise MailError("trash-plan-fingerprint-mismatch")

    def create(self, request, config):
        with self.locked("new-plan"):
            # Bound disk use without silently deleting audit records. Human revocation
            # releases the pending-plan quota; completed/revoked files are retained.
            paths = list(self.directory.glob("*.json"))
            if len(paths) >= 200 or sum(p.lstat().st_size for p in self.directory.iterdir()) >= 128 * 1024 * 1024:
                raise MailError("trash-plan-storage-limit")
            active = sum(self.load(p.stem)["state"]["status"] in {"draft", "approved"} for p in paths)
            if active >= MAX_TRASH_PLANS:
                raise MailError("too-many-pending-trash-plans")
            with Gmail(config) as mailbox:
                if request["uidvalidity"] != mailbox.uidvalidity:
                    raise MailError("mailbox-identity-changed")
                trash_capabilities(mailbox)
                trash_destination(mailbox)
                ids = sorted(request["ids"], key=int)
                messages = mailbox.headers(ids, with_identity=True, deadline=time.monotonic() + 75)
                if len(messages) != len(ids) or {m["id"] for m in messages} != set(ids):
                    raise MailError("trash-plan-target-missing")
            identifier = secrets.token_hex(16)
            manifest = {"account": "gmail", "mailbox_login": mailbox.login_name, "uidvalidity": request["uidvalidity"],
                        "created_at": self.clock().isoformat(), "messages": messages}
            plan = {"version": 1, "plan": identifier, "manifest": manifest, "fingerprint": self.digest(manifest),
                    "state": {"status": "draft", "approved_by": None, "approved_at": None,
                              "approved_fingerprint": None, "approved_count": None, "expires_at": None,
                              "in_flight": [], "before": {}, "outcomes": {}}}
            self.validate(plan, identifier)
            with self.locked(identifier, creating=True):
                if (self.directory / (identifier + ".json")).exists():
                    raise MailError("trash-plan-id-collision")
                self.save(plan)
            return self.summary(plan)

    def manage(self, request, peer):
        operation = request["operation"]
        if operation in {"trash_approve", "trash_revoke"}:
            human_peer(peer)
        with self.locked(request["plan"]):
            plan = self.load(request["plan"])
            if operation == "trash_show":
                result = self.summary(plan)
                start = (request["page"] - 1) * request["page_size"]
                result.update(source="untrusted-email-headers", page=request["page"],
                              messages=plan["manifest"]["messages"][start:start + request["page_size"]])
                return result
            self.check_digest(plan, request)
            state = plan["state"]
            if operation == "trash_revoke":
                if state["status"] != "completed":
                    state["status"] = "revoked"
                    self.save(plan)
            elif operation == "trash_approve":
                if request["count"] != len(plan["manifest"]["messages"]):
                    raise MailError("trash-plan-count-mismatch")
                if state["status"] in {"revoked", "completed"}:
                    raise MailError("trash-plan-is-closed")
                now = self.clock()
                created = timestamp(plan["manifest"]["created_at"])
                if (created > now or (state["approved_by"] is None
                        and now - created >= dt.timedelta(seconds=DRAFT_SECONDS))):
                    raise MailError("trash-plan-too-old-create-a-new-plan")
                state.update(status="approved", approved_by=peer, approved_at=now.isoformat(),
                             approved_fingerprint=plan["fingerprint"], approved_count=request["count"],
                             expires_at=(now + dt.timedelta(seconds=APPROVAL_SECONDS)).isoformat())
                self.save(plan)
            return self.summary(plan)

    def require_approval(self, plan):
        state = plan["state"]
        if state["status"] != "approved":
            raise MailError("trash-plan-not-approved")
        human_peer(state["approved_by"])
        now = self.clock()
        if now < timestamp(state["approved_at"]):
            raise MailError("trash-approval-clock-invalid")
        if now >= timestamp(state["expires_at"]):
            raise MailError("trash-approval-expired")

    def check_identities(self, present, expected):
        for identifier, record in present.items():
            if identifier not in expected or record["gmail_message_id"] != expected[identifier]["gmail_message_id"]:
                raise MailError("trash-target-identity-mismatch")

    def reconcile(self, plan, mailbox, destination, expected):
        state = plan["state"]
        ids = list(state["in_flight"])
        if not ids:
            return
        trash_select(mailbox, "INBOX", True, plan["manifest"]["uidvalidity"])
        present = trash_identities(mailbox, ids)
        self.check_identities(present, expected)
        found = trash_membership(mailbox, destination, [expected[v]["gmail_message_id"] for v in ids])
        unverified = False
        for identifier in ids:
            record = found.get(expected[identifier]["gmail_message_id"])
            if record is not None and identifier not in present:
                state["outcomes"][identifier] = {"result": "moved",
                    "flag_change_observed": record["unread"] != state["before"][identifier]}
                state["in_flight"].remove(identifier)
                del state["before"][identifier]
            elif record is not None or identifier not in present:
                # Do not label absent mail 'deleted', or move any substitute UID.
                unverified = True
        self.save(plan)
        if unverified:
            raise MailError("trash-result-unverified-retry-same-plan")

    def apply(self, request, config):
        with self.locked(request["plan"]):
            plan = self.load(request["plan"])
            self.check_digest(plan, request)
            state = plan["state"]
            if state["status"] == "completed":
                return self.summary(plan)
            self.require_approval(plan)
            expected = {m["id"]: m for m in plan["manifest"]["messages"]}
            with Gmail(config) as mailbox:
                if (mailbox.uidvalidity != plan["manifest"]["uidvalidity"]
                        or mailbox.login_name != plan["manifest"]["mailbox_login"]):
                    raise MailError("mailbox-identity-changed")
                trash_capabilities(mailbox)
                destination = trash_destination(mailbox)
                self.reconcile(plan, mailbox, destination, expected)
                pending = state["in_flight"] or [v for v in expected if v not in state["outcomes"]][:TRASH_BATCH]
                if pending:
                    self.require_approval(plan)  # Again immediately before writable selection.
                    trash_select(mailbox, "INBOX", False, plan["manifest"]["uidvalidity"])
                    present = trash_identities(mailbox, pending)
                    self.check_identities(present, expected)
                    for identifier in pending:
                        if identifier not in present:
                            state["outcomes"][identifier] = {"result": "absent-before-move", "flag_change_observed": False}
                    state["in_flight"] = [v for v in pending if v in present]
                    state["before"] = {v: present[v]["unread"] for v in state["in_flight"]}
                    self.save(plan)  # Durable intent before any mailbox mutation.
                    if state["in_flight"]:
                        self.require_approval(plan)  # Expiry during preflight must not authorize a MOVE.
                        try:
                            status, _ = mailbox.connection.uid("MOVE", ",".join(state["in_flight"]), destination)
                            if status != "OK":
                                raise MailError("trash-move-response-not-ok")
                            self.reconcile(plan, mailbox, destination, expected)
                        except Exception:
                            # Original IDs/intention remain durable. Never fall back to
                            # COPY+STORE/EXPUNGE, broad filters, another UID or another folder.
                            raise MailError("trash-result-unverified-retry-same-plan") from None
                if not state["in_flight"] and len(state["outcomes"]) == len(expected):
                    state["status"] = "completed"
                    self.save(plan)
            return self.summary(plan)


def dispatch(request, himalaya, config, peer=None):
    validate_request(request)
    operation = request["operation"]
    # Fail before state/backend access when the model tries to approve itself.
    if operation in {"trash_approve", "trash_revoke"}:
        human_peer(peer)
    if request["account"] == "outlook":
        return himalaya_headers(request, himalaya, config)
    try:
        if operation.startswith("trash_"):
            store = TrashPlans()
            if operation == "trash_plan":
                return store.create(request, config)
            if operation == "trash_apply":
                return store.apply(request, config)
            return store.manage(request, peer)
        with Gmail(config) as mailbox:
            return getattr(mailbox, operation)(request)
    except MailError:
        raise
    except (socket.timeout, TimeoutError):
        raise MailError("mail-server-timeout") from None
    except Exception:
        raise MailError("mail-connection-or-read-failed") from None


def receive_line(connection, maximum):
    data = bytearray()
    while b"\n" not in data:
        packet = connection.recv(min(65536, maximum + 1 - len(data)))
        if not packet:
            raise MailError("incomplete-response-or-request")
        data.extend(packet)
        if len(data) > maximum:
            raise MailError("message-too-large")
    line, extra = data.split(b"\n", 1)
    if extra:
        raise MailError("only-one-request-is-allowed")
    return bytes(line)


@contextlib.contextmanager
def quiet_backend():
    # Native PDF decoders may write directly to C stdout/stderr. Never leak their
    # diagnostics to the socket or journal; FD0 remains the accepted connection.
    saved = [os.dup(1), os.dup(2)]
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 1)
        os.dup2(null, 2)
        yield
    finally:
        # Flush Python AND libc while the FDs still point at /dev/null.
        # Otherwise buffered decoder diagnostics could surface after restoring FD1.
        with contextlib.suppress(Exception):
            sys.stdout.flush()
            sys.stderr.flush()
            import ctypes
            libc = ctypes.CDLL(None)
            libc.fflush.argtypes = [ctypes.c_void_p]
            libc.fflush(None)
        for target, original in zip((1, 2), saved):
            os.dup2(original, target)
            os.close(original)
        os.close(null)


def serve(himalaya, config):
    connection = socket.socket(fileno=0)
    connection.settimeout(120)
    try:
        _, peer, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        allowed = {0}
        for name in ("hermes", "echo"):
            with contextlib.suppress(KeyError):
                allowed.add(pwd.getpwnam(name).pw_uid)
        if peer not in allowed:
            raise MailError("access-denied")
        request = json.loads(receive_line(connection, MAX_REQUEST))
        with quiet_backend():
            response = dispatch(request, himalaya, config, peer=peer)
    except MailError as error:
        response = {"ok": False, "error": str(error)}
    except (ValueError, OSError, UnicodeError):
        response = {"ok": False, "error": "invalid-request-or-connection"}
    except Exception:
        response = {"ok": False, "error": "mail-broker-failed"}
    encoded = json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(encoded) > MAX_REPLY:
        encoded = b'{"ok":false,"error":"response-too-large"}\n'
    with contextlib.suppress(OSError):
        connection.sendall(encoded)
    connection.close()


def client(request, socket_path=SOCKET_PATH):
    validate_request(request)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(125)
            connection.connect(socket_path)
            # A local unprivileged socket replacement must not receive report requests.
            _, owner, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if owner not in {0, pwd.getpwnam("hermes-mail").pw_uid}:
                raise MailError("invalid-broker-owner")
            packet = json.dumps(request).encode("utf-8") + b"\n"
            if len(packet) > MAX_REQUEST:
                raise MailError("message-too-large")
            connection.sendall(packet)
            response = json.loads(receive_line(connection, MAX_REPLY))
            if type(response) is not dict or type(response.get("ok")) is not bool:
                raise MailError("invalid-broker-response")
            return response
    except MailError:
        raise
    except Exception:
        raise MailError("broker-unavailable-or-invalid-response") from None


def read_ids_file(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REQUEST:
                raise MailError("invalid-trash-id-file")
            value = json.loads(source.read(MAX_REQUEST + 1))
            if type(value) is not list:
                raise MailError("invalid-trash-id-file")
            return value
    except (OSError, ValueError, UnicodeError):
        raise MailError("invalid-trash-id-file") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--himalaya", default="himalaya")
    parser.add_argument("--config", default="/var/lib/hermes-mail/.config/himalaya/config.toml")
    sub = parser.add_subparsers(dest="command")
    listing = sub.add_parser("list")
    listing.add_argument("account", choices=sorted(ACCOUNTS))
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--page", type=int, default=1)
    listing.add_argument("--count-only", action="store_true")
    reading = sub.add_parser("read", help="Gmail IMAP UID from list/scan, never a Himalaya alias")
    reading.add_argument("account", choices=["gmail"])
    reading.add_argument("id")
    reading.add_argument("--uidvalidity", required=True)
    scanning = sub.add_parser("scan")
    scanning.add_argument("account", choices=["gmail"])
    scanning.add_argument("--since", required=True)
    scanning.add_argument("--unread-cursor", type=int, default=0)
    planning = sub.add_parser("trash-plan", help="Draft only; JSON array of exact INBOX UID strings")
    planning.add_argument("account", choices=["gmail"])
    planning.add_argument("--ids-file", required=True)
    planning.add_argument("--uidvalidity", required=True)
    showing = sub.add_parser("trash-show", help="Read-only manifest preview; details only in DM/private channel")
    showing.add_argument("account", choices=["gmail"])
    showing.add_argument("plan")
    showing.add_argument("--page", type=int, default=1)
    showing.add_argument("--limit", type=int, default=50)
    approving = sub.add_parser("trash-approve", help="Only OS echo/root, not Hermes; authorize one fingerprint for 1 hour")
    approving.add_argument("account", choices=["gmail"])
    approving.add_argument("plan")
    approving.add_argument("--fingerprint", required=True)
    approving.add_argument("--count", type=int, required=True)
    revoking = sub.add_parser("trash-revoke", help="Only OS echo/root; stop further batches, not undo past moves")
    revoking.add_argument("account", choices=["gmail"])
    revoking.add_argument("plan")
    revoking.add_argument("--fingerprint", required=True)
    applying = sub.add_parser("trash-apply", help="Only preapproved plan; 50 UIDs per broker call; never permanent delete")
    applying.add_argument("account", choices=["gmail"])
    applying.add_argument("plan")
    applying.add_argument("--fingerprint", required=True)
    applying.add_argument("--all-approved", action="store_true", help="Sequential batches of this same approved plan")
    args = parser.parse_args()
    if args.serve:
        serve(args.himalaya, args.config)
        return
    try:
        if args.command == "list":
            request = {"operation": "list_headers", "account": args.account, "page": args.page, "page_size": args.limit}
        elif args.command == "read":
            request = {"operation": "read_message", "account": "gmail", "id": args.id, "uidvalidity": args.uidvalidity}
        elif args.command == "scan":
            request = {"operation": "candidates", "account": "gmail", "since": args.since,
                       "unread_cursor": args.unread_cursor, "pending": []}
        elif args.command and args.command.startswith("trash-"):
            operation = args.command.replace("-", "_")
            request = {"operation": operation, "account": "gmail"}
            if args.command == "trash-plan":
                request.update(ids=read_ids_file(args.ids_file), uidvalidity=args.uidvalidity)
            else:
                request["plan"] = args.plan
                if args.command == "trash-show":
                    request.update(page=args.page, page_size=args.limit)
                else:
                    request["fingerprint"] = args.fingerprint
                if args.command == "trash-approve":
                    request["count"] = args.count
        else:
            parser.error("choose a mailbox command; see --help")
        result = client(request)
        if args.command == "trash-apply" and args.all_approved:
            # Never accept an expanded target set, substituted plan, or silent
            # fallback. Stop on any broker error or non-decreasing remainder.
            previous = MAX_TRASH_IDS + 1
            for _ in range(MAX_TRASH_IDS + 1):
                if not result.get("ok"):
                    break
                remaining = integer(result.get("remaining"), 0, MAX_TRASH_IDS, "invalid-broker-response")
                if (result.get("plan") != args.plan or result.get("fingerprint") != args.fingerprint
                        or type(result.get("needs_more")) is not bool or remaining >= previous):
                    raise MailError("invalid-broker-response-or-no-trash-progress")
                if not result["needs_more"]:
                    break
                previous = remaining
                result = client(request)
            else:
                raise MailError("trash-batch-limit")
        if args.command == "list" and args.count_only and result.get("ok"):
            result = {"ok": True, "account": args.account, "count": len(result["messages"])}
    except MailError as error:
        result = {"ok": False, "error": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
