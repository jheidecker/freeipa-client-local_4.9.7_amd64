# Authors: Martin Basti <mbasti@redhat.com>
#
# Copyright (C) 2007-2014  Red Hat
# see file 'COPYING' for use and warranty information
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import copy
import logging
import operator
import random

import dns.name
import dns.exception
import dns.resolver
import dns.rdataclass
import dns.rdatatype
import dns.reversename


import six

from ipapython.ipautil import UnsafeIPAddress

if six.PY3:
    unicode = str

logger = logging.getLogger(__name__)


ipa_resolver = None


def get_ipa_resolver():
    global ipa_resolver
    if ipa_resolver is None:
        ipa_resolver = DNSResolver()
    return ipa_resolver


def resolve(*args, **kwargs):
    return get_ipa_resolver().resolve(*args, **kwargs)


def resolve_address(*args, **kwargs):
    return get_ipa_resolver().resolve_address(*args, **kwargs)


def zone_for_name(*args, **kwargs):
    if "resolver" not in kwargs:
        kwargs["resolver"] = get_ipa_resolver()

    return dns.resolver.zone_for_name(*args, **kwargs)


def reset_default_resolver():
    """Re-initialize ipa resolver.
    """
    global ipa_resolver
    ipa_resolver = DNSResolver()


class DNSResolver(dns.resolver.Resolver):
    """DNS stub resolver compatible with both dnspython < 2.0.0
    and dnspython >= 2.0.0.

    Set `use_search_by_default` attribute to `True`, which
    determines the default for whether the search list configured
    in the system's resolver configuration is used for relative
    names, and whether the resolver's domain may be added to relative
    names.

    Increase the default lifetime which determines the number of seconds
    to spend trying to get an answer to the question. dnspython 2.0.0
    changes this to 5sec, while the previous one was 30sec.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reset_ipa_defaults()
        self.resolve = getattr(super(), "resolve", self.query)
        self.resolve_address = getattr(
            super(),
            "resolve_address",
            self._resolve_address
        )

    def reset_ipa_defaults(self):
        """
        BIND's default timeout for resolver is 10sec.
        If that changes then it causes Timeout (instead of SERVFAIL)
        exception for dnspython if BIND under high load. So, let's make
        it the same + operation time.

        dnspython default is 2sec
        """
        self.timeout = 10 + 2

        # dnspython default is 5sec
        self.lifetime = min(self.timeout * len(self.nameservers) * 2, 45)
        self.use_search_by_default = True

    def reset(self):
        super().reset()
        self.reset_ipa_defaults()

    def _resolve_address(self, ip_address, *args, **kwargs):
        """Query nameservers for PTR records.

        :param ip_address: IPv4 or IPv6 address
        :type ip_address: str
        """
        return self.resolve(
            dns.reversename.from_address(ip_address),
            rdtype=dns.rdatatype.PTR,
            *args,
            **kwargs,
        )

    def read_resolv_conf(self, *args, **kwargs):
        """
        dnspython tries nameservers sequentially(not parallel).
        IPA controlled BIND always listen on IPv6 if available,
        so no need to send requests to both IPv4 and IPv6 endpoints
        of the same NS(though BIND handles this).
        """
        super().read_resolv_conf(*args, **kwargs)
        # deduplicate
        nameservers = list(dict.fromkeys(self.nameservers))
        ipv6_loopback = "::1"
        ipv4_loopback = "127.0.0.1"
        if ipv6_loopback in nameservers and ipv4_loopback in nameservers:
            nameservers.remove(ipv4_loopback)
        self.nameservers = nameservers


class DNSZoneAlreadyExists(dns.exception.DNSException):
    supp_kwargs = {'zone', 'ns'}
    fmt = (u"DNS zone {zone} already exists in DNS "
           "and is handled by server(s): {ns}")


@six.python_2_unicode_compatible
class DNSName(dns.name.Name):
    labels = None  # make pylint happy

    @classmethod
    def from_text(cls, labels, origin=None):
        return cls(dns.name.from_text(labels, origin))

    def __init__(self, labels, origin=None):
        try:
            if isinstance(labels, str):
                #pylint: disable=E1101
                labels = dns.name.from_text(unicode(labels), origin).labels
            elif isinstance(labels, dns.name.Name):
                labels = labels.labels

            super(DNSName, self).__init__(labels)
        except UnicodeError as e:
            # dnspython bug, an invalid domain name returns the UnicodeError
            # instead of a dns.exception
            raise dns.exception.SyntaxError(e)

    def __bool__(self):
        #dns.name.from_text('@') is represented like empty tuple
        #we need to acting '@' as nonzero value
        return True

    __nonzero__ = __bool__  # for Python 2

    def __copy__(self):
        return DNSName(self.labels)

    def __deepcopy__(self, memo):
        return DNSName(copy.deepcopy(self.labels, memo))

    def __str__(self):
        return self.to_unicode()

    # method ToASCII named by RFC 3490 and python standard library
    if six.PY2:
        def ToASCII(self):
            # must be unicode string in Py2
            return self.to_text().decode('ascii')
    else:
        def ToASCII(self):
            return self.to_text()

    def canonicalize(self):
        return DNSName(super(DNSName, self).canonicalize())

    def concatenate(self, other):
        return DNSName(super(DNSName, self).concatenate(other))

    def relativize(self, origin):
        return DNSName(super(DNSName, self).relativize(origin))

    def derelativize(self, origin):
        return DNSName(super(DNSName, self).derelativize(origin))

    def choose_relativity(self, origin=None, relativize=True):
        return DNSName(super(DNSName, self).choose_relativity(origin=origin,
                       relativize=relativize))

    def make_absolute(self):
        return self.derelativize(self.root)

    def is_idn(self):
        return any(label.startswith('xn--') for label in self.labels)

    def is_ip4_reverse(self):
        return self.is_subdomain(self.ip4_rev_zone)

    def is_ip6_reverse(self):
        return self.is_subdomain(self.ip6_rev_zone)

    def is_reverse(self):
        return self.is_ip4_reverse() or self.is_ip6_reverse()

    def is_empty(self):
        return len(self.labels) == 0


#DNS public constants
DNSName.root = DNSName(dns.name.root)  # '.'
DNSName.empty = DNSName(dns.name.empty)  # '@'
DNSName.ip4_rev_zone = DNSName(('in-addr', 'arpa', ''))
DNSName.ip6_rev_zone = DNSName(('ip6', 'arpa', ''))

# Empty zones are defined in various RFCs. BIND is by default serving them.
# This constat should contain everything listed in
# IANA registry "Locally-Served DNS Zones"
# URL: http://www.iana.org/assignments/locally-served-dns-zones
# + AS112 zone defined in RFC 7534. It is not in the registry for some
# reason but BIND 9.10 is serving it as automatic empty zones.
EMPTY_ZONES = [DNSName(aez).make_absolute() for aez in [
        # RFC 1918
        "10.IN-ADDR.ARPA", "16.172.IN-ADDR.ARPA", "17.172.IN-ADDR.ARPA",
        "18.172.IN-ADDR.ARPA", "19.172.IN-ADDR.ARPA", "20.172.IN-ADDR.ARPA",
        "21.172.IN-ADDR.ARPA", "22.172.IN-ADDR.ARPA", "23.172.IN-ADDR.ARPA",
        "24.172.IN-ADDR.ARPA", "25.172.IN-ADDR.ARPA", "26.172.IN-ADDR.ARPA",
        "27.172.IN-ADDR.ARPA", "28.172.IN-ADDR.ARPA", "29.172.IN-ADDR.ARPA",
        "30.172.IN-ADDR.ARPA", "31.172.IN-ADDR.ARPA", "168.192.IN-ADDR.ARPA",
        # RFC 6598
        "64.100.IN-ADDR.ARPA", "65.100.IN-ADDR.ARPA", "66.100.IN-ADDR.ARPA",
        "67.100.IN-ADDR.ARPA", "68.100.IN-ADDR.ARPA", "69.100.IN-ADDR.ARPA",
        "70.100.IN-ADDR.ARPA", "71.100.IN-ADDR.ARPA", "72.100.IN-ADDR.ARPA",
        "73.100.IN-ADDR.ARPA", "74.100.IN-ADDR.ARPA", "75.100.IN-ADDR.ARPA",
        "76.100.IN-ADDR.ARPA", "77.100.IN-ADDR.ARPA", "78.100.IN-ADDR.ARPA",
        "79.100.IN-ADDR.ARPA", "80.100.IN-ADDR.ARPA", "81.100.IN-ADDR.ARPA",
        "82.100.IN-ADDR.ARPA", "83.100.IN-ADDR.ARPA", "84.100.IN-ADDR.ARPA",
        "85.100.IN-ADDR.ARPA", "86.100.IN-ADDR.ARPA", "87.100.IN-ADDR.ARPA",
        "88.100.IN-ADDR.ARPA", "89.100.IN-ADDR.ARPA", "90.100.IN-ADDR.ARPA",
        "91.100.IN-ADDR.ARPA", "92.100.IN-ADDR.ARPA", "93.100.IN-ADDR.ARPA",
        "94.100.IN-ADDR.ARPA", "95.100.IN-ADDR.ARPA", "96.100.IN-ADDR.ARPA",
        "97.100.IN-ADDR.ARPA", "98.100.IN-ADDR.ARPA", "99.100.IN-ADDR.ARPA",
        "100.100.IN-ADDR.ARPA", "101.100.IN-ADDR.ARPA",
        "102.100.IN-ADDR.ARPA", "103.100.IN-ADDR.ARPA",
        "104.100.IN-ADDR.ARPA", "105.100.IN-ADDR.ARPA",
        "106.100.IN-ADDR.ARPA", "107.100.IN-ADDR.ARPA",
        "108.100.IN-ADDR.ARPA", "109.100.IN-ADDR.ARPA",
        "110.100.IN-ADDR.ARPA", "111.100.IN-ADDR.ARPA",
        "112.100.IN-ADDR.ARPA", "113.100.IN-ADDR.ARPA",
        "114.100.IN-ADDR.ARPA", "115.100.IN-ADDR.ARPA",
        "116.100.IN-ADDR.ARPA", "117.100.IN-ADDR.ARPA",
        "118.100.IN-ADDR.ARPA", "119.100.IN-ADDR.ARPA",
        "120.100.IN-ADDR.ARPA", "121.100.IN-ADDR.ARPA",
        "122.100.IN-ADDR.ARPA", "123.100.IN-ADDR.ARPA",
        "124.100.IN-ADDR.ARPA", "125.100.IN-ADDR.ARPA",
        "126.100.IN-ADDR.ARPA", "127.100.IN-ADDR.ARPA",
        # RFC 5735 and RFC 5737
        "0.IN-ADDR.ARPA", "127.IN-ADDR.ARPA", "254.169.IN-ADDR.ARPA",
        "2.0.192.IN-ADDR.ARPA", "100.51.198.IN-ADDR.ARPA",
        "113.0.203.IN-ADDR.ARPA", "255.255.255.255.IN-ADDR.ARPA",
        # Local IPv6 Unicast Addresses
        "0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.IP6.ARPA",
        "1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.IP6.ARPA",
        # LOCALLY ASSIGNED LOCAL ADDRESS SCOPE
        "D.F.IP6.ARPA", "8.E.F.IP6.ARPA", "9.E.F.IP6.ARPA", "A.E.F.IP6.ARPA",
        "B.E.F.IP6.ARPA",
        # Example Prefix, RFC 3849.
        "8.B.D.0.1.0.0.2.IP6.ARPA",
        # RFC 7534
        "EMPTY.AS112.ARPA",
    ]]


def assert_absolute_dnsname(name):
    """Raise AssertionError if name is not DNSName or is not absolute.

    >>> assert_absolute_dnsname(DNSName('absolute.name.example.'))
    >>> assert_absolute_dnsname(DNSName('relative.name.example'))
    Traceback (most recent call last):
      ...
    AssertionError: name must be absolute, ...
    >>> assert_absolute_dnsname('absolute.string.example.')
    Traceback (most recent call last):
      ...
    AssertionError: name must be DNSName instance, ...
    """

    assert isinstance(name, DNSName), ("name must be DNSName instance, "
                                       "got '%s'" % type(name))
    assert name.is_absolute(), "name must be absolute, got '%s'" % name


def is_auto_empty_zone(zone):
    """True if specified zone name exactly matches an automatic empty zone.

    >>> is_auto_empty_zone(DNSName('in-addr.arpa.'))
    False
    >>> is_auto_empty_zone(DNSName('10.in-addr.arpa.'))
    True
    >>> is_auto_empty_zone(DNSName('1.10.in-addr.arpa.'))
    False
    >>> is_auto_empty_zone(DNSName('10.in-addr.arpa'))
    Traceback (most recent call last):
      ...
    AssertionError: ...
    """
    assert_absolute_dnsname(zone)
    return zone in EMPTY_ZONES


def inside_auto_empty_zone(name):
    """True if specified absolute name is a subdomain of an automatic empty
    zone.

    DNS domain is a subdomain of itself so this function
    returns True for zone apexes, too.

    >>> inside_auto_empty_zone(DNSName('in-addr.arpa.'))
    False
    >>> inside_auto_empty_zone(DNSName('10.in-addr.arpa.'))
    True
    >>> inside_auto_empty_zone(DNSName('1.10.in-addr.arpa.'))
    True
    >>> inside_auto_empty_zone(DNSName('1.10.in-addr.arpa'))
    Traceback (most recent call last):
      ...
    AssertionError: ...
    """
    assert_absolute_dnsname(name)
    for aez in EMPTY_ZONES:
        if name.is_subdomain(aez):
            return True
    return False


def related_to_auto_empty_zone(name):
    """True if specified absolute name is a sub/superdomain of an automatic
    empty zone.

    DNS domain is a subdomain of itself so this function
    returns True for zone apexes, too.

    >>> related_to_auto_empty_zone(DNSName('.'))
    True
    >>> related_to_auto_empty_zone(DNSName('in-addr.arpa.'))
    True
    >>> related_to_auto_empty_zone(DNSName('10.in-addr.arpa.'))
    True
    >>> related_to_auto_empty_zone(DNSName('1.10.in-addr.arpa.'))
    True
    >>> related_to_auto_empty_zone(DNSName('unrelated.example.'))
    False
    >>> related_to_auto_empty_zone(DNSName('1.10.in-addr.arpa'))
    Traceback (most recent call last):
      ...
    AssertionError: ...
    """
    assert_absolute_dnsname(name)
    relations = {dns.name.NAMERELN_SUBDOMAIN,
                 dns.name.NAMERELN_EQUAL,
                 dns.name.NAMERELN_SUPERDOMAIN}
    return any(name.fullcompare(aez)[0] in relations
               for aez in EMPTY_ZONES)


def has_empty_zone_addresses(hostname):
    """Detect if given host is using IP address belonging to
    an automatic empty zone.

    Information from --ip-address option used in installed is lost by
    the time when upgrade is run. Use IP addresses from DNS as best
    approximation.

    This is brain-dead and duplicates logic from DNS installer
    but I did not find other way around.
    """
    ip_addresses = resolve_ip_addresses(hostname)
    return any(
        inside_auto_empty_zone(DNSName(ip.reverse_dns))
        for ip in ip_addresses
    )


def resolve_rrsets(fqdn, rdtypes):
    """
    Get Resource Record sets for given FQDN.
    CNAME chain is followed during resolution
    but CNAMEs are not returned in the resulting rrset.

    :returns:
        set of dns.rrset.RRset objects, can be empty
        if the FQDN does not exist or if none of rrtypes exist
    """
    # empty set of rdtypes would always return empty set of rrsets
    assert rdtypes, "rdtypes must not be empty"

    if not isinstance(fqdn, DNSName):
        fqdn = DNSName(fqdn)

    fqdn = fqdn.make_absolute()
    rrsets = []
    for rdtype in rdtypes:
        try:
            answer = resolve(fqdn, rdtype)
            logger.debug('found %d %s records for %s: %s',
                         len(answer),
                         rdtype,
                         fqdn,
                         ' '.join(str(rr) for rr in answer))
            rrsets.append(answer.rrset)
        except dns.resolver.NXDOMAIN as ex:
            logger.debug('%s', ex)
            break  # no such FQDN, do not iterate
        except dns.resolver.NoAnswer as ex:
            logger.debug('%s', ex)  # record type does not exist for given FQDN
        except dns.exception.DNSException as ex:
            logger.error('DNS query for %s %s failed: %s', fqdn, rdtype, ex)
            raise

    return rrsets


def resolve_ip_addresses(fqdn):
    """Get IP addresses from DNS A/AAAA records for given host (using DNS).
    :returns:
        list of IP addresses as UnsafeIPAddress objects
    """
    rrsets = resolve_rrsets(fqdn, ['A', 'AAAA'])
    ip_addresses = set()
    for rrset in rrsets:
        ip_addresses.update({UnsafeIPAddress(ip) for ip in rrset})
    return ip_addresses


def check_zone_overlap(zone, raise_on_error=True):
    logger.info("Checking DNS domain %s, please wait ...", zone)
    if not isinstance(zone, DNSName):
        zone = DNSName(zone).make_absolute()

    # automatic empty zones always exist so checking them is pointless,
    # do not report them to avoid meaningless error messages
    if is_auto_empty_zone(zone):
        return

    try:
        containing_zone = zone_for_name(zone)
    except dns.exception.DNSException as e:
        msg = ("DNS check for domain %s failed: %s." % (zone, e))
        if raise_on_error:
            if isinstance(e, dns.resolver.NoNameservers):
                # Show warning and continue in case we've got SERVFAIL
                # because we are supposedly going to create this reverse zone
                logger.warning('%s', msg)
                return
            else:
                raise ValueError(msg)
        else:
            logger.warning('%s', msg)
            return

    if containing_zone == zone:
        try:
            ns = [ans.to_text() for ans in resolve(zone, 'NS')]
        except dns.exception.DNSException as e:
            logger.debug("Failed to resolve nameserver(s) for domain %s: %s",
                         zone, e)
            ns = []

        raise DNSZoneAlreadyExists(zone=zone.to_text(), ns=ns)


def _mix_weight(records):
    """Weighted population sorting for records with same priority
    """
    # trivial case
    if len(records) <= 1:
        return records

    # Optimization for common case: If all weights are the same (e.g. 0),
    # just shuffle the records, which is about four times faster.
    if all(rr.weight == records[0].weight for rr in records):
        random.shuffle(records)
        return records

    noweight = 0.01  # give records with 0 weight a small chance
    result = []
    records = set(records)
    while len(records) > 1:
        # Compute the sum of the weights of those RRs. Then choose a
        # uniform random number between 0 and the sum computed (inclusive).
        urn = random.uniform(0, sum(rr.weight or noweight for rr in records))
        # Select the RR whose running sum value is the first in the selected
        # order which is greater than or equal to the random number selected.
        acc = 0.
        for rr in records.copy():
            acc += rr.weight or noweight
            if acc >= urn:
                records.remove(rr)
                result.append(rr)
    if records:
        result.append(records.pop())
    return result


def sort_prio_weight(records):
    """RFC 2782 sorting algorithm for SRV and URI records

    RFC 2782 defines a sorting algorithms for SRV records, that is also used
    for sorting URI records. Records are sorted by priority and than randomly
    shuffled according to weight.

    This implementation also removes duplicate entries.
    """
    # order records by priority
    records = sorted(records, key=operator.attrgetter("priority"))

    # remove duplicate entries
    uniquerecords = []
    seen = set()
    for rr in records:
        # A SRV record has target and port, URI just has target.
        target = (rr.target, getattr(rr, "port", None))
        if target not in seen:
            uniquerecords.append(rr)
            seen.add(target)

    # weighted randomization of entries with same priority
    result = []
    sameprio = []
    for rr in uniquerecords:
        # add all items with same priority in a bucket
        if not sameprio or sameprio[0].priority == rr.priority:
            sameprio.append(rr)
        else:
            # got different priority, shuffle bucket
            result.extend(_mix_weight(sameprio))
            # start a new priority list
            sameprio = [rr]
    # add last batch of records with same priority
    if sameprio:
        result.extend(_mix_weight(sameprio))
    return result


def query_srv(qname, resolver=None, **kwargs):
    """Query SRV records and sort reply according to RFC 2782

    :param qname: query name, _service._proto.domain.
    :return: list of dns.rdtypes.IN.SRV.SRV instances
    """
    if resolver is None:
        resolve_f = resolve
    else:
        resolve_f = getattr(resolver, "resolve", resolver.query)
    answer = resolve_f(qname, rdtype=dns.rdatatype.SRV, **kwargs)
    return sort_prio_weight(answer)
