#!/usr/bin/env python3

import os
from setuptools import setup, find_packages

setup(
    name="gpwrapper",
    version="0.0.1",
    description="A Scikit-Learn-style wrapper around GPyTorch",
    author="Geoff Pleiss, Jake Gardner, Sirui Li",
    url="https://gpytorch.ai",
    author_email="gpleiss@gmail.com, jrg365@cornell.edu, sirui.li@wustl.edu",
    project_urls={
        "Documentation": "https://gpytorch.readthedocs.io",
        "Source": "https://github.com/cornellius-gp/gpytorch/",
    },
    license="MIT",
    classifiers=["Development Status :: 2 - Pre-Alpha", "Programming Language :: Python :: 3"],
    packages=find_packages(),
    python_requires=">=3.6",
    install_requires=[
        "torch>=1.0.0",
        "gpytorch>=0.1.1",
        "skorch>=0.5.0",
        "scipy>=1.1.0",
        "scikit-learn>=0.19.1",
    ],
    extras_require={
    },
)
