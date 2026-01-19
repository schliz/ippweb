# ippweb

Web-based printing service using CUPS/IPP.

## Features

- List available printers from CUPS
- Display all printer options (from PPD)
- Upload PDF files for printing
- Configure print options (page size, copies, duplex, etc.)
- Monitor job status with real-time updates
- View pages printed (job-impressions-completed)
- OIDC Authentication
- Page count reports

## Requirements

### System Dependencies

```bash
# Debian/Ubuntu
sudo apt install cups libcups2-dev python3-dev

# Fedora/RHEL
sudo dnf install cups cups-devel python3-devel
```

CUPS must be installed and running with at least one printer configured.

### Python Dependencies

Python 3.10+ required.

## Installation

1. Clone the repository:
```bash
git clone <repo-url>
cd ippweb
```

2. Create and activate a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Copy environment configuration:
```bash
cp .env.example .env
# Edit .env as needed
```

## Running (Development)

```bash
python run.py
```

Or with Flask CLI:
```bash
flask --app app run --debug
```

The application will be available at http://localhost:5000

## Production

See the Dockerfile and docker-compose.prod.yml

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| FLASK_DEBUG | false | Enable debug mode |
| FLASK_SECRET_KEY | (required) | Secret key for sessions |
| UPLOAD_FOLDER | ./uploads | Temporary upload directory |
| MAX_CONTENT_LENGTH | 52428800 | Max upload size (50MB) |
| CUPS_SERVER | localhost | CUPS server address |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List printers |
| GET | `/print/<printer>` | Print options form |
| POST | `/print/<printer>` | Submit print job |
| GET | `/job/<id>` | Job status page |
| GET | `/api/job/<id>` | Job status (JSON) |
| POST | `/job/<id>/cancel` | Cancel job |
| GET | `/health` | Health check |

## Future Enhancements

Planned features (not in MVP):

- [ ] Option filtering UI (show only common options)
- [ ] Additional file format support (PostScript, images)
- [ ] Multi-user support with quotas

## License

MIT
