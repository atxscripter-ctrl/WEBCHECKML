"""InfoExtractor + InfoFetcher."""
from __future__ import annotations
import datetime
import os
import random
import threading
import time
from typing import Optional

from .game_conn import GameConn
from .sdp import SdpStruct

HERO_NAMES = [
    "Miya","Balmond","Saber","Alice","Nana","Tigreal","Alucard","Karina","Akai","Franco",
    "Bane","Bruno","Clint","Rafaela","Eudora","Zilong","Fanny","Layla","Minotaur","Lolita",
    "Hayabusa","Freya","Gord","Natalia","Kagura","Chou","Sun","Alpha","Ruby","Yi Sun-shin",
    "Moskov","Johnson","Cyclops","Estes","Hilda","Aurora","Lapu-Lapu","Vexana","Roger","Karrie",
    "Gatotkaca","Harley","Irithel","Grock","Argus","Odette","Lancelot","Diggie","Hylos","Zhask",
    "Helcurt","Pharsa","Lesley","Jawhead","Angela","Gusion","Valir","Martis","Uranus","Hanabi",
    "Chang'e","Kaja","Selena","Aldous","Claude","Vale","Leomord","Lunox","Hanzo","Belerick",
    "Kimmy","Thamuz","Harith","Minsitthar","Kadita","Faramis","Badang","Khufra","Granger","Guinevere",
    "Esmeralda","Terizla","X.Borg","Ling","Dyrroth","Lylia","Baxia","Masha","Wanwan","Silvanna",
    "Cecilion","Carmilla","Atlas","Popol and Kupa","Yu Zhong","Luo Yi","Benedetta","Khaleed","Barats","Brody",
    "Yve","Mathilda","Paquito","Gloo","Beatrix","Phoveus","Natan","Aulus","Aamon","Valentina",
    "Edith","Floryn","Yin","Melissa","Xavier","Julian","Fredrinn","Joy","Novaria","Arlott",
    "Ixia","Nolan","Cici","Chip","Zhuxin","Suyou","Lukas","Kalea","Zetian",
]

RANK_BANDS = [
    (0x00,0x03),(0x04,0x07),(0x08,0x0B),(0x0C,0x10),(0x11,0x15),(0x16,0x1A),
    (0x1B,0x1F),(0x20,0x24),(0x25,0x29),(0x2A,0x2E),(0x2F,0x34),(0x35,0x3A),
    (0x3B,0x40),(0x41,0x46),(0x47,0x4C),(0x4D,0x52),(0x53,0x58),(0x59,0x5E),
    (0x5F,0x64),(0x65,0x6A),(0x6B,0x70),(0x71,0x76),(0x77,0x7C),(0x7D,0x82),
    (0x83,0x88),
]
RANK_NAMES = [
    "Warrior III","Warrior II","Warrior I","Elite III","Elite II","Elite I",
    "Master IV","Master III","Master II","Master I","Grandmaster V","Grandmaster IV",
    "Grandmaster III","Grandmaster II","Grandmaster I","Epic V","Epic IV","Epic III",
    "Epic II","Epic I","Legend V","Legend IV","Legend III","Legend II","Legend I",
]

COLLECTOR_BANDS = [
    (1000,4000),(4000,10000),(10000,22000),(22000,44000),
    (44000,84000),(84000,160000),(160000,280000),(280000,0x7FFFFFFFFFFFFFFF),
]
COLLECTOR_NAMES = [
    "Amateur Collector","Junior Collector","Seasoned Collector","Expert Collector",
    "Renowned Collector","Exalted Collector","Mega Collector","World Collector",
]

SKIN_TYPES    = ["Common Skins","Exceptional Skins","Deluxe Skins","Exquisite Skins","Grand Skins","Supreme Skins"]
SKIN_TYPE_IDS = [1, 2, 3, 4, 5, 6]


def map_rank(v: int) -> str:
    for (lo, hi), name in zip(RANK_BANDS, RANK_NAMES):
        if lo <= v <= hi: return name
    base = 0x88
    if base <= v <= 0xA1:   return f"Mythic {v - base}"
    if 0xA2 <= v <= 0xBA:   return f"Mythical Honor {v - base}"
    if 0xBB <= v <= 0xEC:   return f"Mythical Glory {v - base}"
    if 0xED <= v <= 0x270F: return f"Mythical Immortal {v - base}"
    return "Unknown"


def map_collector_point(v: int) -> str:
    if v < 1000: return "No Tier"
    for i, (lo, hi) in enumerate(COLLECTOR_BANDS):
        if lo <= v < hi:
            if i == 7: return COLLECTOR_NAMES[7]
            step = (hi - lo) // 5
            tier = min(4, max(0, (v - lo) // max(1, step)))
            return f"{COLLECTOR_NAMES[i]} {['V','IV','III','II','I'][tier]}"
    return "Unknown"


def format_timestamp(secs: int) -> str:
    if secs == 0: return "Never"
    try:
        return datetime.datetime.utcfromtimestamp(secs).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "Unknown"


def _norm(v) -> str:
    if v is None: return ""
    return str(v).replace("`", "").strip()


def get_hero_history(lst) -> list[str]:
    out = []
    if not lst: return out
    for i in range(len(lst) - 1, -1, -1):
        if len(out) >= 5: break
        item = lst[i]
        if isinstance(item, (int, float)):
            hid = int(item)
            out.append(HERO_NAMES[hid - 1] if 1 <= hid <= len(HERO_NAMES) else f"Unknown({hid})")
    return out


def parse_skin_counts(skin_struct: Optional[SdpStruct]) -> dict[str, int]:
    result: dict[str, int] = {}
    if skin_struct is None: return result
    raw = skin_struct.get(4)
    if isinstance(raw, SdpStruct):
        for i, tid in enumerate(SKIN_TYPE_IDS):
            v = raw.get(tid)
            if isinstance(v, (int, float)): result[SKIN_TYPES[i]] = int(v)
    elif isinstance(raw, dict):
        for i, tid in enumerate(SKIN_TYPE_IDS):
            v = raw.get(tid) or raw.get(str(tid))
            if isinstance(v, (int, float)): result[SKIN_TYPES[i]] = int(v)
    return result


def extract(account_info: SdpStruct, role_info: SdpStruct) -> Optional[dict]:
    if account_info is None: return None
    top = account_info.get_list(0)
    if not top or not isinstance(top[0], SdpStruct): return None
    core = top[0]

    loc_obj = core.get(0x47)
    location = "NOT FOUND"
    if isinstance(loc_obj, list) and len(loc_obj) >= 2:
        location = ", ".join(str(x) for x in loc_obj)

    last_login_ts      = core.get_long(5)
    last_login_country = _norm(core.get(0x57)) or "Unknown"
    create_country     = _norm(core.get(0x61)) or "Unknown"
    squad_a = _norm(core.get(0x1F))
    squad_b = _norm(core.get(0x1E))
    squad = (squad_a + " " + squad_b).strip() if squad_b else "-"

    cur_rank_val  = core.get(0x5F)
    cur_rank      = map_rank(int(cur_rank_val)) if isinstance(cur_rank_val, (int, float)) else "Unknown"
    high_rank_val = core.get(8)
    high_rank     = map_rank(int(high_rank_val)) if isinstance(high_rank_val, (int, float)) else "Unknown"

    hero_count = role_info.get_long(9)    if role_info else 0
    matches    = role_info.get_long(0x16) if role_info else 0
    skin_info   = role_info.get_struct(0x76) if role_info else None
    skin_counts = parse_skin_counts(skin_info)

    collector       = core.get_struct(0x88)
    collector_point = collector.get_long(9)  if collector else 0
    collector_rank  = collector.get_long(10) if collector else 0
    hero_history    = get_hero_history(core.get_list(0x5B)) or ["Private / Not Available"]

    v2l_obj = account_info.get(0xD)
    v2l = "Yes" if isinstance(v2l_obj, (int, float)) and int(v2l_obj) == 1 else "No"
    skin_count_val = core.get(0x53)
    skin_count = str(skin_count_val) if skin_count_val is not None else "Unknown"

    return {
        "status": "success",
        "data": {
            "basic_info": {
                "nickname":   str(core.get(2)) if core.get(2) is not None else "Unknown",
                "player_id":  str(core.get(0)),
                "server":     str(core.get(1)),
                "level":      str(core.get(3)),
                "skin_count": skin_count,
                "hero_count": str(hero_count),
            },
            "skin_info": {"skin_breakdown": skin_counts},
            "location_info": {
                "location":               location,
                "last_login":             format_timestamp(last_login_ts),
                "last_login_country":     last_login_country,
                "create_account_country": create_country,
            },
            "game_info": {
                "current_rank":       cur_rank,
                "high_rank":          high_rank,
                "achievement_points": str(core.get_long(7)),
                "squad":              squad,
                "hero_history":       hero_history,
                "matches":            str(matches),
            },
            "collector_info": {
                "collector_point": str(collector_point),
                "collector_tier":  map_collector_point(collector_point),
                "collector_rank":  str(collector_rank),
            },
            "security_info": {"v2l_status": v2l},
        },
    }


def format_pipe(info: dict) -> str:
    if not info: return "INFOS-FAIL"
    try:
        d = info["data"]
        b, g, l, c, s = (d["basic_info"], d["game_info"], d["location_info"],
                         d["collector_info"], d["security_info"])
        sb = d["skin_info"]["skin_breakdown"]
        sb_txt = "; ".join(f"{k}:{v}" for k, v in sb.items() if v) or "N/A"
        return (
            f"V2L Status : {s['v2l_status'].lower()} | Nickname : {b['nickname']} "
            f"| Player ID : {b['player_id']} | Server : {b['server']} | Level : {b['level']} "
            f"| Skin Count : {b['skin_count']} | Hero Count : {b['hero_count']} "
            f"| Skin Breakdown : {sb_txt} | Current Rank : {g['current_rank']} "
            f"| Highest Rank : {g['high_rank']} | Achievement : {g['achievement_points']} "
            f"| Total Matches : {g['matches']} | Squad : {g['squad']} "
            f"| Recent Heroes : {', '.join(g['hero_history'])} "
            f"| Collector : {c['collector_tier']} ({c['collector_point']} pts) "
            f"| Location : {l['location']} | Last Login : {l['last_login']} "
            f"| Last Login Country : {l['last_login_country']} "
            f"| Create Account Country : {l['create_account_country']} "
            f"| Collector Rank : {c['collector_rank']}"
        )
    except Exception:
        return "INFOS-FAIL"


BUILTIN_DEVICES = [
    "and_33c9084d10d104e2e0e95288782a92ff3h0ixfjcdk5dm3i9deffa6fb-7f6c-4b46-bcc3-a52ab97c367c",
    "and_8f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0123456789abcdef01234567-0123-4567-89ab-cdef0123",
]


def load_devices_file(path: Optional[str]) -> list[str]:
    out: list[str] = []
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line and line.startswith("and_"): out.append(line)
        except Exception:
            pass
    return out


_sem = threading.Semaphore(2)
_rnd = random.Random()


def fetch(account_id: int, token: str,
          devices_file: Optional[str] = None,
          timeout_ms: int = 20000) -> Optional[dict]:
    devices = load_devices_file(devices_file) or list(BUILTIN_DEVICES)
    deadline = time.time() + timeout_ms / 1000.0
    if not _sem.acquire(timeout=timeout_ms / 1000.0): return None
    try:
        while time.time() < deadline:
            g = GameConn(_rnd.choice(devices))
            try:
                if not g.login_to_login_server():  continue
                if not g.get_game_server():        continue
                if not g.connect_to_game_server(): continue
                info = g.get_account_info(account_id, token) or g.get_account_info(account_id, token)
                if info is None:                   continue
                role = g.get_highest_level_role(info)
                if not role:                       continue
                rid, lvl = role
                detail = g.get_role_detail(rid)
                if detail is None:                 continue
                rinfo = g.get_role_info(rid, lvl)
                if rinfo is None:                  continue
                if not isinstance(rinfo.get(9),    (int, float)): continue
                if not isinstance(rinfo.get(0x16), (int, float)): continue
                res = extract(detail, rinfo)
                if res is not None: return res
            except Exception:
                pass
            finally:
                g.close()
    finally:
        _sem.release()
    return None