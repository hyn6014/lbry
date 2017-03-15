#!/usr/bin/env python

import os
from lbrynet import __version__
from setuptools import setup, find_packages

# TODO: find a way to keep this in sync with requirements.txt
#
# Note though that this list is intentionally less restrictive than
# requirements.txt. This is only the libraries that are direct
# dependencies of the lbrynet library.  requirements.txt includes
# dependencies of dependencies and specific versions that we know
# all work together.
# See https://packaging.python.org/requirements/ for more details.
requires = [
    'Twisted',
    'appdirs',
    'base58',
    'envparse',
    'jsonrpc',
    'jsonschema',
    'lbryum>=2.7.6',
    'miniupnpc',
    'pycrypto',
    'pyyaml',
    'requests',
    'requests_futures',
    'seccure',
    'simplejson',
    'txJSON-RPC',
    'zope.interface',
]

console_scripts = [
    'lbrynet-daemon = lbrynet.lbrynet_daemon.DaemonControl:start',
    'stop-lbrynet-daemon = lbrynet.lbrynet_daemon.DaemonControl:stop',
    'lbrynet-cli = lbrynet.lbrynet_daemon.DaemonCLI:main'
]


def package_files(directory):
    for path, _, filenames in os.walk(directory):
        for filename in filenames:
            yield os.path.join('..', path, filename)


package_name = "lbrynet"
base_dir = os.path.abspath(os.path.dirname(__file__))
# Get the long description from the README file
with open(os.path.join(base_dir, 'README.md')) as f:
    long_description = f.read().decode('utf-8')

setup(
    name=package_name,
    version=__version__,
    author="LBRY Inc.",
    author_email="hello@lbry.io",
    url="https://lbry.io",
    description="A decentralized media library and marketplace",
    long_description=long_description,
    keywords="lbry protocol media",
    license='MIT',

    packages=find_packages(base_dir, exclude=['tests']),
    install_requires=requires,
    entry_points={'console_scripts': console_scripts},
    package_data={
        package_name: list(package_files('lbrynet/resources/ui'))
    },
    zip_safe=False,
)