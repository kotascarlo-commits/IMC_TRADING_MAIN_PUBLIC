"""Native inventory roundtrip only, no ParentAuthority or Source publication."""
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from storage_publish_parent import ParentReadGuards
from storage_publish_deadline import Deadline
from storage_publish_manifest import Journal

binding_path,root,case=sys.argv[1:]
data=json.loads(Path(binding_path).read_bytes())
directory=Path(root)/'journal'
sentinel=directory/('.imc-publish-guard-'+data['nonce']+'.sentinel')
directories=data['directories'];sentinels=data['sentinels']
if case=='WrongAncestor':directories[str(Path(root).parent)]['file_id']+=1
elif case=='MissingAncestor':directories.pop(str(Path(root).parent))
elif case=='ExtraAncestor':directories[str(Path(root)/'unregistered')]={'volume':1,'file_id':1}
elif case=='WrongSentinel':sentinels[str(sentinel)]['file_id']+=1
deadline=Deadline(data['end_tick'],data['frequency'])
guard=ParentReadGuards(root,{'sentinel':sentinel},deadline=time.monotonic()+10,
    nonce=data['nonce'],attached_directories=directories,attached_sentinels=sentinels,
    shared_deadline=deadline)
try:
    guard.__enter__()
    guard.assert_bound()
    assert set(guard.directories)=={Path(path) for path in directories}
    assert set(next(iter(directories.values())))=={'volume','file_id'}
    identity=dict(guard.members['sentinel']['identity'])
    identity['sha256']=hashlib.sha256(b'').hexdigest()
    # Manifest generation remains synthetic; this test proves namespace/header
    # handling, never Source evidence or native maintenance ownership.
    value=data['generation']
    journal=Journal(directory,value,project_root=root,schema_version=2,prepared_sentinel=identity)
    assert journal.next_step==0 and journal.pending is None
    guard.assert_bound();guard.close()
    assert case=='Normal'
except ValueError as error:
    codes={'WrongAncestor':'publish_attached_directory_identity',
        'MissingAncestor':'publish_attached_directory_inventory',
        'ExtraAncestor':'publish_attached_directory_inventory',
        'WrongSentinel':'publish_attached_sentinel_identity',
        'ExtraInventory':'publish_journal_prepared_inventory',
        'ExistingHeader':'publish_journal_prepared_inventory'}
    assert str(error)==codes[case]
    if case in ('ExtraInventory','ExistingHeader'):guard.close()
print('NATIVE_INVENTORY_ROUNDTRIP_PASS')
