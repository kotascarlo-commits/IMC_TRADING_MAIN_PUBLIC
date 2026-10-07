"""Read-only parent file/directory pins for isolated publication handoff.

No CLI, mutex/generation authority, worker lifecycle or production controller.
The caller retains this object and ACTIVE on failure. These handles have READ
access only; they must never be duplicates of a worker's DELETE handles.
"""
import copy
import ctypes
import os

from storage_publish_guard import RenameGuards
from windows_read_guard import ReadFiles


class ParentReadGuards(RenameGuards):
    def _open(self, path):
        handle = self.api.CreateFileW(str(path), 0x80000000, 5, None, 3, 0x200080, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def move(self, key, destination):
        raise ValueError('publish_parent_has_no_move_authority')

    @property
    def sentinel_bindings(self):
        self.assert_bound()
        return {str(path): copy.deepcopy(member['identity'])
                for path, member in self.sentinels.items()}

    def rebind(self, expected_paths):
        """Bind held original identities to parent-approved post-move paths.

        Native paths never supply authority for the expected mapping. The
        caller obtains it from its reviewed generation and durable journal.
        """
        self.check()
        self._assert_directories()
        if type(expected_paths) is not dict or set(expected_paths) != set(self.members):
            raise ValueError('publish_parent_handoff_inventory')
        approved = {}
        for key, text in expected_paths.items():
            path = self.bounded_path(text)
            member = self.members[key]
            if (path != member['path'] and path not in self.destinations
                    or self._info(member['handle']) != member['identity']
                    or self._native_path(member['handle']) != os.path.normcase(str(path))):
                raise ValueError('publish_parent_handoff_identity')
            approved[key] = path
        for key, path in approved.items():
            self.members[key]['path'] = path
        self.assert_bound()

    def acquire_strong(self, expected_paths):
        """Worker DELETE handles must already be checked-closed/exited.

        A sharing conflict fails closed. This method does not infer worker exit
        from success; the native owning controller proves that independently.
        """
        self.rebind(expected_paths)
        strong = ReadFiles([m['path'] for m in self.members.values()], deadline=self.deadline)
        strong.__enter__()
        try:
            for member, evidence in zip(self.members.values(), strong.evidence):
                if any(evidence[k] != member['identity'][k] for k in ('volume', 'file_id')):
                    raise ValueError('publish_parent_strong_identity')
            self.assert_bound()
            return strong
        except BaseException:
            strong.close()  # Weak parent guards remain held throughout.
            raise

    def close_files_after_strong(self, strong):
        """Keep directories/sentinels pinned until the caller's final release."""
        self.assert_bound()
        if (type(strong) is not ReadFiles or strong.close_fault
                or len(strong.handles) != len(strong.paths)
                or set(strong.paths) != {m['path'] for m in self.members.values()}):
            raise ValueError('publish_parent_strong_missing')
        for member in self.members.values():
            evidence = next(e for e in strong.evidence if e['path'] == str(member['path']))
            if any(evidence[k] != member['identity'][k] for k in ('volume', 'file_id')):
                raise ValueError('publish_parent_strong_identity')
            handle = strong.handles[strong.paths.index(member['path'])]
            if (self._info(handle) != member['identity']
                    or self._native_path(handle) != os.path.normcase(str(member['path']))):
                raise ValueError('publish_parent_strong_native_proof')
        for member in self.members.values():
            self._close(member['handle'])
            member['handle'] = None
        self._assert_directories()
