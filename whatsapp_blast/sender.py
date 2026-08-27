"""Concurrent, rate-limited, resumable WhatsApp template sender."""
import csv
import os
import queue
import threading
import time

from .twilio_api import TwilioClient

SUCCESS_STATUSES = {"queued", "accepted", "sent", "delivered"}


def load_already_sent(log_path: str) -> set[str]:
    sent = set()
    if os.path.exists(log_path):
        with open(log_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") in SUCCESS_STATUSES:
                    sent.add(row["to"])
    return sent


def run_campaign(
    client: TwilioClient,
    from_number: str,
    content_sid: str,
    targets: list[tuple[str, dict]],  # [(e164_number, {extra columns for logging / variables})]
    log_path: str,
    content_variables_template: dict | None = None,
    rate_per_min: int = 500,
    workers: int = 8,
    consecutive_error_limit: int = 20,
    request_timeout: int = 30,
) -> dict:
    delay = 60.0 / rate_per_min
    already = load_already_sent(log_path)
    to_send = [t for t in targets if t[0] not in already]

    log_exists = os.path.exists(log_path)
    extra_cols = sorted({k for _, extra in targets for k in extra.keys()})
    logf = open(log_path, "a", newline="", encoding="utf-8")
    w = csv.writer(logf)
    if not log_exists:
        w.writerow(["timestamp", "to", *extra_cols, "status", "sid", "error"])

    log_lock = threading.Lock()
    rate_lock = threading.Lock()
    last_send_time = [0.0]
    counters = {"sent": 0, "error": 0, "consecutive_errors": 0, "done": 0}
    stop_flag = threading.Event()

    def throttle():
        with rate_lock:
            now = time.monotonic()
            wait = last_send_time[0] + delay - now
            if wait > 0:
                time.sleep(wait)
            last_send_time[0] = time.monotonic()

    def render_variables(extra: dict) -> dict | None:
        if not content_variables_template:
            return None
        return {k: str(extra.get(v, "")) for k, v in content_variables_template.items()}

    def handle(item):
        if stop_flag.is_set():
            return
        number, extra = item
        throttle()
        variables = render_variables(extra)
        status, sid, err = client.send_template_message(
            number, from_number, content_sid, variables, timeout=request_timeout
        )
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with log_lock:
            w.writerow([ts, number, *[extra.get(c, "") for c in extra_cols], status, sid, err])
            logf.flush()
            counters["done"] += 1
            if status == "error":
                counters["error"] += 1
                counters["consecutive_errors"] += 1
                if counters["consecutive_errors"] >= consecutive_error_limit:
                    stop_flag.set()
            else:
                counters["sent"] += 1
                counters["consecutive_errors"] = 0

    work_q: queue.Queue = queue.Queue()
    for item in to_send:
        work_q.put(item)

    def run_worker():
        while not stop_flag.is_set():
            try:
                item = work_q.get_nowait()
            except queue.Empty:
                return
            handle(item)
            work_q.task_done()

    threads = [threading.Thread(target=run_worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    logf.close()

    return {
        "targeted": len(targets),
        "already_sent": len(already),
        "attempted": len(to_send),
        "sent": counters["sent"],
        "errors": counters["error"],
        "aborted_on_errors": stop_flag.is_set(),
    }
