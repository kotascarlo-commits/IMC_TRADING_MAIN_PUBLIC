"""Atomic local JSON projections; never execution-ledger or order retries."""
import json
import os
import stat
import time
import uuid
from pathlib import Path


def write_json_projection(path, value, *, retry_seconds=.25, clock=time.monotonic, sleep=time.sleep):
    path=Path(path)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    created=False; file_identity=None
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            created=True
            info=os.fstat(stream.fileno())
            if info.st_ino > 0:file_identity=(info.st_dev,info.st_ino)
            json.dump(value,stream,sort_keys=True,separators=(',',':'),allow_nan=False)
        deadline=clock()+retry_seconds
        failure=None
        while True:
            # Bound additional attempts; an in-flight filesystem call may take
            # longer than this retry window. Never repeat payload generation.
            if failure is not None and clock() >= deadline:raise failure
            try:
                temporary.replace(path)
                created=False
                return
            except OSError as error:
                if getattr(error,'winerror',None) not in (5,32,33):raise
                if getattr(error,'winerror',None)==5:
                    try:readonly=getattr(path.stat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_READONLY
                    except FileNotFoundError:readonly=False
                    if readonly:raise
                remaining=deadline-clock()
                if remaining <= 0:raise
                failure=error
                sleep(min(.02,remaining))
    finally:
        if created and file_identity is not None:
            try:
                info=temporary.stat(follow_symlinks=False)
                if (info.st_dev,info.st_ino)==file_identity:
                    temporary.unlink()
            except OSError:pass
