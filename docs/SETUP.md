# OnRecord Setup

Setup for OnRecord, conversation memory on Ray-Ban Meta Display glasses. Target setup time: under 30 minutes.

Everything for the demo runs locally on one laptop. No cloud accounts needed.

Repo layout:

- `server/` : Python server (FastAPI ingest, MCP server)
- `dashboard/` : Next.js dashboard
- `bridge-ios/` : Swift iOS bridge app

## 1. Prerequisites

- macOS (demo laptop: MacBook Air M4, 16GB)
- Python 3.12
- Node 20 or newer, for the dashboard
- Homebrew, current version
- Xcode, current version, only for the iOS bridge. Skip Xcode if you are only running the server and dashboard.

Check versions:

```bash
python3 --version
node --version
brew --version
```

## 2. Postgres via Postgres.app

Postgres is the demo day choice. SQLite is the survival fallback.

Demo day path, Postgres:

1. Install Postgres.app, current version, from postgresapp.com or with Homebrew:

```bash
brew install --cask postgres-unofficial
```

If the cask name differs on your machine, install Postgres.app manually from the website.

2. Launch Postgres.app once. The first launch initializes the data directory.
3. Open the Postgres.app window, click the plus button, and create a server if prompted. Then create the database:

```bash
createdb onrecord
```

Or in `psql`:

```sql
CREATE DATABASE onrecord;
```

4. Copy the example env file and set `DATABASE_URL`:

```bash
cp .env.example .env
```

In `.env`:

```text
DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/onrecord
```

Replace `user` and `pass` with your local Postgres user and password. For a default Postgres.app install, the user is usually your macOS username and there is often no password for local connections. Adjust the value to match your install.

Fallback, plain and simple:

Skip this section entirely. Leave `DATABASE_URL` unset or empty in `.env` and the server uses SQLite at `DATA_DIR/onrecord.db` (default: `./data/onrecord.db`). No other change needed.

## 3. Python Server

From the repo root:

```bash
make setup
```

This creates `server/.venv` and installs `server/requirements.txt` plus `server/requirements-dev.txt`.

ML extras, demo laptop only:

Install the heavy ML extras only on the demo laptop. The models are large and the download takes time.

```bash
cd server
source .venv/bin/activate
pip install -e ".[ml]"
cd ..
```

This installs: faster-whisper, chromadb, psycopg, sentence-transformers, insightface, speechbrain.

Without the ML extras, the server runs on deterministic test doubles. Tests and dev on other machines run fine without them. Do not install the ML extras on every machine.

Run tests to confirm the base install works:

```bash
make test
```

## 4. Model Download Rule

Download the faster-whisper model at home, before the event.

- Model: `small.en` (set by `STT_MODEL`)
- Rule: the first run of the transcriber pulls the model. Do that first run at home on a fast connection.
- Never first-download at the venue. Venue networks are slow, filtered, or down.

Verify step, required before the event:

1. Start the server (see Section 7).
2. Send or record one 30 second test clip through the normal ingest path.
3. Confirm a transcript comes back with text, not an empty result.
4. Confirm the startup log shows the real transcriber, not the fake transcriber.

If the transcript is empty, see Troubleshooting.

## 5. Dashboard

```bash
cd dashboard
npm install
npm run dev
```

Dashboard runs on port 3000: http://localhost:3000

Leave this terminal open while the dev server runs.

## 6. Env Vars

Config is via `.env`, copied from `.env.example` in the repo root.

| Var | Meaning | Demo day value |
|---|---|---|
| `DATABASE_URL` | Database connection string. If unset, SQLite is used at `DATA_DIR/onrecord.db`. | `postgresql+psycopg://user:pass@localhost:5432/onrecord` with your local user and password. Clear it to fall back to SQLite. |
| `DATA_DIR` | Local data directory for SQLite and other local files. | `./data` |
| `CHROMA_DIR` | Chroma vector store directory. | `./data/chroma` |
| `PORT` | FastAPI ingest server port. | `8000` |
| `FACE_THRESHOLD` | Face match threshold, combined face and voice path. | `0.45` |
| `VOICE_THRESHOLD` | Voice match threshold, combined face and voice path. | `0.75` |
| `FACE_ONLY_THRESHOLD` | Face match threshold when only face is available. | `0.60` |
| `VOICE_ONLY_THRESHOLD` | Voice match threshold when only voice is available. | `0.85` |
| `STT_MODEL` | faster-whisper model name. | `small.en` |
| `SLACK_WEBHOOK_URL` | Slack webhook for the digest. Optional. When unset, the digest queues locally. | Leave unset unless you have a webhook ready and tested. |
| `ONRECORD_REHEARSAL` | Enables rehearsal fixtures. Fixtures are quarantined data. | `0`. Must be `0` on Oct 25 during judging. Never set to `1` during judging. |

Warning on `ONRECORD_REHEARSAL`: rehearsal fixtures are quarantined data for practice only. `make seed` sets `ONRECORD_REHEARSAL=1` for that run. Your normal `.env` must keep it at `0`.

## 7. Run Commands, Demo Day Order

Run in this order.

1. Start Postgres.app

Open Postgres.app and confirm it is running. Skip this step only if you are using the SQLite fallback.

2. Start the laptop hosted network

Set up the network named `insight-net` before the bridge connects:

- On the laptop, set up Internet Sharing or a hosted network named `insight-net`.
- Connect the iPhone to `insight-net`.
- Venue WiFi is never used for the bridge link. The bridge app on the iPhone connects to the laptop over `insight-net` only.

3. Start the ingest server, terminal 1

From the repo root:

```bash
make run
```

FastAPI ingest server on port 8000.

4. Start the MCP server, terminal 2

Open a separate terminal, from the repo root:

```bash
make mcp
```

MCP server on stdio.

The MCP server exposes exactly 5 tools:

- `list_people`
- `list_interactions_with_person`
- `search_conversations`
- `get_interaction`
- `get_person_profile`

5. Start the dashboard, terminal 3

```bash
cd dashboard
npm run dev
```

Dashboard on port 3000.

6. Verify

Check health:

```bash
curl http://localhost:8000/health
```

Expected: HTTP 200.

Check MCP: open the MCP inspector and confirm it lists exactly the 5 tools above, no more and no less.

Check the bridge: open the bridge app on the iPhone, confirm it connects to the laptop over `insight-net`, and confirm a test interaction appears in the dashboard.

Other commands:

```bash
make test   # run pytest
make seed   # load rehearsal fixtures, sets ONRECORD_REHEARSAL=1 for that run only
```

Do not run `make seed` on demo day during judging.

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `GET /health` fails or connection refused | Server not started, or port 8000 is taken by another process | Run `make run` in terminal 1 and leave it open. If port 8000 is taken, stop the other process or set `PORT` in `.env` and use that port consistently. |
| MCP lists no tools | Wrong terminal, server still booting, or MCP process did not start | Confirm you ran `make mcp` in a separate terminal. Wait for the ingest server to finish booting, then run `make mcp` again. Confirm the inspector lists exactly 5 tools. |
| Transcripts are empty | ML extras missing, so the fake transcriber is active | Check the startup log. On the demo laptop, activate the venv in `server/` and run `pip install -e ".[ml]"`, then restart the server. Re-run the 30 second test clip from Section 4. |
| Chroma errors on boot | `CHROMA_DIR` missing or not writable | Delete nothing. Check that `CHROMA_DIR` (default `./data/chroma`) exists and is writable. Create the directory if needed: `mkdir -p ./data/chroma`. Fix permissions, then restart the server. |
| Postgres connection refused | Postgres.app not running, or database `onrecord` does not exist | Launch Postgres.app and confirm it is running. Create the database with `createdb onrecord` if needed. Or clear `DATABASE_URL` in `.env` to fall back to SQLite and restart the server. |
| Phone cannot reach laptop | Phone and laptop are on different networks | Put both on `insight-net`, the laptop hosted network. Disconnect from venue WiFi for the bridge link. Reopen the bridge app and retry. |
| Dashboard does not load | Dashboard dev server not running, or `npm install` was skipped | Run `cd dashboard`, then `npm install`, then `npm run dev`. Open http://localhost:3000. |
