#!/usr/bin/env python

from setuptools import setup, find_packages

setup(name='aiopogo',
      author = 'David Christenson',
      author_email='mail@noctem.xyz',
      description = 'Asynchronous Pokemon API lib',
      version = '1.3.5',
      url = 'https://github.com/Noctem/aiopogo',
      packages = find_packages(),
      install_requires = [
          'protobuf>=3.0.0',
          'gpsoauth>=0.4.0',
          'protobuf3-to-dict>=0.1.4',
          'aiohttp==1.3.*',
          'pycrypt>=0.1.1',
          'pogeo>=0.2.0'],
      extras_require={'ujson': ['ujson']},
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
