# UI Configurator

This folder contains the local browser configurator.

Run the app:

```bash
python start.py
```

Open:

```text
http://localhost:8000/configurator
```

The UI reads and writes YAML config through the FastAPI endpoints in `backend/api/app.py`:

- `GET /configurator/config`
- `POST /configurator/config`
- `POST /configurator/validate`

The saved local files live in `config/*.yaml`; the committed defaults live in `config/*.example.yaml`.

Basic Settings are intended for product setup, source paths, column mappings, and output mode. Advanced Settings hold technical controls such as chunk rules, route policies, source authority, workflow stages, parent expansion, and privacy settings.

Chunk rules, route policies, and source authority use simple editors by default. Advanced JSON editors remain available for users who want direct YAML-compatible structures.
