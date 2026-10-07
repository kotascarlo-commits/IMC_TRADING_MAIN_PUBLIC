"""Native rename-capable read guards, without maintenance or publication policy.

Read+Delete sharing blocks ordinary writers but permits foreign renames. Every
operation therefore rebinds the held native identity to its expected path. An
owning maintenance controller must retain its duplicate guards and ACTIVE on
any ambiguous result. This module grants no publication or restart authority.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import time
import threading
import math
import uuid
import re
import copy
from contextlib import nullcontext
from storage_publish_deadline import Deadline


_RETAINED_GUARDS = []


class FileID(ctypes.Structure):
    _fields_ = [('volume', ctypes.c_uint64), ('identifier', ctypes.c_ubyte * 16)]


class Standard(ctypes.Structure):
    _fields_ = [('allocation', ctypes.c_int64), ('bytes', ctypes.c_int64),
                ('links', ctypes.c_uint32), ('delete_pending', ctypes.c_ubyte),
                ('directory', ctypes.c_ubyte)]


class IOStatusValue(ctypes.Union):
    _fields_ = [('status', ctypes.c_int32), ('pointer', ctypes.c_void_p)]


class IOStatus(ctypes.Structure):
    _fields_ = [('value', IOStatusValue), ('information', ctypes.c_size_t)]


class RenameGuards:
    def __init__(self, root, members, *, deadline, destinations=(), nonce=None,
                 attached_sentinels=None, attached_directories=None, shared_deadline=None):
        if os.name != 'nt':
            raise OSError('publish_windows_required')
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= time.monotonic():
            raise ValueError('publish_guard_deadline')
        if type(members) is not dict or not members or any(type(k) is not str for k in members):
            raise ValueError('publish_guard_members')
        if shared_deadline is not None and type(shared_deadline) is not Deadline:
            raise ValueError('publish_guard_shared_deadline')
        self._shared_deadline = shared_deadline
        self.root = Path(root).resolve(strict=True)
        self.deadline = deadline
        self.members = {key: {'path': Path(path).absolute(), 'handle': None, 'identity': None}
                        for key, path in members.items()}
        self.close_fault = False
        self.release_attempted = False
        self.release_closed = False
        self.release_states = {}
        self._release_lock = threading.RLock()
        self._release_busy = False
        self._release_fault = threading.Event()
        self.native_operation = None
        self.auxiliary = []
        self.directories = {}
        self.destinations = tuple(Path(path).absolute() for path in destinations)
        self.nonce = uuid.uuid4().hex if nonce is None else nonce
        if type(self.nonce) is not str or re.fullmatch('[a-f0-9]{32}', self.nonce) is None:
            raise ValueError('publish_guard_nonce')
        self.sentinels = {}
        self.attached_sentinels = copy.deepcopy(attached_sentinels)
        self.attached_directories = copy.deepcopy(attached_directories)
        if self.attached_directories is not None:
            if type(self.attached_directories) is not dict or self.attached_sentinels is None:
                raise ValueError('publish_attached_directory_inventory')
            for path, expected in self.attached_directories.items():
                if (type(path) is not str or type(expected) is not dict
                        or set(expected) != {'volume', 'file_id'}
                        or any(type(expected[k]) is not int or expected[k] < 0 for k in expected)
                        or expected['file_id'] == 0):
                    raise ValueError('publish_attached_directory_identity')
        if self.attached_sentinels is not None:
            if type(self.attached_sentinels) is not dict:
                raise ValueError('publish_attached_sentinel_inventory')
            for path, expected in self.attached_sentinels.items():
                if (type(path) is not str or type(expected) is not dict
                        or set(expected) != {'volume', 'file_id', 'bytes'}
                        or any(type(expected[k]) is not int or expected[k] < 0 for k in expected)
                        or expected['file_id'] == 0 or expected['bytes'] != 0):
                    raise ValueError('publish_attached_sentinel_identity')
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                       ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        self.api.CreateFileW.restype = ctypes.c_void_p
        self.api.CloseHandle.argtypes = [ctypes.c_void_p]
        self.api.CloseHandle.restype = ctypes.c_int
        self.api.GetFileInformationByHandleEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                        ctypes.c_void_p, ctypes.c_uint32]
        self.api.GetFileInformationByHandleEx.restype = ctypes.c_int
        self.api.GetFinalPathNameByHandleW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                                    ctypes.c_uint32, ctypes.c_uint32]
        self.api.GetFinalPathNameByHandleW.restype = ctypes.c_uint32
        self.nt = ctypes.WinDLL('ntdll')
        self.nt.NtSetInformationFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        self.nt.NtSetInformationFile.restype = ctypes.c_int32
        self.api.SetFilePointerEx.argtypes = [ctypes.c_void_p, ctypes.c_int64,
                                            ctypes.c_void_p, ctypes.c_uint32]
        self.api.SetFilePointerEx.restype = ctypes.c_int
        self.api.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                     ctypes.c_void_p, ctypes.c_void_p]
        self.api.ReadFile.restype = ctypes.c_int
        self.api.GetVolumeInformationByHandleW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_wchar_p, ctypes.c_uint32]
        self.api.GetVolumeInformationByHandleW.restype = ctypes.c_int

    def check_deadlines(self):
        """Time bounds only, including an already registered native send."""
        if self._shared_deadline is not None:
            self._shared_deadline.check()
        if time.monotonic() >= self.deadline:
            raise TimeoutError('publish_guard_deadline')

    def check(self):
        self.check_deadlines()
        if self.native_operation is not None:
            raise OSError('publish_native_operation_unconfirmed')

    def bounded_path(self, path):
        self.check()
        path = Path(path).absolute()
        try:
            path.relative_to(self.root)
        except ValueError:
            raise ValueError('publish_path_outside_root') from None
        if path == self.root:
            raise ValueError('publish_path_is_root')
        if any(':' in part for part in path.relative_to(self.root).parts):
            raise ValueError('publish_alternate_stream')
        current = path
        while current != self.root:
            if os.path.lexists(current):
                if current.lstat().st_file_attributes & 0x400:
                    raise ValueError('publish_reparse_path')
            elif current != path:
                raise ValueError('publish_parent_absent')
            current = current.parent
        if self.root.lstat().st_file_attributes & 0x400:
            raise ValueError('publish_reparse_root')
        if path.parent.resolve(strict=True) != path.parent:
            raise ValueError('publish_parent_binding')
        return path

    def _open(self, path):
        handle = self.api.CreateFileW(str(path), 0x80010000, 5, None, 3, 0x200080, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def _info(self, handle):
        identifier, standard = FileID(), Standard()
        for kind, info in ((18, identifier), (1, standard)):
            if not self.api.GetFileInformationByHandleEx(handle, kind, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
        if standard.directory or standard.delete_pending or standard.links != 1 or standard.bytes < 0:
            raise ValueError('publish_not_exclusive_regular_file')
        return {'volume': identifier.volume,
                'file_id': int.from_bytes(bytes(identifier.identifier), 'little'),
                'bytes': standard.bytes}

    def _directory_info(self, handle):
        identifier, standard = FileID(), Standard()
        for kind, info in ((18, identifier), (1, standard)):
            if not self.api.GetFileInformationByHandleEx(handle, kind, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
        if not standard.directory or standard.delete_pending:
            raise ValueError('publish_not_directory')
        class Tag(ctypes.Structure):
            _fields_ = [('attributes', ctypes.c_uint32), ('tag', ctypes.c_uint32)]
        tag = Tag()
        if not self.api.GetFileInformationByHandleEx(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)):
            raise ctypes.WinError(ctypes.get_last_error())
        if tag.attributes & 0x400 or tag.tag:
            raise ValueError('publish_native_directory_reparse')
        return {'volume': identifier.volume,
                'file_id': int.from_bytes(bytes(identifier.identifier), 'little')}

    def _pin_directories(self):
        leaves = {m['path'].parent for m in self.members.values()} | {p.parent for p in self.destinations}
        expected_sentinels = {str(path / ('.imc-publish-guard-' + self.nonce + '.sentinel'))
                              for path in leaves}
        if self.attached_sentinels is not None and set(self.attached_sentinels) != expected_sentinels:
            raise ValueError('publish_attached_sentinel_inventory')
        parents = set()
        for path in [m['path'] for m in self.members.values()] + list(self.destinations):
            path = self.bounded_path(path)
            current = path.parent
            while True:
                parents.add(current)
                if current == (Path(self.root.anchor) if self.attached_directories is not None else self.root):
                    break
                current = current.parent
        if (self.attached_directories is not None
                and set(self.attached_directories) != {str(path) for path in parents}):
            raise ValueError('publish_attached_directory_inventory')
        for path in sorted(parents, key=lambda p: len(p.parts)):
            self.check()
            # Initially no Write/Delete sharing. All target parents preexist.
            handle = self.api.CreateFileW(str(path), 0xA0, 1, None, 3, 0x2200000, None)
            if handle in (None, ctypes.c_void_p(-1).value):
                raise ctypes.WinError(ctypes.get_last_error())
            self.directories[path] = {'handle': handle, 'identity': None}
            self.directories[path]['identity'] = self._directory_info(handle)
            if (self.attached_directories is not None
                    and self.directories[path]['identity'] != self.attached_directories[str(path)]):
                raise ValueError('publish_attached_directory_identity')
            system = ctypes.create_unicode_buffer(32)
            if not self.api.GetVolumeInformationByHandleW(handle, None, 0, None, None, None, system, len(system)):
                raise ctypes.WinError(ctypes.get_last_error())
            if system.value != 'NTFS':
                raise ValueError('publish_local_ntfs_required')
            self.check()
        # Nonempty directories cannot become NTFS junctions/symlinks. Keep a
        # fresh zero-byte sentinel without Delete sharing even after original
        # members leave their directories. Files stay retained, never deleted.
        for path in sorted(leaves, key=str):
            sentinel = path / ('.imc-publish-guard-' + self.nonce + '.sentinel')
            self.bounded_path(sentinel)
            # Attach only to the parent's exact generation/held native identity.
            # Default remains CREATE_NEW; there is no exists-based fallback.
            creation = 1 if self.attached_sentinels is None else 3
            handle = self.api.CreateFileW(str(sentinel), 0x80000000, 1, None, creation, 0x200080, None)
            if handle in (None, ctypes.c_void_p(-1).value):
                raise ctypes.WinError(ctypes.get_last_error())
            self.sentinels[sentinel] = {'handle': handle, 'identity': None}
            self.sentinels[sentinel]['identity'] = self._info(handle)
            if self.attached_sentinels is not None and self.sentinels[sentinel]['identity'] != self.attached_sentinels[str(sentinel)]:
                raise ValueError('publish_attached_sentinel_identity')
        # The rename target's internal directory open needs Write sharing.
        # Overlap old and new handles; Delete sharing remains excluded.
        for path in {m['path'].parent for m in self.members.values()} | {p.parent for p in self.destinations}:
            self.check()
            old = self.directories[path]
            new = self.api.CreateFileW(str(path), 0xA0, 3, None, 3, 0x2200000, None)
            if new in (None, ctypes.c_void_p(-1).value):
                raise ctypes.WinError(ctypes.get_last_error())
            self.auxiliary.append(new)
            if self._directory_info(new) != old['identity']:
                raise ValueError('publish_directory_handoff_identity')
            self._close(old['handle'])
            self.directories[path] = {'handle': new, 'identity': old['identity']}
            self.auxiliary.remove(new)

    def _assert_directories(self):
        for path, member in self.directories.items():
            self.check()
            if self._native_path(member['handle']) != os.path.normcase(str(path)):
                raise ValueError('publish_directory_path_changed')
            if self._directory_info(member['handle']) != member['identity']:
                raise ValueError('publish_directory_identity_changed')
        for path, member in self.sentinels.items():
            if (self._native_path(member['handle']) != os.path.normcase(str(path))
                    or self._info(member['handle']) != member['identity']):
                raise ValueError('publish_sentinel_binding_changed')

    def _native_path(self, handle):
        buffer = ctypes.create_unicode_buffer(32768)
        length = self.api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not 0 < length < len(buffer):
            raise OSError('publish_native_path_unknown')
        value = buffer.value
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
        return os.path.normcase(os.path.normpath(value))

    def _close(self, handle):
        try:
            if not self.api.CloseHandle(handle):
                raise OSError('publish_native_close_unconfirmed')
        except BaseException:
            self.close_fault = True
            if self not in _RETAINED_GUARDS:
                _RETAINED_GUARDS.append(self)
            raise OSError('publish_native_close_unconfirmed') from None

    def _acquire(self):
        self._pin_directories()
        observed = set()
        for member in self.members.values():
            member['path'] = self.bounded_path(member['path'])
            member['handle'] = self._open(member['path'])
            member['identity'] = self._info(member['handle'])
            key = (member['identity']['volume'], member['identity']['file_id'])
            if key in observed or observed and key[0] != next(iter(observed))[0]:
                raise ValueError('publish_file_alias_or_volume')
            observed.add(key)
        self.assert_bound()
        return self

    def __enter__(self):
        try:
            return self._acquire()
        except BaseException:
            self.close()
            raise

    def acquire_retained(self):
        """Worker bootstrap only: retain partial acquisition without cleanup.

        Caller must not use this object as a context manager. Native owning
        Root decides Job cleanup; neither a bootstrap error nor process GC is
        a Source-release grant. Failed acquisition is permanently consumed.
        """
        if (getattr(self,'_retained_acquisition',False) or self.close_fault
                or any(m['handle'] is not None for m in self.members.values())
                or self.directories or self.sentinels or self.auxiliary):
            self.close_fault=True
            if self not in _RETAINED_GUARDS:_RETAINED_GUARDS.append(self)
            raise ValueError('publish_retained_acquisition_consumed')
        self._retained_acquisition=True
        if self not in _RETAINED_GUARDS:_RETAINED_GUARDS.append(self)
        try:
            return self._acquire()
        except BaseException:
            self.close_fault=True
            raise

    def assert_bound(self):
        self.check()
        if self.close_fault:
            raise OSError('publish_native_close_unconfirmed')
        self._assert_directories()
        for member in self.members.values():
            path = self.bounded_path(member['path'])
            handle = member['handle']
            if handle is None or self._info(handle) != member['identity']:
                raise ValueError('publish_native_identity_changed')
            if self._native_path(handle) != os.path.normcase(str(path)):
                raise ValueError('publish_native_path_changed')
            fresh = self._open(path)
            self.auxiliary.append(fresh)
            try:
                if self._info(fresh) != member['identity']:
                    raise ValueError('publish_path_identity_changed')
            finally:
                self._close(fresh)
                self.auxiliary.remove(fresh)
        self.check()

    def move(self, key, destination, *, submission=None):
        """No overwrite or blind retry; caller fsyncs intent before invocation."""
        self.assert_bound()
        destination = self.bounded_path(destination)
        if destination.parent not in self.directories:
            raise ValueError('publish_destination_directory_not_pinned')
        if self.destinations and destination not in self.destinations:
            raise ValueError('publish_destination_not_bound')
        if os.path.lexists(destination):
            raise ValueError('publish_destination_exists')
        member = self.members[key]
        source = member['path']
        # Rename this held file, never whichever file occupies a source path
        # after the last binding check. ReplaceIfExists remains false.
        text = destination.name
        units = len(text.encode('utf-16-le')) // 2
        class RenameInfo(ctypes.Structure):
            _fields_ = [('replace', ctypes.c_ubyte), ('root', ctypes.c_void_p),
                        ('length', ctypes.c_uint32), ('name', ctypes.c_wchar * (units + 1))]
        if (RenameInfo.root.offset != 8 or RenameInfo.name.offset != 20
                or ctypes.sizeof(ctypes.c_wchar) != 2 or ctypes.sizeof(IOStatus) != 16
                or IOStatus.information.offset != 8):
            raise ValueError('publish_rename_abi')
        # FILE_RENAME_INFORMATION has trailing structure padding on x64.
        # Include that padding plus all UTF-16 bytes, even for short names.
        buffer = ctypes.create_string_buffer(max(ctypes.sizeof(RenameInfo), 24 + units * 2))
        info = RenameInfo.from_buffer(buffer)
        info.root = self.directories[destination.parent]['handle']
        info.length = len(text.encode('utf-16-le'))
        info.name = text
        completion = IOStatus()
        completion.value.status = -1
        # Retain native memory AND all handles before submission. PENDING or
        # inconsistent completion is not cancellation. Context exit cannot
        # close these resources or authorize a retry; the owning controller
        # must keep its ACTIVE fence and duplicate guards.
        self.native_operation = (buffer, info, completion, source, destination)
        if self not in _RETAINED_GUARDS:
            _RETAINED_GUARDS.append(self)
        # Optional host synchronization orders fault registration against the
        # actual native submission, after all namespace/buffer preparation.
        with (nullcontext() if submission is None else submission()):
            status = self.nt.NtSetInformationFile(member['handle'], ctypes.byref(completion),
                                                  buffer, len(buffer), 10)
        if status != 0 or completion.value.status != 0:
            raise OSError('publish_native_rename_unconfirmed')
        self.native_operation = None
        _RETAINED_GUARDS.remove(self)
        # Track the expected path before a fallible post-check. Failure remains
        # ambiguous to the caller and must keep its ACTIVE fence and guards.
        member['path'] = destination
        if os.path.lexists(source):
            raise ValueError('publish_source_reappeared')
        self.assert_bound()

    def digest(self, key):
        """Hash this held identity; ordinary CRT opens may deny DELETE sharing."""
        self.assert_bound()
        handle = self.members[key]['handle']
        if not self.api.SetFilePointerEx(handle, 0, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        buffer, count = ctypes.create_string_buffer(1048576), ctypes.c_uint32()
        digest, length = hashlib.sha256(), 0
        while True:
            self.check()
            if not self.api.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not count.value:
                break
            length += count.value
            digest.update(buffer.raw[:count.value])
        if length != self.members[key]['identity']['bytes']:
            raise ValueError('publish_hash_length_changed')
        self.assert_bound()
        return digest.hexdigest()

    def close(self):
        if self.native_operation is not None:
            raise OSError('publish_native_operation_unconfirmed')
        if self.close_fault:
            raise OSError('publish_native_close_unconfirmed')
        if self.release_attempted:
            raise OSError('publish_source_release_consumed')
        while self.auxiliary:
            self._close(self.auxiliary[-1])
            self.auxiliary.pop()
        for member in reversed(tuple(self.members.values())):
            if member['handle'] is not None:
                self._close(member['handle'])
                member['handle'] = None
        for member in reversed(tuple(self.directories.values())):
            if member['handle'] is not None:
                self._close(member['handle'])
                member['handle'] = None
        for member in reversed(tuple(self.sentinels.values())):
            if member['handle'] is not None:
                self._close(member['handle'])
                member['handle'] = None
        if self in _RETAINED_GUARDS:
            _RETAINED_GUARDS.remove(self)

    def _remaining_bound(self):
        """Query only still-owned resources; never create a probe handle."""
        self.check()
        if self._release_fault.is_set() or self.close_fault:
            raise OSError('publish_source_release_faulted')
        for group, directory in ((self.members,False),(self.sentinels,False),(self.directories,True)):
            for key,member in group.items():
                if member['handle'] is None:
                    continue
                path=member.get('path',key)
                if (self._native_path(member['handle'])!=os.path.normcase(str(path))
                    or (self._directory_info(member['handle']) if directory else self._info(member['handle']))!=member['identity']):
                    raise ValueError('publish_source_remaining_binding')
        self.check()

    def release_source(self, *, authority_check, submission):
        """One explicit checked release; failures retain remaining/unknown FDs.

        Caller owns serialization of its receive/ACK and independent Parent
        pins. Authority-owned probe closes are outside this Source inventory.
        """
        if not callable(authority_check) or not callable(submission):
            raise ValueError('publish_source_release_authority')
        if not self._release_lock.acquire(blocking=False):
            self._release_fault.set();raise ValueError('publish_source_release_concurrent')
        try:
            if self._release_busy or self.release_attempted:
                self._release_fault.set();raise ValueError('publish_source_release_consumed')
            self._release_busy=True;self.release_attempted=True
            if self not in _RETAINED_GUARDS:_RETAINED_GUARDS.append(self)
            try:
                if self.native_operation is not None or self.auxiliary or self.close_fault:
                    raise OSError('publish_source_release_unconfirmed')
                # File pins first, then leaf sentinels, namespace directories
                # last. Root keeps its independent entire namespace throughout.
                inventory=[]
                for name,group in (('member',self.members),('sentinel',self.sentinels),('directory',self.directories)):
                    for key,member in reversed(tuple(group.items())):
                        if member['handle'] is None:raise ValueError('publish_source_release_inventory')
                        label=name+':'+str(key)
                        inventory.append((label,member));self.release_states[label]='OWNED'
                for label,member in inventory:
                    authority_check();self._remaining_bound();authority_check()
                    with submission():
                        if self._release_fault.is_set():raise ValueError('publish_source_release_faulted')
                        self.release_states[label]='CLOSING'
                        try:
                            self._close(member['handle'])
                            member['handle']=None;self.release_states[label]='CLOSED'
                            # Resource fact survives a later authority/ACK fault.
                            self.release_closed=all(state=='CLOSED' for state in self.release_states.values())
                        except BaseException:
                            self.release_states[label]='UNKNOWN';raise
                    # A confirmed native close stays CLOSED even when its
                    # completion crossed either deadline. Never recast it as
                    # UNKNOWN or admit another close after that violation.
                    self.check()
                self.check();authority_check();self.check()
                with submission():
                    self.check()
                    if self._release_fault.is_set():raise ValueError('publish_source_release_faulted')
                    self.release_closed=True
                _RETAINED_GUARDS.remove(self)
            except BaseException:
                self._release_fault.set();self.close_fault=True
                raise
            finally:self._release_busy=False
        finally:self._release_lock.release()

    def __exit__(self, *args):
        self.close()
