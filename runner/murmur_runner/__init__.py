"""Murmur runner: drives Locust swarms over a Murmur load graph."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("murmur-runner")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "unknown"
