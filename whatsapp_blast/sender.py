"""Concurrent, rate-limited, resumable message sender.

Channel-agnostic: takes a `send_fn(number, extra) -> (status, sid, error)`
closure so the same engine (throttling, worker pool, resumable CSV log,
consecutive-error circuit breaker) backs both WhatsApp template sends and
plain SMS sends. Build the closure with whatsapp_blast.channels.
"""
import csv
import os
import queue
import threading
import time
from typing import Callable

SUCCESS_STATUSES = {"queued", "accepted", "sent", "delivered"}

SendFn = Callable[[str, dict], tuple[str, str, str]]


def load_already_sent(log_path: str) -> set[str]:
    sent = set()
    if os.path.exists(log_path):
        with open(log_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") in SUCCESS_STATUSES:
                    sent.add(row["to"])
    return sent


def run_campaign(
    send_fn: SendFn,
    targets: list[tuple[str, dict]],  # [(e164_number, {extra columns for logging / templating})]
    log_path: str,
    rate_per_min: int = 500,
    workers: int = 8,
    consecutive_error_limit: int = 20,
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

    def handle(item):
        if stop_flag.is_set():
            return
        number, extra = item
        throttle()
        status, sid, err = send_fn(number, extra)
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
