""" Module specifically to hold algorithm version information.  The reason this
exists is the version information is needed in both setup.py for install and
also in ccd/__init__.py when generating results.  If these values were
defined in ccd/__init__.py then install would fail because there are other
dependencies imported in ccd/__init__.py that are not present until after
install. Do not import anything into this module."""
__name = 'lcmap-pyccd'

# While we sometimes may need to change the code, this may not actually change
# the core algorithm. So, the core algorithm needs it's own version
# that actually gets reported with results, and a release version for pypi
# and system integration purposes.
__algorithm_version__ = '2018.10.17'
__local_version__ = ''

# __algorithm__ = ':'.join([__name, __algorithm_version__, __local_version__])
__algorithm__ = ':'.join([__name, __algorithm_version__])
__version__ = __algorithm_version__
# __version__ = '.'.join([__algorithm_version__, __local_version__])
