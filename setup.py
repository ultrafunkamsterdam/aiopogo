#!/usr/bin/env python

from setuptools import setup, find_packages

setup(name='aiopogo',
      author='David Christenson',
      author_email='mail@noctem.xyz',
      description='Asynchronous Pokemon API lib',
      version='2.0',
      url='https://github.com/Noctem/aiopogo',
      packages=find_packages(),
      install_requires=[
          'protobuf>=3.0.0',
          'gpsoauth>=0.4.0',
          'aiohttp>=2.0.7,<2.1',
          'pycrypt>=0.7.0',
          'cyrandom>=0.1.2'],
      extras_require={'ujson': ['ujson'], 'socks': ['aiosocks>=0.2.2']},
      license='MIT',
      classifiers=[
          'Development Status :: 5 - Production/Stable',
          'Intended Audience :: Developers',
          'Operating System :: OS Independent',
          'Programming Language :: Python :: 3',
          'Programming Language :: Python :: 3 :: Only',
          'Programming Language :: Python :: 3.5',
          'Programming Language :: Python :: 3.6',
          'License :: OSI Approved :: MIT License'
      ]
)
