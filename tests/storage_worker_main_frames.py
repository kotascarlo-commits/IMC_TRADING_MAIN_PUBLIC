"""Canonical synthetic fixture frames, not publication authority."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from storage_publish_manifest import encoded, STEPS
from storage_publish_writer import digest

value=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
mode=sys.argv[2]
if mode=='boot':
    binding=value['binding']
    binding['generation_sha256']=digest(encoded(value['generation']))
    binding['envelope_sha256']=digest(encoded({k:v for k,v in value.items() if k!='binding'}))
elif mode=='journal':
    i=value.pop('index');generation=value.pop('generation')
    paths,members=generation['paths'],generation['members']
    if i<8:
        source,target=(('source_shm','archive_shm'),('source_wal','archive_wal'),
                       ('source_main','archive_main'),('work_main','installed_main'))[i//2]
        proof={'source':paths[source],'destination':paths[target],'identity':members[source]}
        if i%2:proof.update(source_absent=True,destination_absent=members[source] is None)
    else:
        proof={'installed_main':paths['installed_main'],'identity':members['work_main']}
        if i==9:proof.update(integrity=True,schema=True,canonical_sidecars_absent=True)
        if i==11:proof.update(source_guard_cleanup=True,integrity=True,safety=True,absence=True)
    value.update(schema_version=2,kind='JOURNAL_REQUEST',ipc_sequence=i+1,journal_sequence=i,
                 step=STEPS[i//2],event_kind='DONE' if i%2 else 'INTENT',evidence=proof)
sys.stdout.write(encoded(value).decode('utf-8'))
