#
# Copyright (C) 2018  FreeIPA Contributors see COPYING for license
#
"""Distribution information

Known Linux distros with /etc/os-release
----------------------------------------

- alpine
- centos (like rhel, fedora)
- debian
- fedora
- rhel
- ubuntu (like debian)

The platform ids for ipaplatform providers are based on:

1) IPAPLATFORM_OVERRIDE env var
2) ipaplatform.override.OVERRIDE value
3) ID field of /etc/os-release (Linux)
4) ID_LIKE fields of /etc/os-release (Linux)
"""
from __future__ import absolute_import

from collections.abc import Mapping
import importlib
import re
import os
import sys
import warnings


import ipaplatform
try:
    from ipaplatform.override import OVERRIDE
except ImportError:
    OVERRIDE = None


_osrelease_line = re.compile(
    u"^(?!#)(?P<name>[a-zA-Z0-9_]+)="
    u"(?P<quote>[\"\']?)(?P<value>.+)(?P=quote)$"
)


def _parse_osrelease(filename='/etc/os-release'):
    """Parser for /etc/os-release for Linux distributions

    https://www.freedesktop.org/software/systemd/man/os-release.html
    """
    release = {}
    with open(filename) as f:
        for line in f:
            mo = _osrelease_line.match(line)
            if mo is not None:
                release[mo.group('name')] = mo.group('value')
    if 'ID_LIKE' in release:
        release['ID_LIKE'] = tuple(
            v.strip()
            for v in release['ID_LIKE'].split(' ')
            if v.strip()
        )
    else:
        release["ID_LIKE"] = ()
    # defaults
    release.setdefault('NAME', 'Linux')
    release.setdefault('ID', 'linux')
    release.setdefault('VERSION', '')
    release.setdefault('VERSION_ID', '')
    return release


class OSInfo(Mapping):
    __slots__ = ('_info', '_platform', '_container')

    bsd_family = (
        'freebsd',
        'openbsd',
        'netbsd',
        'dragonfly',
        'gnukfreebsd'
    )

    def __init__(self):
        if sys.platform.startswith('linux'):
            # Linux, get distribution from /etc/os-release
            info = self._handle_linux()
        elif sys.platform == 'win32':
            info = self._handle_win32()
        elif sys.platform == 'darwin':
            info = self._handle_darwin()
        elif sys.platform.startswith(self.bsd_family):
            info = self._handle_bsd()
        else:
            raise ValueError("Unsupported platform: {}".format(sys.platform))
        self._info = info
        self._platform = None
        self._container = None

    def _handle_linux(self):
        """Detect Linux distribution from /etc/os-release
        """
        try:
            return _parse_osrelease()
        except Exception as e:
            warnings.warn("Failed to read /etc/os-release: {}".format(e))
            return {
                'NAME': 'Linux',
                'ID': 'linux',
            }

    def _handle_win32(self):
        """Windows 32 or 64bit platform
        """
        return {
            'NAME': 'Windows',
            'ID': 'win32',
        }

    def _handle_darwin(self):
        """Handle macOS / Darwin platform
        """
        return {
            'NAME': 'macOS',
            'ID': 'macos',
        }

    def _handle_bsd(self):
        """Handle BSD-like platforms
        """
        platform = sys.platform
        simple = platform.rstrip('0123456789')
        id_like = []
        if simple != platform:
            id_like.append(simple)
        return {
            'NAME': platform,
            'ID': platform,
            'ID_LIKE': tuple(id_like),
        }

    def __getitem__(self, item):
        return self._info[item]

    def __iter__(self):
        return iter(self._info)

    def __len__(self):
        return len(self._info)

    @property
    def name(self):
        """OS name (user)
        """
        return self._info['NAME']

    @property
    def id(self):
        """Lower case OS identifier
        """
        return self._info['ID']

    @property
    def id_like(self):
        """Related / similar OS
        """
        return self._info.get('ID_LIKE', ())

    @property
    def version(self):
        """Version number and name of OS (for user)
        """
        return self._info.get('VERSION')

    @property
    def version_number(self):
        """Version number tuple based on version_id
        """
        version_id = self._info.get('VERSION_ID')
        if not version_id:
            return ()
        return tuple(int(p) for p in version_id.split('.'))

    @property
    def platform_ids(self):
        """Ordered tuple of detected platforms (including override)
        """
        platforms = []
        # env var first
        env = os.environ.get("IPAPLATFORM_OVERRIDE")
        if env:
            platforms.append(env)
        # override from package definition
        if OVERRIDE is not None and OVERRIDE not in platforms:
            # allow RPM and Debian packages to override platform
            platforms.append(OVERRIDE)
        if self.id not in platforms:
            platforms.append(self.id)
        platforms.extend(self.id_like)
        return tuple(platforms)

    @property
    def platform(self):
        if self._platform is not None:
            return self._platform
        for platform in self.platform_ids:
            try:
                importlib.import_module('ipaplatform.{}'.format(platform))
            except ImportError:
                pass
            else:
                self._platform = platform
                return platform
        raise ImportError('No ipaplatform available for "{}"'.format(
                          ', '.join(self.platform_ids)))

    @property
    def container(self):
        if self._container is not None:
            return self._container
        from ipaplatform.tasks import tasks
        try:
            self._container = tasks.detect_container()
        except NotImplementedError:
            raise NotImplementedError(
                'Platform does not support detecting containers')
        return self._container

osinfo = OSInfo()
ipaplatform.NAME = osinfo.platform

if __name__ == '__main__':
    import pprint
    pprint.pprint(dict(osinfo))
