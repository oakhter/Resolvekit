import subprocess
import sys
import os
import time
import signal
from dotenv import load_dotenv

# ── Paths ────────────────────────────────────────────────────
APP_DIR   = os.path.dirname(os.path.abspath(__file__))
VENV_PATH = os.path.join(APP_DIR, ".venv")
LOG_DIR   = os.path.join(APP_DIR, "diagnostics", "logs")
PORT      = 8000
DEBUG     = True   # True → app log messages visible in terminal

load_dotenv(os.path.join(APP_DIR, ".env"))

venv_python = os.path.join(
    VENV_PATH,
    "Scripts" if os.name == "nt" else "bin",
    "python"
)

# ── Auto-restart inside venv ─────────────────────────────────
if os.path.realpath(sys.executable) != os.path.realpath(venv_python):
    if not os.path.exists(venv_python):
        print("Virtual environment not found.")
        print(f"   Run:  python3 -m venv {VENV_PATH}")
        print(f"         pip install -r requirements.txt")
        sys.exit(1)
    os.execv(venv_python, [venv_python] + sys.argv)


processes = []
log_files = []


def log(msg: str) -> None:
    print(msg, flush=True)


def cleanup(sig=None, frame=None) -> None:
    log("\nShutting down...")
    for p in processes:
        try:
            p.terminate()
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
        except Exception:
            pass
    for f in log_files:
        try:
            f.close()
        except Exception:
            pass
    sys.exit(0)


# ── DB pre-flight ─────────────────────────────────────────────
def check_db() -> bool:
    """Verify the configured Postgres database is reachable before starting."""
    try:
        import psycopg2

        database_url = os.getenv("DATABASE_URL")

        if not database_url:
            log("ERROR: DATABASE_URL is not set in .env")
            return False

        try:
            conn = psycopg2.connect(database_url, connect_timeout=5)
            conn.close()
        except Exception as e:
            first_line = str(e).strip().splitlines()[0]
            log("ERROR: DATABASE_URL is not reachable.")
            log(f"   {first_line}")
            log("   -> Start PostgreSQL and try again.")
            return False

        return True

    except ImportError:
        log("ERROR: psycopg2 is not installed — run: pip install psycopg2-binary")
        return False


def spawn(cmd: list, **kwargs) -> subprocess.Popen:
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def ensure_log_dir() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)


# ── Port cleanup ─────────────────────────────────────────────
def kill_port(port: int) -> None:
    """Kill any process currently holding the given port."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit():
                        subprocess.run(["taskkill", "/F", "/PID", pid],
                                       capture_output=True, timeout=5)
                        log(f"   Cleared stale process on port {port} (PID {pid})")
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True, timeout=5
            )
            for pid in result.stdout.split():
                if pid.strip().isdigit():
                    subprocess.run(["kill", "-9", pid.strip()],
                                   capture_output=True, timeout=5)
                    log(f"   Cleared stale process on port {port} (PID {pid.strip()})")
    except Exception:
        pass


# ── Main ─────────────────────────────────────────────────────
def main() -> None:
    signal.signal(signal.SIGINT,  cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    ensure_log_dir()

    log("\n── ResolveKit ────────────────────────")
    log(f"   Port:  {PORT}")
    log(f"   Mode:  {'DEBUG (app logs on)' if DEBUG else 'QUIET'}")
    log(f"   Demo:  {os.getenv('DEMO_MODE', 'true')}")
    log("─────────────────────────────────────────────\n")

    # ── 1. DB check ───────────────────────────────────────────
    log("Checking database connections...")
    if not check_db():
        sys.exit(1)
    log("Databases connected\n")

    # ── 2. FastAPI ────────────────────────────────────────────
    kill_port(PORT)
    log("Starting FastAPI server...")

    uvicorn_cmd = [
        venv_python, "-m", "uvicorn", "backend.api.app:app",
        "--host", "0.0.0.0",
        "--port", str(PORT),
        "--no-access-log",
        "--log-level", "warning",
    ]

    out = None if DEBUG else subprocess.DEVNULL
    err = None if DEBUG else subprocess.DEVNULL

    server = spawn(uvicorn_cmd, cwd=APP_DIR, stdout=out, stderr=err)
    processes.append(server)
    time.sleep(3)

    if server.poll() is not None:
        log("ERROR: FastAPI failed to start.")
        log(f"   Debug: python3 -m uvicorn backend.api.app:app --port {PORT}")
        cleanup()

    log(f"Main app        -> http://localhost:{PORT}")
    log(f"Ticket sandbox  -> http://localhost:{PORT}")
    log(f"Configurator    -> http://localhost:{PORT}/configurator\n")

    log("Ready.\n")

    # ── 3. Monitor ───────────────────────────────────────────
    try:
        while True:
            time.sleep(5)

            if server.poll() is not None:
                log("ERROR: FastAPI stopped unexpectedly")
                cleanup()

    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
