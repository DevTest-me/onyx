import os
import signal
import subprocess
import sys
import threading

import serve_frontend


agent_process = None


def _start_agent_runtime():
    global agent_process
    if os.getenv("RUN_AGENT_RUNTIME", "1").strip().lower() in {"0", "false", "no"}:
        print("Agent runtime disabled by RUN_AGENT_RUNTIME.")
        return

    required = ("WALLET_JSON", "ONYX_PROGRAM_ID", "VAN_PROGRAM_ID")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        print(f"Agent runtime not started; missing env vars: {', '.join(missing)}")
        return

    agent_process = subprocess.Popen(
        [sys.executable, "runtime.py"],
        cwd=os.path.join(os.path.dirname(__file__), "agent"),
    )
    print(f"Agent runtime started with pid {agent_process.pid}.")

    def _watch_agent():
        code = agent_process.wait()
        print(f"Agent runtime exited with code {code}.")

    threading.Thread(target=_watch_agent, daemon=True).start()


def _shutdown(_signum, _frame):
    if agent_process and agent_process.poll() is None:
        agent_process.terminate()
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    _start_agent_runtime()
    serve_frontend.main()
