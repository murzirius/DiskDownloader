"""Run the built executable with isolated state and test its real HTTP lifecycle."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import requests


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--executable")
    parser.add_argument("--window",action="store_true")
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    if args.executable:
        executable=Path(args.executable).resolve()
    elif sys.platform=="win32":
        executable=root/"dist/DiskDownloader/DiskDownloader.exe"
    elif sys.platform=="darwin":
        executable=root/"dist/DiskDownloader.app/Contents/MacOS/DiskDownloader"
    else:
        executable=root/"dist/DiskDownloader/DiskDownloader"
    assert executable.is_file(),"native executable missing"
    state=Path(tempfile.mkdtemp(prefix="diskdownloader-native-"))
    environment={**os.environ,"DISKDOWNLOADER_STATE":str(state),"PYTHONDONTWRITEBYTECODE":"1"}
    command=[str(executable),"--no-browser"]+([] if args.window else ["--headless"])
    with open(state/"startup.log","w",encoding="utf-8") as log:
        process=subprocess.Popen(command,cwd=state,env=environment,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+60
            while not (state/"runtime.json").exists():
                assert process.poll() is None,"executable exited before serving"
                assert time.monotonic()<deadline,"startup timeout"
                time.sleep(.1)
            runtime=json.loads((state/"runtime.json").read_text(encoding="utf-8"))
            url=runtime["url"]
            native_window=False
            native_picker=False
            if args.window and sys.platform=="win32":
                import ctypes
                from ctypes import wintypes
                user=ctypes.WinDLL("user32",use_last_error=True)
                callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
                user.EnumWindows.argtypes=(callback_type,wintypes.LPARAM)
                user.GetWindowThreadProcessId.argtypes=(wintypes.HWND,ctypes.POINTER(wintypes.DWORD))
                user.GetWindowTextW.argtypes=(wintypes.HWND,wintypes.LPWSTR,ctypes.c_int)
                user.IsWindowVisible.argtypes=(wintypes.HWND,)
                def windows():
                    result=[]
                    def collect(hwnd,_):
                        pid=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
                        if pid.value==runtime['pid'] and user.IsWindowVisible(hwnd):
                            title=ctypes.create_unicode_buffer(300);user.GetWindowTextW(hwnd,title,300)
                            result.append((hwnd,title.value))
                        return True
                    user.EnumWindows(callback_type(collect),0)
                    return result
                assert any(title=='DiskDownloader' for _,title in windows()),'native status window missing'
                native_window=True
            session=requests.Session();session.trust_env=False
            response=session.get(url,headers={"Accept-Language":"en-US"},timeout=10)
            assert response.status_code==200 and '<html lang="en">' in response.text
            token=re.search(r'name="app-token" content="([^"]+)"',response.text).group(1)
            assert session.get(url+"api/state",timeout=5).json()["quota"]["authoritative"] is False
            assert session.get(url+"static/fonts/GoogleSans.ttf",timeout=10).status_code==200
            assert session.post(url+"api/settings",json={"language":"ru"},timeout=5).status_code==403
            headers={"X-App-Token":token,"Origin":url.rstrip("/")}
            assert session.post(url+"api/settings",headers=headers,json={"language":"ru"},timeout=5).status_code==200
            assert '<html lang="ru">' in session.get(url,headers={"Accept-Language":"en"},timeout=5).text
            if native_window:
                assert session.post(url+'api/pick-folder',headers=headers,json={},timeout=5).status_code==200
                deadline=time.monotonic()+10
                dialog=None
                while not dialog and time.monotonic()<deadline:
                    dialog=next((hwnd for hwnd,title in windows() if title and title!='DiskDownloader'),None)
                    if not dialog:time.sleep(.1)
                assert dialog,'native folder dialog missing'
                user.PostMessageW.argtypes=(wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
                user.PostMessageW(dialog,0x10,0,0)
                deadline=time.monotonic()+10
                while session.get(url+'api/state',timeout=5).json()['picker']['pending']:
                    assert time.monotonic()<deadline,'folder picker did not cancel'
                    time.sleep(.1)
                native_picker=True
            second=subprocess.run(command,cwd=state,env=environment,stdout=log,stderr=log,timeout=30)
            assert second.returncode==0
            assert json.loads((state/"runtime.json").read_text(encoding="utf-8"))["pid"]==runtime["pid"]
            response=session.post(url+"api/shutdown",headers=headers,json={},timeout=15)
            assert response.status_code==200
            exit_code=process.wait(timeout=30)
            assert exit_code==0, f"native shutdown returned {exit_code}"
            assert not (state/"runtime.json").exists()
            print(json.dumps({"native_startup":True,"local_http":True,"auto_language":True,"csrf":True,
                              "bundled_font":True,"single_instance":True,"clean_shutdown":True,
                              "window_requested":args.window,"native_window_verified":native_window,
                              "native_picker_verified":native_picker,"google_account_tested":False}))
        except Exception:
            log.flush()
            print((state/"startup.log").read_text(encoding="utf-8",errors="replace")[-12000:],file=sys.stderr)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(20)
                except subprocess.TimeoutExpired:process.kill();process.wait(10)


if __name__=="__main__":main()
