#!/usr/bin/env python

from setuptools import setup, find_packages
from aiopogo import __title__, __version__, __author__

setup(name=__title__,
      author=__author__,
      author_email='mail@noctem.xyz',
      description='Asynchronous Pokemon API lib',
      version=__version__,
      url='https://github.com/Noctem/aiopogo',
      packages=find_packages(),
      install_requires=[
          'protobuf>=3.0.0',
          'gpsoauth>=0.4.0',
          'protobuf3-to-dict>=0.1.4',
          'aiohttp==1.3.*',
          'pycrypt>=0.1.1',
          'pogeo>=0.2.0'],
      extras_require={'ujson': ['ujson']},
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
