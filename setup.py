#!/usr/bin/env python

import os
from setuptools import setup, find_packages
from pip.req import parse_requirements

setup_dir = os.path.dirname(os.path.realpath(__file__))
path_req = os.path.join(setup_dir, 'requirements.txt')
install_reqs = parse_requirements(path_req, session=False)

reqs = [str(ir.req) for ir in install_reqs]

setup(name='aiopogo',
      author = 'David Christenson',
      author_email='mail@noctem.xyz',
      description = 'Asynchronous Pokemon API lib',
      version = '1.2',
      url = 'https://github.com/Noctem/aiopogo',
      packages = find_packages(),
      install_requires = reqs,
      package_data={'aiopogo': ['lib/*.so', 'lib/*.dylib', 'lib/*.dll']},
      classifiers=[
          'Development Status :: 5 - Production/Stable',
          'Intended Audience :: Developers',
          'Operating System :: OS Independent',
          'Programming Language :: Python :: 3',
          'Programming Language :: Python :: 3.5',
          'Programming Language :: Python :: 3.6',
          'License :: OSI Approved :: MIT License'
      ]
)
