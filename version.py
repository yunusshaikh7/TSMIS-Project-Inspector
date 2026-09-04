"""Single source of truth for the app identity and version.

Imported by the build tooling, the updater and the GUI About box. Keep this
file dependency-free so it can be imported from anywhere, including the .spec.
"""

__version__ = "0.1.0"                    # semantic version MAJOR.MINOR.PATCH
APP_NAME = "TSMIS Branch Identifier"     # onefolder / executable name
