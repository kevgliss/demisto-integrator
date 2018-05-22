"""
demisto-content-sync
====================
:license: Apache, see LICENSE for more details.
"""
import pip

import io
from distutils.core import setup

from setuptools import find_packages

if tuple(map(int, pip.__version__.split('.'))) >= (10, 0, 0):
    from pip._internal.download import PipSession
    from pip._internal.req import parse_requirements
else:
    from pip.download import PipSession
    from pip.req import parse_requirements


with io.open('README.md', encoding='utf-8') as readme:
    long_description = readme.read()


# Populates __version__ without importing the package
__version__ = None
with io.open('demisto_integrator/_version.py', encoding='utf-8') as ver_file:
    exec(ver_file.read())  # nosec: config file safe
if not __version__:
    print('Could not find __version__ from demisto_integrator/_version.py')
    exit(1)


setup(
    name='demisto-integrator',
    version=__version__,
    author='Kevin Glisson',
    author_email='kevgliss@gmail.com',
    url='https://github.com/kevgliss/demisto-integrator',
    description='Helps combine custom content and demisto content.',
    long_description=long_description,
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'click>=6.7',
        'click-log>=0.2.1',
        'dulwich>=0.19.2'
    ],
    entry_points={
      'console_scripts': [
          'integrator = demisto_integrator.cli:entry_point'
      ]
    },
    classifiers=[
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'Operating System :: OS Independent',
        'Topic :: Software Development',
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.6",
        "Natural Language :: English",
        "License :: OSI Approved :: Apache Software License"
    ],
    python_requires='>=3.6'
)