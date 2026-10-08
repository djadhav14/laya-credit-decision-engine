# Installation — step by step

Tested on Linux (Ubuntu 22.04/24.04) and macOS. Windows works via the PowerShell variants noted below.

## 0. Prerequisites

| Need | Version | Check |
|---|---|---|
| Python | 3.10 or newer (tested up to 3.14) | `python3 --version` |
| PostgreSQL + pgvector | PostgreSQL 16, pgvector ≥ 0.6 | via Docker (recommended) or native |
| Docker (optional) | any recent | `docker --version` |
| Disk / RAM | ~3 GB for torch + one checkpoint (~0.8 GB); 4 GB RAM minimum | |
| Internet | **first run only**, to download Python packages and the Laya checkpoint | |

## 1. Get the code

```bash
git clone https://github.com/<your-account>/laya-credit-decision-engine.git
cd laya-credit-decision-engine
```

## 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate              # Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

## 3. Install PyTorch (choose ONE)

Laya runs on PyTorch. Installing torch first lets you pick the build; otherwise pip may pull a large GPU build.

```bash
# CPU only (smallest; fine for this prototype)
pip install torch --index-url https://download.pytorch.org/whl/cpu

# NVIDIA GPU - pick the CUDA build for your driver from https://pytorch.org/get-started/locally/
# Apple Silicon - the default `pip install torch` includes MPS support
```

## 4. Install the project dependencies

```bash
pip install -r requirements.txt
python -I -c "import laya; print('laya', laya.__version__)"     # should print 0.4.x or newer
```

Optional semantic embeddings for RAG: `pip install sentence-transformers` and set `EMBEDDING_PROVIDER=sentence-transformers`.

## 5. Start PostgreSQL with pgvector

**Option A — Docker (recommended).** The port is bound to `127.0.0.1` only.

```bash
cp .env.example .env          # then edit PG_PASSWORD in .env
docker compose up -d
docker compose ps             # STATUS should become "healthy"
```

**Option B — native PostgreSQL 16.**

```bash
sudo apt install postgresql-16 postgresql-16-pgvector        # macOS: brew install postgresql@16 pgvector
sudo -u postgres psql -c "CREATE USER laya WITH PASSWORD 'change_me_local_only';"
sudo -u postgres psql -c "CREATE DATABASE laya_credit OWNER laya;"
sudo -u postgres psql -d laya_credit -c "CREATE EXTENSION vector; CREATE EXTENSION pgcrypto;"
```

`schema.sql` runs `CREATE EXTENSION IF NOT EXISTS vector`; if the `laya` user is not a superuser, create both extensions as `postgres` once (last line above).

## 6. Configure `.env`

Open `.env` and review at least:

| Variable | Set to |
|---|---|
| `PG_PASSWORD` | the password from step 5 |
| `DECISION_BACKEND` | `laya` (real model) — or `mock` for a no-download smoke test |
| `LAYA_MODEL` | `auto` (router picks per request) or pin `english` |
| `LAYA_DEVICE` | empty for auto, or `cpu` / `cuda` / `mps` |
| `LAYA_REVISION` | after your first validated run, the Hugging Face commit SHA of the checkpoint |

## 7. Download the Laya checkpoint (first run only)

The checkpoint downloads automatically on the first decision. To do it explicitly and see progress:

```bash
python -I -c "from laya import Router; r = Router(default='english'); \
print(r.predict('warm-up', {'q': {'type': 'noul', 'instructions': 'Is this a test?'}})['routing'])"
```

With `LAYA_MODEL=auto` the engine also needs the **multilingual** checkpoint (test `TW_05` is in Hindi). Download both before going offline — use the same revision you pin in `.env`:

```bash
python -I -c "from laya import Router; Router(revision='<sha or leave empty>' or None).preload(['english', 'multilingual'])"
```

If you skip this and set `HF_HUB_OFFLINE=1`, the engine now stops at start-up with a message naming the missing checkpoint (earlier versions failed mid-run on the first Hindi case).

Expected output: a dict starting `{'model': 'english', 'repo': 'convaiinnovations/laya', ...}`. That means the checkpoint loaded and answered.

You will also see this warning, which is expected and harmless for this project:

```
RuntimeWarning: laya: this checkpoint ships invalid temperatures or values outside [0.5, 5];
using choice:11+=0.10058280825614929 -> 0.5. Treat confidence from the affected entries as uncalibrated.
```

The shipped checkpoint carries an over-sharp calibration value for `choice` questions with **11 or more options**, and Laya clamps it on load. Every question in this engine has 2 or 3 options, so none of them use that value. Keep the warning visible rather than suppressing it: it is the kind of signal a model-risk reviewer should see.

Record the exact checkpoint revision you just validated and pin it in `.env` (`LAYA_REVISION=<sha>`):

```bash
ls ~/.cache/huggingface/hub/models--convaiinnovations--laya/snapshots/
```

The English checkpoint is roughly 0.8 GB. It is cached in `~/.cache/huggingface` (move it with `HF_HUB_CACHE`).
For data localisation, uncomment `HF_HUB_OFFLINE=1` in `.env` afterwards — the engine then makes no outbound calls.

## 8. Load the dummy data

```bash
python scripts/01_create_dummy_data.py --write-tests
```

Expected tail of the output:

```
[4/6] Registering decision model
  model registered: id=1 backend=laya checkpoint=auto revision=UNPINNED min_confidence=0.7
[5/6] Generating synthetic applicant pool
  PL: 100 synthetic applicants -> data/synthetic/applicants_PL.jsonl
...
  20 test cases -> tests/cases/
Done.
```

## 9. Verify

```bash
pytest -q tests/unit                                                        # 10 passed
python scripts/02_decision_engine.py --file tests/cases/PL_01_clean_approve.json
python scripts/02_decision_engine.py --verify-audit                         # "valid": true
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `type "vector" does not exist` | pgvector not installed, or extension not created — see step 5 |
| `UnregisteredModelError` | You changed `DECISION_BACKEND`, `LAYA_MODEL` or `LAYA_REVISION`. Re-register: `python scripts/01_create_dummy_data.py --register-only` |
| `laya.load()` hangs | TensorFlow installed in the same environment can deadlock `transformers` (known Laya issue). Use a clean venv without TensorFlow |
| Slow first decision | Checkpoint download + model build (seconds). Later calls are fast; batch runs reuse the loaded model |
| `ModuleNotFoundError: laya` | Run scripts with the venv's Python (`source .venv/bin/activate`) |
| Hugging Face blocked by corporate proxy | Download the checkpoint on an allowed machine, copy the cache folder, set `HF_HUB_CACHE` and `HF_HUB_OFFLINE=1` |
