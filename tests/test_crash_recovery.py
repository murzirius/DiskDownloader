import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from test_downloads import setup_job, run
from diskdownloader.store import Store
from diskdownloader.engine import Engine


def test_abrupt_process_exit_keeps_verified_partial_and_history(tmp_path):
    state=tmp_path/'state';state.mkdir();root=tmp_path/'archive'
    store=Store(state);content=b'synthetic photo bytes'*60000
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",content)])
    project=Path(__file__).resolve().parents[1]
    script=r'''
import sys,os,itertools
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_downloads import Drive,metadata,FOLDER
from diskdownloader.store import Store
from diskdownloader.engine import Engine
import diskdownloader.engine as engine_module
import types
state,root,identity=sys.argv[1:]
store=Store(Path(state));content=b'synthetic photo bytes'*60000
folder=metadata('folder-root','Archive',mime=FOLDER);photo=metadata('photo','photo.jpg',content)
drive=Drive(store,{'folder-root':(folder,b''),'photo':(photo,content)},{('folder-root',''):{'files':[photo]}})
engine=Engine(store,drive);engine.active=identity
job=store.job(identity);engine._scan(job);store.change_job(identity,status='running')
with store.db() as db:
 row=dict(db.execute('SELECT * FROM files WHERE job=?',(identity,)).fetchone())
 db.execute("UPDATE files SET status='downloading' WHERE job=?",(identity,))
original=drive.quota.record
def record(*args,**kwargs):
 original(*args,**kwargs)
 if kwargs.get('bytes_count'):os._exit(91)
drive.quota.record=record
ticks=itertools.count(100,2)
engine_module.time.monotonic=lambda:next(ticks)
engine_module.shutil.disk_usage=lambda p:types.SimpleNamespace(free=10**12)
engine._transfer(job,row)
os._exit(92)
'''
    result=subprocess.run([sys.executable,'-B','-X','utf8','-c',script,str(state),str(root),identity],cwd=project,
                          env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,timeout=30)
    assert result.returncode==91,result.stderr.decode(errors='replace')
    recovered=Store(state)
    assert recovered.job(identity)['status']=='interrupted'
    with recovered.db() as db:
        row=dict(db.execute('SELECT * FROM files WHERE job=?',(identity,)).fetchone())
        assert 0<row['bytes']<len(content)
        assert row['part_sha']==hashlib.sha256(content[:row['bytes']]).hexdigest()
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    drive.quota.store=recovered
    engine=Engine(recovered,drive)
    assert run(engine,identity)['status']=='complete'
    assert drive.downloads==[('photo',row['bytes'])]
    assert (root/'photo.jpg').read_bytes()==content
