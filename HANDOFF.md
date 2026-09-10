# SGF ingestion + KataGo slice

Implemented in D:/gpt/workspace/GoGame, the existing Streamlit project. The
original task workspace was AestheticLens and was left unchanged. GoGame had no
Git metadata; initialized codex/sgf-katago locally. The first commit therefore
also tracks existing application source. Credentials, database, images and venv
are excluded. No push.

SGF upload selects first-variation mainline positions, defaulting to final move.
Move 0 is setup. Coordinates reuse extracted board conversions; sgfmill handles
captures. Supports root AB/AW/AE, handicap/PL, pass, metadata and SGF encodings.
Rejects collections, non-Go games, boards outside 2–19, conflicting setup,
occupied-point moves and midgame setup/PL edits. Reconstruction is not a full
rules adjudicator: sgfmill allows suicide and does not enforce ko; preserved
history is sent to KataGo for rules validation. Unknown rules yield engine errors
or visible engine warnings. Missing RU/KM defaults to Japanese/6.5, shown in UI.

KataGoAdapter is a protocol; LocalKataGoAdapter accepts explicit paths or env.
Position and AnalysisResult are independent of Streamlit. Snapshot callers supply
next_player, rules and komi; known SGF histories are preserved. Evaluations use
Black's perspective; candidates are sorted by engine order. Raw policy uses
KataGo's board-index order plus pass, with -1 for illegal moves. Subprocesses have
a timeout and are reaped by subprocess.run. No fake analysis or GPT fallback.
GPT prompts, notebook and embedding logic remain unchanged.

## Configuration and verification

Use a working Python (the pre-existing venv references a missing D:/anaconda).
`python -m pip install -r requirements.txt`; install pytest for tests.
Set KATAGO_EXECUTABLE, KATAGO_CONFIG (analysis config), KATAGO_MODEL, and optional
KATAGO_TIMEOUT (seconds, default 120) in the existing .env or process environment.
Paths with spaces are supported. Preserve the existing OpenAI configuration for
screenshot/commentary features. Launch with `python -m streamlit run app.py`.

Upload `(;SZ[9]KM[6.5];B[ba];W[aa];B[ab];W[])`. Check move 0 empty, move 2 has
black B9/white A9, move 3 captures white A9, and final move 4 passes unchanged.
Click KataGo analysis with paths unset: explicit unavailable error, no GPT call.
Configure real engine paths; analyze moves 2 and 4, check Black-perspective
winrate/lead, candidates and warnings. Confirm screenshot/manual editing, GPT
commentary and the existing notebook workflow still operate with configured
provider credentials.

## Validation

`python -m pytest -q`: 30 passed (offline protocol responses are test fixtures only).
`python -m compileall -q app.py analyzer.py memory_store.py board_state.py sgf_ingestion.py katago_adapter.py`: passed.
`git diff --cached --check`: passed.
UI smoke test could not run: Streamlit is absent in working Python and its installation failed with a proxy connection reset. Real KataGo/GPU/model and live GPT calls remain unverified; no engine is configured.

Exact next step: configure real KataGo analysis executable/config/model paths,
then run the SGF move-2/move-4 smoke test above before integrating engine output
with GPT commentary.

Protocol reference: https://github.com/lightvector/KataGo/blob/master/docs/Analysis_Engine.md
