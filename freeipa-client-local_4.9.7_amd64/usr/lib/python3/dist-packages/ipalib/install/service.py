#
# Copyright (C) 2016  FreeIPA Contributors see COPYING for license
#

"""
Base service installer module
"""

from ipalib.util import validate_domain_name
from ipapython.install import common, core, typing
from ipapython.install.core import group, knob


def prepare_only(obj):
    """
    Decorator which makes an installer attribute appear only in the prepare
    phase of the install
    """
    obj.__exclude__ = getattr(obj, '__exclude__', set()) | {'enroll'}
    return obj


def enroll_only(obj):
    """
    Decorator which makes an installer attribute appear only in the enroll
    phase of the install
    """
    obj.__exclude__ = getattr(obj, '__exclude__', set()) | {'prepare'}
    return obj


def master_install_only(obj):
    """
    Decorator which makes an installer attribute appear only in master install
    """
    obj.__exclude__ = getattr(obj, '__exclude__', set()) | {'replica_install'}
    return obj


def replica_install_only(obj):
    """
    Decorator which makes an installer attribute appear only in replica install
    """
    obj.__exclude__ = getattr(obj, '__exclude__', set()) | {'master_install'}
    return obj


def _does(cls, arg):
    def remove(name):
        def removed(self):
            raise AttributeError(name)

        return property(removed)

    return type(
        cls.__name__,
        (cls,),
        {
            n: remove(n) for n in dir(cls)
            if arg in getattr(getattr(cls, n), '__exclude__', set())
        }
    )


def prepares(cls):
    """
    Returns installer class stripped of attributes not related to the prepare
    phase of the install
    """
    return _does(cls, 'prepare')


def enrolls(cls):
    """
    Returns installer class stripped of attributes not related to the enroll
    phase of the install
    """
    return _does(cls, 'enroll')


def installs_master(cls):
    """
    Returns installer class stripped of attributes not related to master
    install
    """
    return _does(cls, 'master_install')


def installs_replica(cls):
    """
    Returns installer class stripped of attributes not related to replica
    install
    """
    return _does(cls, 'replica_install')


@group
class ServiceInstallInterface(common.Installable,
                              common.Interactive,
                              core.Composite):
    """
    Interface common to all service installers
    """
    description = "Basic"

    domain_name = knob(
        str, None,
        description="primary DNS domain of the IPA deployment "
                    "(not necessarily related to the current hostname)",
        cli_names='--domain',
    )

    @domain_name.validator
    def domain_name(self, value):
        validate_domain_name(value)

    servers = knob(
        # pylint: disable=invalid-sequence-index
        typing.List[str], None,
        description="FQDN of IPA server",
        cli_names='--server',
        cli_metavar='SERVER',
    )

    realm_name = knob(
        str, None,
        description="Kerberos realm name of the IPA deployment (typically "
                    "an upper-cased name of the primary DNS domain)",
        cli_names='--realm',
    )

    @realm_name.validator
    def realm_name(self, value):
        validate_domain_name(value, entity="realm")

    host_name = knob(
        str, None,
        description="The hostname of this machine (FQDN). If specified, the "
                    "hostname will be set and the system configuration will "
                    "be updated to persist over reboot. By default the result "
                    "of getfqdn() call from Python's socket module is used.",
        cli_names='--hostname',
    )

    ca_cert_files = knob(
        # pylint: disable=invalid-sequence-index
        typing.List[str], None,
        description="load the CA certificate from this file",
        cli_names='--ca-cert-file',
        cli_metavar='FILE',
    )

    dm_password = knob(
        str, None,
        sensitive=True,
        description="Directory Manager password (for the existing master)",
    )


class ServiceAdminInstallInterface(ServiceInstallInterface):
    """
    Interface common to all service installers which require admin user
    authentication
    """

    principal = knob(
        str, None,
    )
    principal = enroll_only(principal)
    principal = replica_install_only(principal)

    admin_password = knob(
        str, None,
        sensitive=True,
    )
    admin_password = enroll_only(admin_password)
