# aeo-pipeline

Answer Engine Optimization pipeline — weekly crawl, coverage diff, and recommendation engine.

## Usage

```bash
# Install
pip install -e .

# Run pipeline (dry-run, no DB writes)
aeo run securin.io --dry-run

# Fallback if editable install isn't on PATH
python -m aeo.cli run securin.io --dry-run

# Initialize database schema
aeo db-init

# Show latest recommendations
aeo report securin.io

# Show reference blueprint
aeo blueprint securin.io
```

## Configuration

Copy `.env.example` to `.env` and fill in the required values.
