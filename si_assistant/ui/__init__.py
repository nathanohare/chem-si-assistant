"""Qt (PySide6) user interface."""

from ..compile.qt_images import allow_large_images as _allow_large_images

# Pictures pasted at 1200 dpi can be bigger than Qt opens by default (SPEC §13k.6).
_allow_large_images()
