import argparse
import json
import os
from pathlib import Path
import queue
import signal
import sys
import threading
import time
import webbrowser

from .config import state_directory, system_language


class Instance:
    def __init__(self, directory):
        self.stream = open(directory / "instance.lock", "a+b")
        self.stream.seek(0)
        self.stream.write(b"0")
        self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError("already_running") from None

    def close(self):
        self.stream.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="DiskDownloader — download original Google Drive files")
    parser.add_argument("--headless", action="store_true", help="Run the local service without the status window")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--port", type=int, default=0, help="Local port (0 selects an available port)")
    parser.add_argument("--background", action="store_true", help="Start a detached local service")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if args.background:
        import subprocess
        command = [sys.executable] + ([] if getattr(sys, "frozen", False) else [str(Path(__file__).parent.parent / "run.py")])
        command += ["--headless", "--port", str(args.port)]
        if args.no_browser:
            command += ["--no-browser"]
        kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)
        return 0
    directory = state_directory()
    try:
        instance = Instance(directory)
    except RuntimeError:
        try:
            import re
            runtime = json.loads((directory / "runtime.json").read_text(encoding="utf-8"))
            url = runtime["url"]
            if re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}/", url) and not args.no_browser:
                webbrowser.open(url)
        except Exception:
            pass
        return 0
    lang = system_language()
    text = json.loads((Path(__file__).parent / "locales" / (lang + ".json")).read_text(encoding="utf-8"))
    ui_queue = queue.Queue()
    stopping = threading.Event()
    stopped = threading.Event()
    ready = threading.Event()
    failed = threading.Event()
    service = {}
    root = None
    if not args.headless:
        try:
            if os.name == "nt":
                import ctypes
                try:
                    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
                except (AttributeError, OSError):
                    pass
                font = Path(__file__).parent / "static/fonts/GoogleSans.ttf"
                ctypes.windll.gdi32.AddFontResourceExW(str(font), 0x10, 0)
            import tkinter as tk
            root = tk.Tk()
            root.title("DiskDownloader")
            root.configure(bg="#101315")
            root.geometry("540x330")
            root.minsize(480, 300)
            root.option_add("*Font", ("Google Sans", 11))
            tk.Label(root, text="↓  DiskDownloader", bg="#101315", fg="#b8f2c8", font=("Google Sans", 23, "bold")).pack(anchor="w", padx=28, pady=(25, 15))
            status = tk.Label(root, text="Запускаем приложение…" if lang == "ru" else "Starting the application…", bg="#101315", fg="#e7edef", wraplength=475, justify="left")
            status.pack(anchor="w", padx=28, pady=4)
            detail = tk.Label(root, text="", bg="#101315", fg="#9daeb5", wraplength=475, justify="left")
            detail.pack(anchor="w", padx=28, pady=8)
            controls = tk.Frame(root, bg="#101315")
            controls.pack(side="bottom", fill="x", padx=28, pady=25)
            def button(label, command):
                result = tk.Button(controls, text=label, command=command, bg="#b8f2c8", fg="#163922", activebackground="#cff8da", relief="flat", padx=15, pady=9, cursor="hand2", borderwidth=0)
                result.pack(side="left", padx=(0, 10))
                return result
            open_button = button("Открыть интерфейс" if lang == "ru" else "Open interface", lambda: webbrowser.open(service["url"]) if ready.is_set() else None)
            button(text["exit"], lambda: threading.Thread(target=shutdown, daemon=True).start())
            root.protocol("WM_DELETE_WINDOW", lambda: threading.Thread(target=shutdown, daemon=True).start())
        except Exception:
            if root:
                root.destroy()
            root = None

    def pick_folder():
        if root is None:
            raise RuntimeError("no_native_window")
        result = {"event": threading.Event()}
        ui_queue.put(("pick", result))
        while not result["event"].wait(0.2):
            if stopping.is_set():
                return ""
        return result.get("path", "")

    def shutdown():
        if stopping.is_set():
            return
        stopping.set()
        while not ready.is_set() and not failed.is_set():
            time.sleep(0.05)
        if ready.is_set():
            engine = service["engine"]
            if engine.busy():
                engine.pause(engine.active, stopped=True)
                engine.thread.join()
            service["server"].close()
            service["server"].task_dispatcher.shutdown()
        stopped.set()

    def start():
        try:
            ui_queue.put(("status", "Открываем историю…" if lang == "ru" else "Opening task history…"))
            from .store import Store
            from .quota import Quota
            from .auth import Auth
            from .drive import Drive
            from .engine import Engine
            from .web import create_app
            from waitress import create_server
            store = Store(directory)
            auth = Auth(store)
            quota = Quota(store)
            drive = Drive(auth, quota)
            engine = Engine(store, drive)
            app = create_app(store, auth, drive, engine, shutdown=shutdown, picker=pick_folder)
            server = create_server(app, host="127.0.0.1", port=args.port, threads=8, ident="DiskDownloader")
            url = "http://127.0.0.1:" + str(server.effective_port) + "/"
            app.config["APP_ORIGIN"] = url.rstrip("/")
            service.update(store=store, auth=auth, engine=engine, server=server, url=url)
            runtime = directory / "runtime.json"
            temp = directory / "runtime.new"
            temp.write_text(json.dumps({"pid": os.getpid(), "url": url}), encoding="utf-8")
            os.replace(temp, runtime)
            ready.set()
            if not args.no_browser and not stopping.is_set():
                webbrowser.open(url)
            if not stopping.is_set():
                server.run()
        except Exception:
            failed.set()
            ui_queue.put(("status", text["application_failed"]))
            if root is None:
                stopped.set()
        finally:
            if ready.is_set():
                stopped.set()

    worker = threading.Thread(target=start, name="local-service", daemon=True)
    worker.start()
    def on_signal(*_):
        threading.Thread(target=shutdown, name="signal-shutdown", daemon=True).start()
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    if root:
        def tick():
            while not ui_queue.empty():
                kind, value = ui_queue.get()
                if kind == "status":
                    status.configure(text=value)
                elif kind == "pick":
                    from tkinter import filedialog
                    try:
                        root.attributes("-topmost", True)
                        value["path"] = filedialog.askdirectory(parent=root, title=text["choose_folder"], mustexist=True)
                    finally:
                        root.attributes("-topmost", False)
                        value["event"].set()
            if stopped.is_set():
                root.destroy()
                return
            if stopping.is_set():
                status.configure(text=text["shutdown_notice"])
            elif ready.is_set():
                engine = service["engine"]
                if engine.busy():
                    job = service["store"].job(engine.active)
                    status.configure(text=text.get(job["phase"], job["phase"]))
                    detail.configure(text=job["name"] + "\n" + str(engine.live["files_checked"]) + " " + text["checked_files"])
                else:
                    status.configure(text=text["connected"] if service["auth"].public()["connected"] else text["not_connected"])
                    detail.configure(text="127.0.0.1 · " + text["originals_only"])
            root.after(500, tick)
        root.after(100, tick)
        root.mainloop()
    else:
        while not stopped.wait(0.5):
            pass
    if ready.is_set():
        runtime = directory / "runtime.json"
        try:
            if json.loads(runtime.read_text())["pid"] == os.getpid():
                runtime.unlink()
        except (OSError, ValueError, KeyError):
            pass
    instance.close()
    return 1 if failed.is_set() else 0
