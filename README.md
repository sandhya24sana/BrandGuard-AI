# BrandGuard AI

BrandGuard AI is a brand-governance application for managing brand guidelines and reviewing marketing content against those guidelines. It includes a React frontend, a Flask API, and a SQLite database. The application starts with no seeded brands or demo content; add a brand before using analysis features.

## Features

- Create, edit, and delete brand profiles.
- Manage approved facts, terminology guidance, claim rules, and stored visual guidelines for each brand.
- Enter content in the editor or upload `.txt`, `.md`, `.markdown`, `.docx`, `.pdf`, or `.json` files for text extraction.
- Run a quick check or save a content audit. The rule-based engine works without an API key; an optional Gemini API key enables AI analysis with rule-based checks retained as a fallback.
- Review categorized findings, risk scores, readability information, and suggested corrections. Apply individual fixes or adopt corrected content as a new version.
- Record reviewer decisions, reviewer details, and comments against the reviewed content version; stale frontend submissions are rejected. Existing review history is preserved without guessing a version for older records.
- View dashboard statistics and export a content audit report as JSON.

Visual rules are stored and included as context in optional Gemini text analysis, but the uploader does not accept images and image, color, logo, or layout validation is not implemented. Enabling it requires image ingestion and a validation pipeline, plus brand logo/reference assets where logo presence is checked; a Gemini key alone does not enable visual validation. Automated text analysis is not a substitute for human approval.

Automatic corrections use exact detected spans and an explicitly configured replacement-text field. Claim disclaimers and policy explanations are not treated as replacement copy; for compatibility, only an explicitly quoted alternative following `Use:` in a legacy disclaimer is extracted as replacement text. Other existing claim rules without replacement text remain manual-review issues. Applying an individual correction or adopting all corrections creates a new content version and re-runs the analysis; grammar is not automatically certified, so human review remains required.

## Project layout

```text
BrandGuard-AI/
├── backend/
│   ├── app.py
│   ├── config.py
│   ├── database.py
│   ├── seed_data.py        # Compatibility entry point; does not seed data
│   ├── routes/
│   ├── services/
│   ├── tests/
│   └── requirements.txt
├── frontend/
│   ├── src/
│   ├── package.json
│   └── vite.config.js
├── run.bat
├── run.sh
└── README.md
```

The backend creates the database schema on startup but does not insert example brands or records.

## Requirements

- Python 3.9 or newer
- Node.js and npm (for frontend development or building)

## Run the application

### Windows

From the project directory, run:

```cmd
run.bat
```

The script installs backend dependencies, builds the frontend if no build is present, and starts the Flask application at `http://localhost:5000`.

### Linux or macOS

From the project directory, run:

```bash
chmod +x run.sh
./run.sh
```

### Manual setup

Install backend dependencies from the project directory:

```bash
python -m pip install -r backend/requirements.txt
```

Optionally copy `backend/.env.example` to `backend/.env` and set `GEMINI_API_KEY` to enable backend Gemini text analysis. The key stays on the backend; audit text and brand guidelines are sent to Google's Gemini API when this is enabled. Without a key, analysis uses the local rule-based engine.

Start the backend in one terminal:

```bash
python backend/app.py
```

For frontend development with Vite, install dependencies and start the dev server in another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` for the Vite frontend or `http://localhost:5000` for the backend-served production frontend (after building it with `npm run build` in `frontend`).

## Backend tests

Run the backend and frontend tests from the project directory:

```bash
python -m unittest discover -s backend/tests -v
cd frontend
npm test
npm run lint
npm run build
```

The backend tests use isolated temporary databases and cover brand and guideline management, content auditing and versioning, review decisions, correction behavior, API validation, dashboard aggregation, report export, and brand deletion. Frontend tests cover issue rendering, API failures, brand-scoped request races, and review-version submission.

## Configuration

Backend settings can be provided through `backend/.env` or environment variables. Supported settings include `GEMINI_API_KEY`, `DATABASE_PATH`, `UPLOAD_FOLDER`, `PORT`, `FLASK_DEBUG`, and `CORS_ORIGINS`. The default database path is `backend/brandguard.db`.

Uploaded files are extracted for analysis and are not listed as preloaded project data.
