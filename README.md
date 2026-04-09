# Labyrinth Credential Auditor

A high-performance asynchronous credential auditing and data extraction tool designed for controlled environments such as security testing labs and educational scenarios.
The tool was made for fun.. mostly a spaghetti code.

---

## Features

* Asynchronous architecture using `asyncio` and `aiohttp`
* SQLite-based result storage
* Credential testing with prefix strategies
* Duplicate attempt prevention using Bloom Filter
* Basic user data extraction via GraphQL
* Progress tracking with `rich` UI

---

## How It Works

The tool:

1. Generates usernames based on a defined pattern
2. Attempts authentication using multiple password prefixes
3. Stores successful credentials locally
4. Optionally retrieves associated user data

---

## Requirements

* Python 3.9+
* Dependencies:

  ```bash
  pip install aiohttp aiosqlite beautifulsoup4 rich
  ```

(Optional)

```bash
pip install mmh3
```

---

## Usage

```bash
python main.py
```

You will be prompted to configure:

* User ID range
* Prefix attempts
* Username format
* Concurrency mode

---

## Output

* `labyrinth.db` → SQLite database
* `results.json` → Extracted credential data

---

## Important Notice

This tool is intended strictly for:

* Educational purposes
* Authorized security testing
* Controlled lab environments

---

## Design Notes

* Uses asynchronous requests for efficiency
* Implements a Bloom Filter to reduce redundant attempts
* Modular structure for easy extension

---

## Disclaimer

See [DISCLAIMER.md](./DISCLAIMER.md)

---

## License

MIT License
